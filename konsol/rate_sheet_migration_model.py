"""Group Exchange Rate to Rate Sheet: the pure migration planner (konsol#592
pair 1, task T5).

Decided by Deepak Pai on 22 Nov 2026 (#592): actuals rates are held on one
**Statutory** sheet per (fiscal year, fiscal period), with an Average and a
Closing quote per currency pair on one row (D1). Budget and forecast
translate at one **Constant** sheet per site and year, with one rate per pair
(D2, QA-1, QB-1). There is no Disabled flag (P1-Q3 A).

``plan`` reads plain dicts of Group Exchange Rate rows and returns what the
patch creates. It imports nothing from frappe or konsol; the patch
(``migrate_group_exchange_rates_to_rate_sheets``) reads the rows, calls
``plan``, stops on any refusal (``assert_no_refusals``) and inserts the
sheets.

Statutory sheets, from the rows of the actuals Rate Set:

* A pair is its Closing and its Average rate. A pair missing one of them is
  refused, naming the key: nothing is assumed for the other type.
* Quoted Per: when Closing and Average quote per a different unit, both are
  rescaled exactly, in Decimal, to the larger unit, so the true rate
  (quote / quoted_per) is unchanged.
* **Corrections supersede** (#312-3, Deepak Pai 9 Oct 2026; #312-S3-2). A GER
  correction names the approved rate it replaced in ``supersedes`` (or, for
  an older amendment, in ``amended_from``), and its approval cancelled that
  rate. A period's history becomes a chain of sheets: ``RS-S-<FY>-<PP>``,
  then ``-2``, ``-3``. Every sheet but the last is cancelled, and each later
  sheet names the one before in ``supersedes`` (not ``amended_from``).
* **The chain-grouping rule.** A supersede event is the cancellation of
  superseded rates, timed by the superseded rate's ``modified`` (approval of
  the correction cancels it in the same transaction, so that is the moment
  the correction took effect). Rates cancelled at the same instant are one
  event, and each event is one sheet version. Versions are rebuilt
  backwards from the approved state: the last sheet holds the approved
  rates; undoing the latest event (putting back the rates it cancelled)
  gives the sheet before it, and so on. So one correction of one pair makes
  a new version that holds every pair as it stood after it. Accepted
  trade-off: a pair a correction did not touch carries its current rate into
  every earlier version, because GER keeps no approval time to say whether
  that pair existed yet.
* Drafts: in a period with no approved rate, the drafts become a Draft sheet.
  A draft that is a pending correction (it names ``supersedes``), and a plain
  draft in a period that has approved rates, are dropped and named.
* A cancelled rate that no rate replaced, and a correction chain that ends in
  a cancelled rate, are dropped and named: they left the warehouse already.
* A Closing-type period cannot be entered on a new sheet, but one that has
  rates is migrated for audit, with a warning.
* A period the calendar does not declare is refused: a sheet's dates come
  from the calendar (P1-Q4 A).

Constant sheets, from the rows of a non-actuals Rate Set that an approved
Budget Cycle names:

* The year's set is the one its approved Budget Cycles name. Two approved
  Cycles of one year on different sets are refused, unless the approved
  Approved Budgets of that year pick exactly one of them.
* Every approved rate of the set in that year, both types and every period,
  is read. If each pair's true rate is the same throughout, the sheet is
  approved with that rate. Otherwise the sheet is a Draft: a uniform pair
  carries its rate, a non-uniform pair is left blank for a person to enter
  (nothing is picked for them), and a warning names the pairs and values.
* Rows of a set no approved Cycle uses, of a year no approved Cycle uses the
  set for, and drafts or cancelled rows of a budget set, are dropped and
  named.
"""
from collections import defaultdict
from decimal import Decimal

STATUTORY = "Statutory"
CONSTANT = "Constant"
MIGRATED = "Migrated"
RATE_TYPES = ("Closing", "Average")
CLOSING_PERIOD = "Closing"

