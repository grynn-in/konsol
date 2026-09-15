"""Record the acquisitions and disposals already on file as deal documents (konsolidat#198).

Before Business Combination and Business Disposal existed, an acquisition was
a handful of figures on the Ownership Period it started (``acquisition_date``,
``acquisition_price``, ``fair_value_adjustment``) and a disposal two flags on
the period it ended (``is_disposal``, ``disposal_date``, ``disposal_price``).
Those seven fields are now read-only: a deal document fills them (design 2a).
So the figures already there must become deal documents, or the stack's
history is stuck in fields nobody can edit.

For every SUBMITTED Ownership Period:

* ``acquisition_price > 0`` → one **Draft** Business Combination: the group,
  the entity, the acquisition date (the period's own, else its effective
  date), the share = ``ownership_pct``, the root's reporting currency, one
  Cash consideration line for the price, linked to the period. A fair value
  adjustment goes into the description ("to allocate"): without a balance
  line it has no account, and inventing one would be a policy choice.
* ``is_disposal = 1`` → one **Draft** Business Disposal with one Cash
  proceeds line for ``disposal_price`` (0 is real: design decision 5).

Nothing is submitted: a migration cannot approve. A period a deal document
already links (any docstatus) is skipped, so a second run inserts nothing.
The controller's ``validate`` may refuse a migrated Draft — no Consolidation
Policy on the root yet, no Closing rate, no declared period; the migration
can supply none of these — so a refused Draft is inserted without validate
and carries the refusal in its description for the reviewer. Only a
``frappe.ValidationError`` (the controller's judgement) takes that route:
any other error on the insert — a duplicate name, a database error — is not
a refusal, retrying would raise it again, so the period is listed as not
migrated and the migrate goes on. A period with no root node, no entity, or
(for a disposal) no date cannot become a document and is listed the same way.

patches.txt has no sections, so this runs pre_model_sync: the doctypes are
reloaded before anything reads them — Consolidation Group first, because the
deal controllers' ``validate`` selects the root's policy columns (design 1a),
which exist only once that doctype is reloaded; then the deal doctypes,
children first; Ownership Period last. Counts are printed.
"""
import frappe
from frappe.utils import strip_html

#: (module, doctype folder) in reload order: Consolidation Group first (its
#: policy columns are what validate reads), children before their parents,
#: Ownership Period last (its deal fields are what this reads).
RELOAD = (
    ("consolidation", "consolidation_group"),
    ("consolidation", "business_combination_consideration"),
    ("consolidation", "business_combination_acquired_balance"),
    ("consolidation", "business_combination_cost"),
    ("consolidation", "business_combination"),
    ("consolidation", "business_disposal_proceeds"),
    ("consolidation", "business_disposal"),
    ("consolidation", "ownership_period"),
)

PERIOD_FIELDS = (
    "name", "consolidation_group", "data_area_id", "effective_date", "end_date",
    "ownership_pct", "acquisition_date", "acquisition_price", "fair_value_adjustment",
    "is_disposal", "disposal_date", "disposal_price",
)

#: A blank Link is stored as NULL: the root is the node with no entity.
_BLANK = ["is", "not set"]


def execute():
    for module, name in RELOAD:
        frappe.reload_doc(module, "doctype", name)

    periods = frappe.get_all(
        "Ownership Period",
        filters={"docstatus": 1},
        fields=list(PERIOD_FIELDS),
        order_by="effective_date asc, name asc",
        limit_page_length=0,
    )

    counts = {"Business Combination": 0, "Business Disposal": 0, "skipped": 0, "kept_with_reason": 0}
    unconvertible = []
    for period in periods:
        period = _Row(period)
        if float(period.acquisition_price or 0) > 0:
            _migrate(period, "Business Combination", _combination, counts, unconvertible)
        if int(period.is_disposal or 0):
            _migrate(period, "Business Disposal", _disposal, counts, unconvertible)

    print(
        "migrate_deals_to_business_combinations: %d Business Combination(s) and "
        "%d Business Disposal(s) inserted as Draft, %d skipped (already linked), "
        "%d kept with the controller's refusal to review"
        % (counts["Business Combination"], counts["Business Disposal"],
           counts["skipped"], counts["kept_with_reason"])
    )
    for name, why in unconvertible:
        print("  not migrated: Ownership Period %s: %s" % (name, why))


def _migrate(period, doctype, build, counts, unconvertible):
    if frappe.db.exists(doctype, {"ownership_period": period.name}):
        counts["skipped"] += 1
        return
    data, why = build(period)
    if why:
        unconvertible.append((period.name, why))
        return
    try:
        kept = _insert(data)
    except _NotMigrated as e:
        unconvertible.append((period.name, str(e)))
        return
    if kept:
        counts["kept_with_reason"] += 1
    counts[doctype] += 1


