"""Group Exchange Rate becomes Rate Sheet (konsol#592 pair 1, task T25).

Decided by Deepak Pai on 22 Nov 2026 (#592): actuals rates are held on one
Statutory sheet per (fiscal year, fiscal period) and budget rates on one
Constant sheet per year (D1, D2, QA-1, QB-1). The rules for what each GER row
becomes are in ``konsol.rate_sheet_migration_model.plan`` (T5); this patch
reads the rows, calls it, and writes what it plans.

* **Reads raw**, every docstatus: ``tabGroup Exchange Rate``, Budget Cycles
  with their ``rate_set`` (the field left the JSON in T22; the column stays
  until ``drop_budget_cycle_rate_set``, which runs after this), Approved
  Budgets, the declared calendar, and the Rate Set flagged Is Actuals.
* **Stops visibly**: any refusal of the planner raises with every refusal
  listed, and nothing is written. So does a plan the controller cannot hold
  (below), a GER row the plan does not account for, and any controller
  refusal, which names the sheet.
* **Writes through the controller**, as the Administrator under
  ``MIGRATE_FLAG`` (the controller then skips the move check and the period
  gates, and accepts the Migrated source and Legacy Documents): ``insert``
  with the planner's name, then ``submit`` for an approved sheet. A
  correction chain is replayed as it happened: the earlier sheet is
  approved, then its successor (which names it in ``supersedes``) is
  approved, and that approval cancels the earlier one through the
  controller's own supersede cancel. So Versions and the trail are what a
  person's correction leaves; nothing is cancelled at the DB level.
* **Two shapes the controller cannot hold**:
  - A non-uniform Constant draft carries a blank rate on purpose (a person
    enters it), and the controller's row rules refuse a blank rate on every
    save. That draft alone is inserted with validate skipped
    (``flags.ignore_validate``); its window, label and title are set here
    from the same rules (``rate_sheet_model``). It publishes nothing.
  - A Statutory sheet for a Closing-type period: the planner migrates it
    with a warning, but the controller's window refuses it and the publish
    fails on it (refused sheets fail the publish, T10). Writing it would
    break every publish, so the migrate stops and names it.
* **Audit**: each sheet gets a private CSV of its GER rows (the planner's
  audit_rows). ``retire_group_exchange_rate`` (T26) drops the GER table, so
  ONE further CSV of every GER row, dropped ones included, with its
  disposition (the sheets that hold it, or dropped and why) is attached to
  the CONSTANT Rate Set (``rate_set.ensure_constant_set`` first). A GER row
  missing from that record stops the migrate.
* **Idempotent**: skipped when a Migrated sheet row or the migration audit
  file exists (the second covers a site whose every row was dropped).

Runs before the model sync (patches.txt has no sections), so the Rate Sheet
doctypes are reloaded first. A fresh install marks this patch done without
running it; a fresh site has no GER rows, and after_install creates CONSTANT.
"""
import csv
import io
from collections import Counter

import frappe

from konsol import rate_sheet_migration_model as model
from konsol import rate_sheet_model

RATE_SHEET = "Rate Sheet"
RATE_SET = "Rate Set"
GER_TABLE = "Group Exchange Rate"
#: The controller's flag (rate_sheet.py MIGRATE_FLAG); not imported, so this
#: patch loads without the controller's Document base.
MIGRATE_FLAG = "konsol_migrating_rates"
MIGRATED = model.MIGRATED
SOURCE_NOTE = "Migrated from Group Exchange Rate (konsol#592)."
AUDIT_FILE = "group-exchange-rate-migration-audit.csv"
AUDIT_COLUMNS = ("disposition", "rate_sheets", "reason") + model.AUDIT_FIELDS
STATUS = {0: "draft", 1: "approved", 2: "cancelled"}


