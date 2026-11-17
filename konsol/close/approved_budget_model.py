"""Approved budget state, pure: what the Consolidated Statement's Budget
columns may read for a (group, fiscal year), and the one sentence that says
why when they may not (konsol#348 S5 K3; BUD-4-2, Deepak Pai, 7 Nov 2026:
"Numbers shows a visible 'no approved budget' state, never a blank column").

Imports nothing from frappe or konsol. Loaded by path in
konsol/tests/test_approved_budget_model.py. The reader
(statement_api._approved_budget, S5 K4) finds the rows and calls
``budget_state``; this module decides nothing about where they come from.

The five states, judged in this order, the first that holds wins:

1. ``no_approved_budget``: no Approved Budget with docstatus 1 for the
   (group, year). A draft is named: it is not approved until submitted.
2. ``cycle_cancelled``: the Cycle the declaration names is not approved any
   more (cancelled, a draft again through an amend, or gone). Budget Cycle's
   before_cancel refuses the cancel while a declaration names it (S5-Q5 a,
   K16); this state is the backstop the reader shows anyway.
3. ``inputs_changed``: the Cycle's input fingerprint, recomputed at this read
   (#348 S5-Q2 a), no longer matches the one stamped at Approve, or the
   declaration's copy of it is not the Cycle's, or the declaration carries no
   copy at all (a submitted row with a blank ``input_fingerprint`` cannot be
   checked, so it is never ok; PR #554 review m2). The approved figures are
   not known to be the ones in the warehouse, so nothing is read (BUD-11-3:
   reported, and the approver decides what follows).
4. ``layer_missing``: the layer policy names no existing Budget Layer (One
   layer whose layer is gone; All layers on a site with none).
5. ``ok``: ``layers`` holds the layer codes to read, in sort order.

Every state has a sentence (``SENTENCES``) naming the group, the year, the
Cycle where there is one, and what to do; ``sentence`` raises on a state
without one, so no state is ever shown blank.
"""

OK = "ok"
NO_APPROVED_BUDGET = "no_approved_budget"
CYCLE_CANCELLED = "cycle_cancelled"
INPUTS_CHANGED = "inputs_changed"
LAYER_MISSING = "layer_missing"
STATES = (OK, NO_APPROVED_BUDGET, CYCLE_CANCELLED, INPUTS_CHANGED, LAYER_MISSING)

#: Approved Budget ``layer_policy`` options (approved_budget.json).
ALL_LAYERS = "All layers"
ONE_LAYER = "One layer"

#: One sentence per state. ``{group}`` is the group's code, ``{fiscal_year}``
#: the year, ``{declaration}`` the Approved Budget's name, ``{cycle}`` the
#: Budget Cycle's name; ``{cycle_why}``, ``{inputs_why}``, ``{layers}``,
#: ``{layer_why}`` and ``{approved}`` are built by ``budget_state``.
SENTENCES = {
    OK: ("Approved budget for {group} FY{fiscal_year}: Budget Cycle {cycle}, {layers}, Rate Set {rate_set} "
         "(Approved Budget {declaration}{approved})."),
    NO_APPROVED_BUDGET: ("No approved budget for {group} FY{fiscal_year}: declare one in Approved Budget (the "
                         "Budget Cycle and the layer policy) and submit it.{draft}"),
    CYCLE_CANCELLED: ("Approved Budget {declaration} for {group} FY{fiscal_year} names Budget Cycle {cycle}, "
                      "which {cycle_why}: cancel the declaration and amend it to name the approved Cycle."),
    INPUTS_CHANGED: "Approved Budget {declaration} for {group} FY{fiscal_year} {inputs_why}",
    LAYER_MISSING: ("Approved Budget {declaration} for {group} FY{fiscal_year} reads {layer_why}: define the "
                    "layer in Budget Layer, or amend the declaration."),
}

#: The two reasons for ``inputs_changed`` (``{inputs_why}``).
_INPUTS_CHANGED_WHY = ("names Budget Cycle {cycle}, whose inputs have changed since it was approved, so the "
                       "warehouse no longer holds the approved figures and none are shown. Review the Cycle; to "
                       "approve the changed inputs, cancel and amend the Cycle and the declaration.")
_NO_FINGERPRINT_WHY = ("carries no input fingerprint, so the figures it approved cannot be checked against "
                       "Budget Cycle {cycle} and none are shown. Cancel the declaration and amend it, so that "
                       "Approve stamps the Cycle's fingerprint.")


def sentence(state, **context):
    """The state's sentence, filled from ``context``. An unknown state raises
    ``ValueError`` naming it: a state is never shown without its sentence."""
    template = SENTENCES.get(state)
    if template is None:
        raise ValueError(f"{state!r} is not an approved-budget state with a sentence; one of {STATES}")
    return template.format(**context)