class _NotMigrated(Exception):
    """The insert failed for a reason that is not the controller's refusal
    (a duplicate name, a database error): the period is listed, not retried."""


def _insert(data):
    """Insert the Draft; when the controller refuses it (a ValidationError),
    insert it anyway without validate, the refusal appended to the
    description. Returns True when the refusal route was taken. A refused
    ``insert()`` writes nothing (validate runs before the row), so the retry
    is a fresh document. Any other error — on the first insert or on the
    retry — raises ``_NotMigrated`` so the migrate goes on with the next
    period instead of aborting."""
    doc = frappe.get_doc(data)
    doc.flags.ignore_permissions = True
    try:
        doc.insert()
        return False
    except frappe.ValidationError as e:  # the controller's judgement: keep with the reason
        reason = _plain(e)
    except Exception as e:  # noqa: BLE001 — not a refusal; retrying would raise it again
        raise _NotMigrated(_plain(e)) from e
    if hasattr(frappe, "clear_last_message"):
        frappe.clear_last_message()
    data = dict(data)
    data["description"] = "%s\nNot yet valid: %s" % (data["description"], reason)
    doc = frappe.get_doc(data)
    doc.flags.ignore_permissions = True
    doc.flags.ignore_validate = True
    try:
        doc.insert()
    except Exception as e:  # noqa: BLE001 — listed with both reasons, the migrate goes on
        raise _NotMigrated("%s (after the controller refused: %s)" % (_plain(e), reason)) from e
    return True


def _root_currency(period):
    return frappe.db.get_value(
        "Consolidation Group",
        {"consolidation_group": period.consolidation_group, "data_area_id": _BLANK},
        "reporting_currency",
    )


def _combination(period):
    """The Draft Business Combination for an acquisition, or (None, why)."""
    if not period.data_area_id:
        return None, "no entity: a Business Combination acquires an entity"
    currency = _root_currency(period)
    if not currency:
        return None, ("no Consolidation Group node '%s' without an entity: the root carries the "
                      "reporting currency" % period.consolidation_group)
    description = "Migrated from Ownership Period %s; review and submit" % period.name
    fva = float(period.fair_value_adjustment or 0)
    if fva:
        description += ". Fair value adjustment %.2f to allocate to Acquired Balance Sheet lines" % fva
    return {
        "doctype": "Business Combination",
        "consolidation_group": period.consolidation_group,
        "acquired_entity": period.data_area_id,
        "acquisition_date": period.acquisition_date or period.effective_date,
        "share_acquired_pct": period.ownership_pct,
        "consideration_currency": currency,
        "ownership_period": period.name,
        "consideration": [{
            "component": "Cash",
            "amount": float(period.acquisition_price),
            "currency": currency,
            "description": "Acquisition price from Ownership Period %s" % period.name,
        }],
        "description": description,
    }, None


def _disposal(period):
    """The Draft Business Disposal for a disposal, or (None, why)."""
    if not period.data_area_id:
        return None, "no entity: a Business Disposal disposes of an entity"
    date = period.disposal_date or period.end_date
    if not date:
        return None, "is_disposal is set but neither disposal_date nor end_date says when"
    currency = _root_currency(period)
    if not currency:
        return None, ("no Consolidation Group node '%s' without an entity: the root carries the "
                      "reporting currency" % period.consolidation_group)
    return {
        "doctype": "Business Disposal",
        "consolidation_group": period.consolidation_group,
        "disposed_entity": period.data_area_id,
        "disposal_date": date,
        "share_disposed_pct": period.ownership_pct,
        "retained_interest_pct": 0,
        "proceeds_currency": currency,
        "ownership_period": period.name,
        "proceeds": [{
            "component": "Cash",
            "amount": float(period.disposal_price or 0),
            "currency": currency,
            "description": "Disposal price from Ownership Period %s" % period.name,
        }],
        "description": "Migrated from Ownership Period %s; review and submit" % period.name,
    }, None


def _plain(exc):
    """The error as one line of text for the migrate output. frappe.throw's
    message is HTML-ish: ``<br>`` becomes "; ", other tags go. A database
    error from pymysql carries ``args == (errno, message)`` and prints as
    that tuple — ``(1062, "Duplicate entry …")`` — so the message alone is
    taken. An error with no text is named by its type."""
    args = exc.args
    if len(args) == 2 and isinstance(args[0], int) and isinstance(args[1], str):
        text = args[1]
    else:
        text = str(exc)
    text = strip_html(text.replace("<br>", "; "))
    return " ".join(text.split()) or type(exc).__name__


class _Row(dict):
    """A get_all row with attribute access, whatever dict type it came as."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)