def execute():
    for name in ("rate_sheet_row", "rate_sheet", "rate_set"):
        frappe.reload_doc("consolidation", "doctype", name)
    if not frappe.db.table_exists(GER_TABLE):
        print("konsol#592 rate sheets: no Group Exchange Rate table; nothing to migrate")
        return
    if _already_migrated():
        print("konsol#592 rate sheets: Group Exchange Rate is already migrated; nothing to do")
        return

    rates = _read_rates()
    actuals = _actuals_set(rates)
    calendar = _calendar()
    plan = model.plan(rates, actuals, _read_cycles(), calendar, _read_budgets()) if rates else {
        "sheets": [], "dropped": [], "refusals": [], "warnings": []}
    model.assert_no_refusals(plan)
    _refuse_unwritable(plan)
    audit = audit_rows(rates, plan)

    from konsol.consolidation.doctype.rate_set.rate_set import CONSTANT_NAME, ensure_constant_set

    ensure_constant_set()
    warnings = list(plan["warnings"])
    _write_sheets(plan["sheets"], warnings, calendar)
    if rates:
        _attach(RATE_SET, CONSTANT_NAME, AUDIT_FILE, AUDIT_COLUMNS, audit)
    _report(plan, warnings, len(rates))


# -- reads ------------------------------------------------------------------------------

def _already_migrated():
    from konsol.consolidation.doctype.rate_set.rate_set import CONSTANT_NAME

    return bool(frappe.db.exists("Rate Sheet Row", {"parenttype": RATE_SHEET, "source": MIGRATED})
                or frappe.db.exists("File", {"attached_to_doctype": RATE_SET,
                                             "attached_to_name": CONSTANT_NAME, "file_name": AUDIT_FILE}))


def _read_rates():
    columns = ", ".join(f"`{f}`" for f in model.AUDIT_FIELDS)
    return frappe.db.sql(f"SELECT {columns} FROM `tabGroup Exchange Rate` ORDER BY name", as_dict=True)


def _actuals_set(rates):
    flagged = [r["name"] for r in frappe.db.sql(
        "SELECT name FROM `tabRate Set` WHERE is_actuals = 1 ORDER BY name", as_dict=True)]
    if rates and len(flagged) != 1:
        frappe.throw(
            f"{len(rates)} Group Exchange Rate row(s) cannot be migrated to Rate Sheets: "
            + (f"{', '.join(flagged)} are all flagged Is Actuals" if flagged
               else "no Rate Set is flagged Is Actuals")
            + ". Flag exactly one Rate Set as Is Actuals (its rates become the Statutory sheets), "
            "then migrate again. Nothing was migrated.", frappe.ValidationError)
    return flagged[0] if flagged else None


def _read_cycles():
    """Budget Cycles with the raw ``rate_set`` column, which left the JSON (T22)."""
    if not frappe.db.table_exists("Budget Cycle"):
        return []
    rate_set = "`rate_set`" if frappe.db.has_column("Budget Cycle", "rate_set") else "NULL"
    return frappe.db.sql(
        f"SELECT name, fiscal_year, docstatus, {rate_set} AS rate_set FROM `tabBudget Cycle` ORDER BY name",
        as_dict=True)


def _read_budgets():
    if not frappe.db.table_exists("Approved Budget"):
        return []
    return frappe.db.sql(
        "SELECT name, fiscal_year, budget_cycle, rate_set, docstatus FROM `tabApproved Budget` ORDER BY name",
        as_dict=True)


def _calendar_rows():
    return frappe.db.sql(
        "SELECT y.fiscal_year AS fiscal_year, p.fiscal_period AS period, p.period_type AS type, "
        "p.start_date AS start_date, p.end_date AS end_date "
        "FROM `tabEPM Fiscal Year` y JOIN `tabEPM Fiscal Year Period` p "
        "ON p.parent = y.name AND p.parenttype = 'EPM Fiscal Year' AND p.parentfield = 'periods' "
        "ORDER BY y.fiscal_year, p.fiscal_period", as_dict=True)


def _calendar():
    """{year: [{"fiscal_year", "period", "type", "start_date", "end_date"}]}: the
    shape the planner and rate_sheet_model.window both read."""
    out = {}
    for r in _calendar_rows():
        out.setdefault(int(r["fiscal_year"]), []).append(dict(r))
    return out