def layers_for(declaration, layer_rows):
    """The Budget Layer codes the declaration reads, in ``sort_order`` (then
    code) order. ``layer_rows`` are ``{"layer_code", "sort_order"}`` of every
    Budget Layer on the site. All layers: every row; One layer: the named
    layer when it is among the rows, else ``[]`` (``budget_state`` reads an
    empty list as ``layer_missing``). An unknown policy raises ``ValueError``
    naming it: the Select has two options, and a third is a setup fault."""
    policy = declaration.get("layer_policy")
    codes = [row["layer_code"] for row in sorted(
        layer_rows, key=lambda r: (r.get("sort_order") if r.get("sort_order") is not None else 0, r["layer_code"]))]
    if policy == ALL_LAYERS:
        return codes
    if policy == ONE_LAYER:
        layer = declaration.get("layer")
        return [layer] if layer and layer in codes else []
    raise ValueError(f"Approved Budget {declaration.get('name')}: layer_policy {policy!r} is not one of "
                     f"({ALL_LAYERS!r}, {ONE_LAYER!r})")


def _layers_text(declaration, layers):
    if declaration.get("layer_policy") == ONE_LAYER:
        return f"layer {layers[0]}"
    return "all layers (" + ", ".join(layers) + ")"


def _date_text(value):
    """``YYYY-MM-DD`` of a date, datetime or string; '' for nothing."""
    if value is None or value == "":
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]


def budget_state(group, fiscal_year, declaration, cycle, fingerprint, layer_rows):
    """``{"state", "message", "layers"}`` for the (group, year).

    - ``group``: the root group's code, ``fiscal_year`` its year (for the
      sentence; the reader resolved them).
    - ``declaration``: the live Approved Budget row (``name, docstatus,
      budget_cycle, rate_set, layer_policy, layer, input_fingerprint, owner,
      modified``), or None.
    - ``cycle``: the named Budget Cycle's row (``name, docstatus,
      input_fingerprint``), or None when it is gone.
    - ``fingerprint``: ``budget_fingerprint.check_input_fingerprint``'s
      result for the Cycle (``matches`` True / False / None, ``stored``).
      Required once the Cycle is approved (S5-Q2 a: checked at every read);
      None there raises ``ValueError``, never passes as ok.
    - ``layer_rows``: every Budget Layer, as ``layers_for`` takes them.

    ``layers`` is the list to read when the state is ``ok``, else None.
    """
    ctx = {"group": group, "fiscal_year": fiscal_year}

    if declaration is None or int(declaration.get("docstatus") or 0) != 1:
        draft = ""
        if declaration is not None and int(declaration.get("docstatus") or 0) == 0:
            draft = (f" Approved Budget {declaration.get('name')} is a draft: it declares nothing until it is "
                     "submitted.")
        return {"state": NO_APPROVED_BUDGET, "message": sentence(NO_APPROVED_BUDGET, draft=draft, **ctx),
                "layers": None}

    ctx["declaration"] = declaration.get("name")
    ctx["cycle"] = declaration.get("budget_cycle")

    cycle_docstatus = None if cycle is None else int(cycle.get("docstatus") or 0)
    if cycle_docstatus != 1:
        if cycle is None:
            why = "no longer exists"
        elif cycle_docstatus == 2:
            why = "is cancelled"
        else:
            why = "is not approved (a draft)"
        return {"state": CYCLE_CANCELLED, "message": sentence(CYCLE_CANCELLED, cycle_why=why, **ctx),
                "layers": None}

    if fingerprint is None:
        raise ValueError(f"Budget Cycle {ctx['cycle']} is approved but no input fingerprint check was given; the "
                         "check runs at every read (#348 S5-Q2 a)")
    stamped = declaration.get("input_fingerprint")
    stored = fingerprint.get("stored")
    if not stamped:
        # PR #554 review m2: a submitted declaration with no copy of the
        # Cycle's fingerprint cannot be checked, so it is never ok.
        why = _NO_FINGERPRINT_WHY.format(**ctx)
        return {"state": INPUTS_CHANGED, "message": sentence(INPUTS_CHANGED, inputs_why=why, **ctx), "layers": None}
    if fingerprint.get("matches") is not True or (stored and stamped != stored):
        why = _INPUTS_CHANGED_WHY.format(**ctx)
        return {"state": INPUTS_CHANGED, "message": sentence(INPUTS_CHANGED, inputs_why=why, **ctx), "layers": None}

    layers = layers_for(declaration, layer_rows)
    if not layers:
        if declaration.get("layer_policy") == ONE_LAYER:
            why = f"layer {declaration.get('layer')}, which is no Budget Layer"
        else:
            why = "all layers, but no Budget Layer is defined"
        return {"state": LAYER_MISSING, "message": sentence(LAYER_MISSING, layer_why=why, **ctx), "layers": None}

    approved = ""
    owner, when = declaration.get("owner"), _date_text(declaration.get("modified"))
    if owner or when:
        approved = ", approved" + (f" by {owner}" if owner else "") + (f" on {when}" if when else "")
    return {
        "state": OK,
        "message": sentence(OK, layers=_layers_text(declaration, layers), rate_set=declaration.get("rate_set"),
                            approved=approved, **ctx),
        "layers": layers,
    }