#: The GER fields copied onto each sheet's audit rows (the audit CSV).
AUDIT_FIELDS = ("name", "rate_set", "from_currency", "to_currency", "rate_type", "fiscal_year",
                "fiscal_period", "quote", "quoted_per", "docstatus", "supersedes", "amended_from",
                "change_reason", "source", "owner", "creation", "modified")


class MigrationRefused(Exception):
    """The plan has refusals: the migrate stops and changes nothing."""


def statutory_name(fiscal_year, fiscal_period, version=1):
    """``RS-S-<FY>-<PP>``; a later version of the same period gets ``-<n>``."""
    base = f"RS-S-{int(fiscal_year)}-{int(fiscal_period):02d}"
    return base if version == 1 else f"{base}-{version}"


def constant_name(fiscal_year):
    return f"RS-C-{int(fiscal_year)}"


def _dec(value):
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value if value is not None else 0))


def rescale(quote, quoted_per, to_per):
    """The quote per ``to_per`` units that keeps quote / quoted_per exactly.

    ``to_per`` must be a whole multiple of ``quoted_per`` (Quoted Per is 1,
    10, 100, 1,000 or 10,000, and a quote is only ever scaled up)."""
    per, to_per = int(quoted_per or 1), int(to_per or 1)
    if to_per % per:
        raise ValueError(f"Quoted Per {per} does not rescale to {to_per}")
    return _dec(quote) * (to_per // per)


def true_rate(quote, quoted_per):
    return _dec(quote) / Decimal(int(quoted_per or 1))


def assert_no_refusals(plan):
    if plan["refusals"]:
        raise MigrationRefused(
            "Group Exchange Rate cannot be migrated to Rate Sheets:\n- " + "\n- ".join(plan["refusals"]))


def _label(r):
    return (f"{r.get('from_currency')} → {r.get('to_currency')} {r.get('rate_type')} "
            f"FY{r.get('fiscal_year')} P{r.get('fiscal_period')}")


def _pair(r):
    return (r["from_currency"], r["to_currency"])


def _key(r):
    return (r["from_currency"], r["to_currency"], r["rate_type"])


def _period_types(calendar):
    out = {}
    for year, rows in (calendar or {}).items():
        for p in rows or ():
            out[(int(year), int(p["period"]))] = p.get("type")
    return out


def _audit(rows):
    return [{f: r.get(f) for f in AUDIT_FIELDS} for r in sorted(rows, key=lambda r: r["name"])]


class _Plan:
    def __init__(self):
        self.sheets, self.dropped, self.refusals, self.warnings = [], [], [], []

    def drop(self, r, reason):
        self.dropped.append({"document": r["name"], "reason": reason})

    def refuse(self, message):
        if message not in self.refusals:
            self.refusals.append(message)

    def result(self):
        return {"sheets": self.sheets,
                "dropped": sorted(self.dropped, key=lambda d: d["document"]),
                "refusals": self.refusals, "warnings": self.warnings}


def plan(rates, actuals_set, budget_cycles=(), calendar=None, approved_budgets=()):
    """The Rate Sheets to create from Group Exchange Rate rows.

    ``rates``: GER rows as dicts (see AUDIT_FIELDS). ``actuals_set``: the Rate
    Set flagged Is Actuals. ``budget_cycles``: rows with name, fiscal_year,
    rate_set, docstatus. ``calendar``: {year: [{"period": n, "type": t}]}.
    ``approved_budgets``: rows with name, fiscal_year, budget_cycle,
    rate_set, docstatus.

    Returns {"sheets", "dropped", "refusals", "warnings"}. Each sheet has
    name, rate_basis, fiscal_year, fiscal_period (None for Constant),
    period_type, docstatus, supersedes, change_reason, rows and audit_rows. A
    Statutory row has from_currency, to_currency, quoted_per, average_quote,
    closing_quote, source and legacy_documents; a Constant row has rate in
    place of the two quotes. Quotes are Decimal.
    """
    out = _Plan()
    types = _period_types(calendar)
    by_set = defaultdict(list)
    for r in rates:
        if not r.get("rate_set"):
            out.refuse(f"{r['name']} ({_label(r)}) names no Rate Set: it cannot be placed on a sheet.")
            continue
        by_set[r["rate_set"]].append(r)

    _statutory(out, by_set.get(actuals_set, []), types)
    _constant(out, by_set, actuals_set, budget_cycles, approved_budgets)
    out.sheets.sort(key=lambda s: (s["rate_basis"] != STATUTORY, s["fiscal_year"],
                                   s["fiscal_period"] or 0, s.get("_version", 1)))
    for s in out.sheets:
        s.pop("_version", None)
    return out.result()


# -- Statutory ----------------------------------------------------------------------

def _statutory(out, rows, types):
    by_period = defaultdict(list)
    for r in rows:
        by_period[(int(r["fiscal_year"]), int(r["fiscal_period"]))].append(r)
    for (fy, fp) in sorted(by_period):
        period_rows = by_period[(fy, fp)]
        if (fy, fp) not in types:
            out.refuse(f"FY{fy} P{fp} is not a declared period of the fiscal calendar, so its sheet has "
                       f"no dates; declare it first ({', '.join(sorted(r['name'] for r in period_rows))}).")
            continue
        _period(out, fy, fp, types[(fy, fp)], period_rows)


def _period(out, fy, fp, ptype, rows):
    approved = [r for r in rows if r["docstatus"] == 1]
    cancelled = [r for r in rows if r["docstatus"] == 2]
    drafts = [r for r in rows if r["docstatus"] == 0]
    for d in drafts:
        if d.get("supersedes"):
            out.drop(d, f"a pending correction of {d['supersedes']}: it was never approved, so the "
                        "approved rate stands; enter it again on a correction sheet")
    plain_drafts = [d for d in drafts if not d.get("supersedes")]

    if approved:
        for d in plain_drafts:
            out.drop(d, f"a draft in FY{fy} P{fp}, which has approved rates: enter it again on a "
                        "correction sheet")
        versions, reasons = _versions(out, fy, fp, approved, cancelled)
        if versions is None:
            return
        last = len(versions)
        for i, state in enumerate(versions, start=1):
            _statutory_sheet(out, fy, fp, ptype, list(state.values()), version=i,
                             docstatus=1 if i == last else 2,
                             supersedes=statutory_name(fy, fp, i - 1) if i > 1 else None,
                             change_reason=reasons[i - 1])
        return

    for c in cancelled:
        out.drop(c, "cancelled, and no approved rate of its key replaced it: it left the warehouse")
    if plain_drafts:
        seen = {}
        for d in plain_drafts:
            if _key(d) in seen:
                out.refuse(f"{_label(d)}: two drafts, {seen[_key(d)]} and {d['name']}; delete one so "
                           "the Draft sheet holds one quote per key.")
            seen.setdefault(_key(d), d["name"])
        _statutory_sheet(out, fy, fp, ptype, plain_drafts, version=1, docstatus=0, supersedes=None,
                         change_reason=None)


def _versions(out, fy, fp, approved, cancelled):
    """The sheet versions of one period, oldest first, as {key: GER row},
    and each version's Reason for Change. (None, None) when refused."""
    current = {}
    for r in approved:
        if _key(r) in current:
            out.refuse(f"{_label(r)}: two approved rates, {current[_key(r)]['name']} and {r['name']}; "
                       "a key has exactly one.")
            return None, None
        current[_key(r)] = r

    cancelled_by_name = {c["name"]: c for c in cancelled}
    # A successor names the cancelled rate it replaced: supersedes first (a
    # correction), else amended_from (an amendment of a cancelled rate).
    pred, succ = {}, {}  # successor name -> cancelled row; cancelled name -> successor name
    for link in ("supersedes", "amended_from"):
        for x in approved + cancelled:
            s = cancelled_by_name.get(x.get(link))
            if s is None or x["name"] in pred or _key(s) != _key(x):
                continue
            if s["name"] in succ:
                if link == "supersedes":
                    out.refuse(f"{_label(s)}: {s['name']} is superseded by both {succ[s['name']]} "
                               f"and {x['name']}.")
                    return None, None
                continue
            pred[x["name"]], succ[s["name"]] = s, x["name"]
    reachable = set()
    for r in approved:
        name = r["name"]
        while name in pred:
            name = pred[name]["name"]
            reachable.add(name)
    by_name = {r["name"]: r for r in approved + cancelled}
    for c in cancelled:
        if c["name"] in reachable:
            continue
        if c["name"] in succ:
            out.drop(c, "a correction chain that ends in a cancelled rate: it left the warehouse")
        else:
            out.drop(c, "cancelled, and no approved rate of its key replaced it: it left the warehouse")

    events = defaultdict(list)
    for name in reachable:
        s = by_name[name]
        events[str(s.get("modified") or "")].append(s)
    state = dict(current)
    versions, reasons = [state], []
    for moment in sorted(events, reverse=True):
        earlier = dict(state)
        why = []
        for s in sorted(events[moment], key=lambda r: r["name"]):
            x = by_name[succ[s["name"]]]
            if state.get(_key(s)) is not x:
                out.refuse(f"{_label(s)}: the history of {s['name']} cannot be ordered (its correction "
                           f"{x['name']} was replaced before {s['name']} was cancelled).")
                return None, None
            earlier[_key(s)] = s
            if (x.get("change_reason") or "").strip():
                why.append(x["change_reason"].strip())
        reasons.insert(0, "; ".join(dict.fromkeys(why)) or None)
        versions.insert(0, earlier)
        state = earlier
    reasons.insert(0, None)
    return versions, reasons


def _statutory_sheet(out, fy, fp, ptype, rates, version, docstatus, supersedes, change_reason):
    name = statutory_name(fy, fp, version)
    pairs = defaultdict(dict)
    for r in rates:
        pairs[_pair(r)][r["rate_type"]] = r
    rows, ok = [], True
    for (frm, to) in sorted(pairs, key=lambda p: (p[1], p[0])):
        by_type = pairs[(frm, to)]
        missing = [t for t in RATE_TYPES if t not in by_type]
        if missing:
            have = ", ".join(f"{t} {by_type[t]['name']}" for t in RATE_TYPES if t in by_type)
            out.refuse(f"{frm} → {to} FY{fy} P{fp}: has {have} but no {' or '.join(missing)} rate; a "
                       "sheet row needs both (enter the missing rate, or cancel by correction).")
            ok = False
            continue
        c, a = by_type["Closing"], by_type["Average"]
        per = max(int(c.get("quoted_per") or 1), int(a.get("quoted_per") or 1))
        rows.append({
            "from_currency": frm, "to_currency": to, "quoted_per": per,
            "average_quote": rescale(a.get("quote"), a.get("quoted_per"), per),
            "closing_quote": rescale(c.get("quote"), c.get("quoted_per"), per),
            "source": MIGRATED, "legacy_documents": sorted([a["name"], c["name"]]),
        })
    if not ok:
        return
    if ptype == CLOSING_PERIOD and version == 1:
        out.warnings.append(f"{name}: FY{fy} P{fp} is a Closing period. Its rates are migrated for audit; "
                            "a new sheet for a Closing period is refused.")
    out.sheets.append({
        "name": name, "rate_basis": STATUTORY, "fiscal_year": fy, "fiscal_period": fp,
        "period_type": ptype, "docstatus": docstatus, "supersedes": supersedes,
        "change_reason": change_reason, "rows": rows, "audit_rows": _audit(rates), "_version": version,
    })


# -- Constant -----------------------------------------------------------------------

def _constant(out, by_set, actuals_set, budget_cycles, approved_budgets):
    cycles = [c for c in budget_cycles or () if c.get("docstatus") == 1 and c.get("rate_set")]
    cycle_set = {c["name"]: c["rate_set"] for c in cycles}
    years = defaultdict(lambda: defaultdict(list))
    for c in cycles:
        years[int(c["fiscal_year"])][c["rate_set"]].append(c["name"])

    chosen, refused_years = {}, set()
    for fy in sorted(years):
        sets = years[fy]
        if len(sets) == 1:
            chosen[fy] = next(iter(sets))
            continue
        picked = {cycle_set.get(b.get("budget_cycle")) or b.get("rate_set")
                  for b in approved_budgets or ()
                  if b.get("docstatus") == 1 and int(b.get("fiscal_year") or 0) == fy
                  and (cycle_set.get(b.get("budget_cycle")) or b.get("rate_set")) in sets}
        if len(picked) == 1:
            chosen[fy] = picked.pop()
            continue
        named = "; ".join(f"{s}: {', '.join(sorted(n))}" for s, n in sorted(sets.items()))
        out.refuse(f"FY{fy}: approved Budget Cycles name different Rate Sets ({named}), and no single "
                   "approved Approved Budget picks one; one Constant sheet serves the year (QB-1).")
        refused_years.add(fy)

    used_sets = {s for sets in years.values() for s in sets}
    for rate_set, rows in sorted(by_set.items()):
        if rate_set == actuals_set:
            continue
        for r in rows:
            fy = int(r["fiscal_year"])
            if fy in refused_years and rate_set in years[fy]:
                continue
            if rate_set not in used_sets:
                out.drop(r, f"Rate Set {rate_set} has no approved Budget Cycle")
            elif chosen.get(fy) != rate_set:
                out.drop(r, f"no approved Budget Cycle uses Rate Set {rate_set} for FY{fy}"
                            + (f" (the year's Constant sheet comes from {chosen[fy]})" if fy in chosen else ""))
            elif r["docstatus"] == 0:
                out.drop(r, "a draft budget rate: a Constant sheet is made from approved rates only")
            elif r["docstatus"] == 2:
                out.drop(r, "a cancelled budget rate: a Constant sheet is made from approved rates only")

    for fy, rate_set in sorted(chosen.items()):
        rows = [r for r in by_set.get(rate_set, []) if int(r["fiscal_year"]) == fy and r["docstatus"] == 1]
        if not rows:
            out.warnings.append(
                f"FY{fy}: Budget Cycle {', '.join(sorted(years[fy][rate_set]))} names Rate Set {rate_set}, "
                f"which has no approved rate in FY{fy}; no Constant sheet is made, so enter {constant_name(fy)}.")
            continue
        _constant_sheet(out, fy, rows)


def _constant_sheet(out, fy, rates):
    name = constant_name(fy)
    pairs = defaultdict(list)
    for r in rates:
        pairs[_pair(r)].append(r)
    rows, uneven = [], []
    for (frm, to) in sorted(pairs, key=lambda p: (p[1], p[0])):
        group = pairs[(frm, to)]
        per = max(int(r.get("quoted_per") or 1) for r in group)
        values = sorted({rescale(r.get("quote"), r.get("quoted_per"), per).normalize() for r in group})
        rate = None
        if len(values) == 1:
            rate = rescale(group[0].get("quote"), group[0].get("quoted_per"), per)
        else:
            uneven.append(f"{frm} → {to} per {per}: {', '.join(str(v) for v in values)}")
        rows.append({"from_currency": frm, "to_currency": to, "quoted_per": per, "rate": rate,
                     "source": MIGRATED, "legacy_documents": sorted(r["name"] for r in group)})
    if uneven:
        out.warnings.append(f"{name} is a Draft: its budget rates are not one rate for the year, so the rate "
                            f"is left blank for a person to enter and approve ({'; '.join(uneven)}).")
    out.sheets.append({
        "name": name, "rate_basis": CONSTANT, "fiscal_year": fy, "fiscal_period": None,
        "period_type": None, "docstatus": 0 if uneven else 1, "supersedes": None, "change_reason": None,
        "rows": rows, "audit_rows": _audit(rates),
    })