# -- checks before any write ------------------------------------------------------------

def _refuse_unwritable(plan):
    closing = [s["name"] for s in plan["sheets"]
               if s["rate_basis"] == model.STATUTORY and s.get("period_type") == model.CLOSING_PERIOD]
    if closing:
        frappe.throw(
            "Group Exchange Rate cannot be migrated to Rate Sheets: "
            f"{', '.join(closing)} would be a Statutory sheet for a Closing period, which a Rate Sheet "
            "cannot hold and the publish refuses. The warehouse reads a Closing period at the year's "
            "last Regular period, so those rates are not read; cancel them in Group Exchange Rate (or "
            "delete the drafts), then migrate again. Nothing was migrated.", frappe.ValidationError)


def audit_rows(rates, plan):
    """One audit row per GER row: migrated (the sheets that hold it) or
    dropped (and why). Raises when a row is in neither: the record must not
    lose one."""
    holders = {}
    for s in plan["sheets"]:
        names = {a["name"] for a in s["audit_rows"]}
        for row in s["rows"]:
            names.update(row["legacy_documents"])
        for n in names:
            holders.setdefault(n, []).append(s["name"])
    dropped = {d["document"]: d["reason"] for d in plan["dropped"]}
    out, lost = [], []
    for r in sorted(rates, key=lambda r: r["name"]):
        record = {f: r.get(f) for f in model.AUDIT_FIELDS}
        if r["name"] in holders:
            record.update(disposition="migrated", rate_sheets=" ".join(holders[r["name"]]), reason="")
        elif r["name"] in dropped:
            record.update(disposition="dropped", rate_sheets="", reason=dropped[r["name"]])
        else:
            lost.append(r["name"])
        out.append(record)
    if lost:
        frappe.throw(f"The migration plan accounts for no sheet or drop of {', '.join(lost)}; nothing was "
                     "migrated (konsol#592 T25 must not lose a Group Exchange Rate row).",
                     frappe.ValidationError)
    return out


# -- writes -----------------------------------------------------------------------------

def _write_sheets(sheets, warnings, calendar):
    previous_user = frappe.session.user
    frappe.set_user("Administrator")
    frappe.flags[MIGRATE_FLAG] = True
    try:
        for sheet in sheets:
            _write_sheet(sheet, warnings, calendar)
        _settle_docstatus(sheets)
    finally:
        frappe.flags.pop(MIGRATE_FLAG, None)
        frappe.set_user(previous_user)


def _row(sheet, row):
    out = {"from_currency": row["from_currency"], "to_currency": row["to_currency"],
           "quoted_per": str(int(row["quoted_per"])), "source": MIGRATED,
           "legacy_documents": "\n".join(row["legacy_documents"])}
    if sheet["rate_basis"] == model.CONSTANT:
        out["rate"] = None if row.get("rate") is None else float(row["rate"])
    else:
        out["average_quote"] = float(row["average_quote"])
        out["closing_quote"] = float(row["closing_quote"])
    return out


def _change_reason(sheet, warnings):
    reason = (sheet.get("change_reason") or "").strip()
    if reason or not sheet.get("supersedes"):
        return reason or None
    legacy = sorted({d for row in sheet["rows"] for d in row["legacy_documents"]})
    warnings.append(f"{sheet['name']}: the Group Exchange Rate correction recorded no reason; the sheet "
                    "says so in its Reason for Change.")
    return (f"Migrated from Group Exchange Rate: the correction recorded no reason "
            f"(supersedes {sheet['supersedes']}; {', '.join(legacy)}).")


def _write_sheet(sheet, warnings, calendar):
    name = sheet["name"]
    doc = frappe.get_doc({
        "doctype": RATE_SHEET, "rate_basis": sheet["rate_basis"], "fiscal_year": sheet["fiscal_year"],
        "fiscal_period": sheet["fiscal_period"] if sheet["rate_basis"] == model.STATUTORY else None,
        "supersedes": sheet.get("supersedes"), "change_reason": _change_reason(sheet, warnings),
        "source_note": SOURCE_NOTE, "rates": [_row(sheet, r) for r in sheet["rows"]],
    })
    blank = sheet["rate_basis"] == model.CONSTANT and any(r.get("rate") is None for r in sheet["rows"])
    try:
        if blank:
            _prepare_blank_constant_draft(doc, sheet, calendar)
        doc.insert(ignore_permissions=True, set_name=name)
        if doc.name != name:
            frappe.throw(f"was named {doc.name}, not the planned {name}", frappe.ValidationError)
        _attach(RATE_SHEET, name, f"{name}-migration-audit.csv", model.AUDIT_FIELDS, sheet["audit_rows"])
        if sheet["docstatus"] >= 1:
            doc.submit()
    except Exception as e:
        raise model.MigrationRefused(
            f"Rate Sheet {name} could not be migrated from Group Exchange Rate: {e}") from e


def _prepare_blank_constant_draft(doc, sheet, calendar):
    """The one sheet written with validate skipped (see the module note):
    the window, label and title the controller would set. A blank Float is
    stored as 0, which the row rules refuse on the next save, so the draft
    cannot be approved until a person enters the rate."""
    if sheet["docstatus"] != 0:
        frappe.throw("only a draft Constant sheet may carry a blank rate", frappe.ValidationError)
    year = int(sheet["fiscal_year"])
    dates, why = rate_sheet_model.window({"rate_basis": model.CONSTANT, "fiscal_year": year,
                                          "fiscal_period": None}, calendar.get(year, []))
    if why:
        frappe.throw(why, frappe.ValidationError)
    doc.valid_from, doc.valid_to = dates
    doc.period_label = f"FY{year}"
    doc.title = f"{model.CONSTANT} {rate_sheet_model.key_label(model.CONSTANT, year, None)}"
    doc.flags.ignore_validate = True


def _settle_docstatus(sheets):
    """A cancelled version is cancelled by its successor's approval. Any
    planned cancel left approved (none in a planner chain) is cancelled
    through the controller; any other mismatch stops the migrate."""
    for sheet in sheets:
        if sheet["docstatus"] == 2 and int(frappe.db.get_value(RATE_SHEET, sheet["name"], "docstatus")) == 1:
            frappe.get_doc(RATE_SHEET, sheet["name"]).cancel()
    wrong = [f"{s['name']} is {STATUS.get(int(frappe.db.get_value(RATE_SHEET, s['name'], 'docstatus')))}, "
             f"planned {STATUS[s['docstatus']]}"
             for s in sheets if int(frappe.db.get_value(RATE_SHEET, s["name"], "docstatus")) != s["docstatus"]]
    if wrong:
        frappe.throw("Rate Sheet migration left sheets in the wrong state: " + "; ".join(wrong),
                     frappe.ValidationError)


def _csv(columns, rows):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(columns), extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for r in rows:
        writer.writerow({c: "" if r.get(c) is None else r.get(c) for c in columns})
    return buf.getvalue()


def _attach(doctype, name, file_name, columns, rows):
    frappe.get_doc({
        "doctype": "File", "file_name": file_name, "attached_to_doctype": doctype,
        "attached_to_name": name, "is_private": 1, "content": _csv(columns, rows),
    }).insert(ignore_permissions=True)


# -- report -----------------------------------------------------------------------------

def _report(plan, warnings, ger_rows):
    counts = Counter((s["rate_basis"], STATUS[s["docstatus"]]) for s in plan["sheets"])
    made = ", ".join(f"{n} {basis} {status}" for (basis, status), n in sorted(counts.items())) or "none"
    print(f"konsol#592 rate sheets: {ger_rows} Group Exchange Rate row(s) read; sheets made: {made}; "
          f"{len(plan['dropped'])} row(s) dropped"
          + (f"; every row and its disposition is in {AUDIT_FILE} on Rate Set CONSTANT" if ger_rows else ""))
    for d in plan["dropped"]:
        print(f"  dropped {d['document']}: {d['reason']}")
    for w in warnings:
        print(f"  warning: {w}")
