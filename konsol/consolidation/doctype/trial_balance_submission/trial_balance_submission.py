"""Trial Balance Submission — F8: the CSV intake for entities without an ERP feed.

A subsidiary with no connector uploads its period trial balance as a CSV;
validation runs synchronously against Frappe- and warehouse-side reference
data, and on submit the rows land in ClickHouse under a generated batch_id.

The control-table pattern is the load-bearing part. ClickHouse has no
transactions, so correctness comes from ordering, not atomicity:

  1. on_submit inserts the rows into epm_raw.trial_balance_submissions;
  2. only then does it write the batch_id to
     epm_raw.trial_balance_submission_control — the claim IS the commit point;
  3. bronze reads raw INNER JOIN control, so a crash between the two steps
     leaves rows nobody will ever read (reaped after REAP_AFTER_DAYS);
  4. on_cancel deletes the control row — the batch vanishes from consolidation
     without touching raw data;
  5. a resubmission is a NEW document with a NEW batch_id (Frappe's amend flow
     gives this for free), never an edit of landed rows.

CSV contract (header required, case-insensitive):
    main_account,debit,credit[,description][,partner_data_area_id][,amount_basis]
Amounts are in the entity's accounting currency. One row per account and
partner.

partner_data_area_id (konsol#159; `partner`, `partner_entity`, `partner_id`
and `counterparty` are accepted too) is the OTHER group entity a row is held
with. It is optional on every row and every account (decision 2, 13 Sep 2026):
a row on an intercompany account without one loads, is never eliminated, and
consolidation lists it as unmatched. Nothing guesses it. When given, it must be
an existing non-group Entity and never the row's own entity.

amount_basis (konsolidat#199; `basis` and `amount basis` are accepted too) is
what every debit and credit IS: this period's movement, the year-to-date
movement, or the closing balance at period end (konsol.tb_basis_model). The
submission declares it on the form, and the claim row carries it so the
warehouse can normalise all three into period movements. The column is
optional and only ever confirms the form: a row that names a different basis
refuses the file, since one file holds one basis. A blank cell is "not given".
"""

import csv
import importlib.util
import io
import json
import math
import os
import uuid

import frappe
from frappe.model.document import Document

from konsol.clickhouse import ensure_raw_tables, execute
from konsol.period_status import assert_open, assert_postable
from konsol.tb_basis_model import (
    ALIASES as BASIS_ALIASES, AMOUNT_BASES, COLUMN as BASIS, basis_problems, canonical,
)

RAW_TABLE = "epm_raw.trial_balance_submissions"
CONTROL_TABLE = "epm_raw.trial_balance_submission_control"
REAP_AFTER_DAYS = 7

#: sum(debit) and sum(credit) may differ by at most this much (currency units).
BALANCE_TOLERANCE = 0.01

_REQUIRED_COLUMNS = ("main_account", "debit", "credit")

#: The intercompany partner entity on a row (konsol#159).
PARTNER = "partner_data_area_id"
#: Other header spellings accepted for PARTNER.
PARTNER_ALIASES = ("partner", "partner_entity", "partner_id", "counterparty")


def _column(header):
    name = (header or "").strip().lower()
    if name in PARTNER_ALIASES:
        return PARTNER
    if name in BASIS_ALIASES:
        return BASIS
    return name


def parse_tb_csv(text):
    """Parse trial-balance CSV text into row dicts. Pure; host-testable.

    Returns a list of {main_account, debit, credit, description,
    partner_data_area_id, amount_basis, line}; the partner and the basis are
    '' when the file has no such column or the cell is blank. The basis is
    returned as written: validate() judges it (konsol.tb_basis_model). ``line``
    is the physical line the row came from (``reader.line_num``, konsol#305
    A38): csv.DictReader skips blank lines and a quoted field can span lines,
    so counting data rows (``index + 2``) drifts from the file's own line
    numbers whenever either happens.
    Raises ValueError with a human-readable message on structural problems —
    a missing header, a non-numeric amount, a blank account. Business
    validation (balance, duplicates, chart membership) is validate_tb_rows()'s
    job, so a file can be parsed and then reported on as a whole.
    """
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("The file is empty — expected a CSV header row")
    headers = [_column(h) for h in reader.fieldnames]
    missing = [c for c in _REQUIRED_COLUMNS if c not in headers]
    if missing:
        raise ValueError(
            f"Missing column(s) {', '.join(missing)} — the header must be "
            "main_account,debit,credit[,description][,partner_data_area_id][,amount_basis]"
        )
    if headers.count(PARTNER) > 1:
        raise ValueError(
            "Two partner columns: keep one of partner_data_area_id, "
            + ", ".join(PARTNER_ALIASES)
        )
    if headers.count(BASIS) > 1:
        raise ValueError(
            "Two amount_basis columns: keep one of " + ", ".join(BASIS_ALIASES)
        )

    rows = []
    for raw in reader:
        # reader.line_num is the physical line of the row just read (konsol#305
        # A38): unlike enumerate(reader, start=2), it stays correct across a
        # blank line DictReader skipped or a quoted field that spanned lines.
        lineno = reader.line_num
        # csv.DictReader parks surplus cells under the None restkey as a LIST;
        # without this check a stray trailing comma becomes an AttributeError
        # deep in the strip() below instead of a readable message.
        if raw.get(None):
            raise ValueError(
                f"Line {lineno}: more cells than the header has columns "
                "(a stray comma?)"
            )
        item = {_column(k): (v or "").strip() for k, v in raw.items()
                if k is not None}
        account = item.get("main_account", "")
        if not account:
            raise ValueError(f"Line {lineno}: main_account is blank")
        try:
            debit = float(item.get("debit") or 0)
            credit = float(item.get("credit") or 0)
        except ValueError:
            raise ValueError(
                f"Line {lineno}: debit/credit must be numbers "
                f"(got {item.get('debit')!r} / {item.get('credit')!r})"
            )
        # float() happily accepts 'nan' and 'inf', and NaN then sails through
        # every comparison in validate_tb_rows (all NaN comparisons are False),
        # so an arbitrarily unbalanced file would validate and land NaN in the
        # warehouse. Refuse non-finite values outright.
        if not (math.isfinite(debit) and math.isfinite(credit)):
            raise ValueError(
                f"Line {lineno}: debit/credit must be finite numbers "
                f"(got {item.get('debit')!r} / {item.get('credit')!r})"
            )
        # Round to cents HERE so the amounts validated, landed, and cast by
        # bronze (Decimal(38,2)) are all the same numbers — a file balanced
        # only at 3+ decimals must fail validation, not drift past it and
        # unbalance later in the warehouse.
        rows.append({
            "main_account": account,
            "debit": round(debit, 2),
            "credit": round(credit, 2),
            "description": item.get("description", ""),
            PARTNER: item.get(PARTNER, ""),
            BASIS: item.get(BASIS, ""),
            "line": lineno,
        })
    if not rows:
        raise ValueError("The file has a header but no data rows")
    return rows


def _row_label(r):
    partner = r.get(PARTNER) or ""
    return f"{r['main_account']} (partner {partner})" if partner else r["main_account"]


#: konsol#182: a site with no Published Main Account has no chart to post to.
NO_CHART = ("No group chart is published yet: upload and publish one (Main Account) "
            "before submitting trial balances")


def _load_tb_model():
    """konsol/close/tb_model.py, loaded by path: it is pure, and the host tests
    load this controller under a stub ``konsol`` package that has no ``close``."""
    app_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    spec = importlib.util.spec_from_file_location(
        "konsol_tbs_close_tb_model", os.path.join(app_dir, "close", "tb_model.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: konsol#305 A35 (decision P1): the per-line checker is the one rule set; the
#: file-level strings below are only its wording for a whole file.
_tb_model = _load_tb_model()
check_rows = _tb_model.check_rows

#: Row problems worded per file below; any other row problem is reported with its line.
_FILE_WORDED = frozenset({
    _tb_model.DUPLICATE_ROW, _tb_model.SELF_PARTNER, _tb_model.UNKNOWN_PARTNER,
    _tb_model.NEGATIVE_AMOUNT, _tb_model.HEADING_ACCOUNT, _tb_model.CLOSED_ACCOUNT,
    _tb_model.UNKNOWN_ACCOUNT,
    # the amount basis is judged once, by validate() against the form (basis_problems)
    _tb_model.AMOUNT_BASIS,
})


def _flagged(result, code):
    """(row, problem) for every row of a check_rows result with a problem of ``code``."""
    return [(row, p) for row in result["rows"] for p in row["problems"] if p["code"] == code]


def _chart_messages(result, chart):
    """The file-level wording of check_rows' account problems (konsol#182)."""
    if not chart:
        return [NO_CHART]
    out = []
    for code in sorted({row["main_account"] for row, _ in _flagged(result, _tb_model.HEADING_ACCOUNT)}):
        out.append(f"{code} is a heading in the group chart; post to the accounts under it.")
    closed = sorted({row["main_account"] for row, _ in _flagged(result, _tb_model.CLOSED_ACCOUNT)})
    if closed:
        out.append(f"Not open for posting in the group chart (is_posting is off): {', '.join(closed)}. "
                   "Post to another account, or ask the Close Lead to open it.")
    unknown = sorted({row["main_account"] for row, _ in _flagged(result, _tb_model.UNKNOWN_ACCOUNT)})
    if unknown:
        out.append(f"Account(s) not in the group chart: {', '.join(unknown)}")
    return out


def chart_errors(rows, chart):
    """What the group chart says about the accounts a trial balance posts to.
    ``chart`` is konsol.group_chart.chart_accounts(). Pure; host-testable.

    No chart at all is one refusal, not every account listed. A heading, and an
    account closed for posting, are refused with the reason. The rules are
    check_rows' (konsol#305 A35)."""
    result = check_rows(rows, chart, None, None, None, BALANCE_TOLERANCE)
    return _chart_messages(result, chart)


def validate_tb_rows(rows, known_accounts=None, tolerance=BALANCE_TOLERANCE,
                     entity=None, known_entities=None, chart=None):
    """Business validation over parsed rows. Pure; host-testable.

    Returns a list of error strings — empty means valid. known_accounts is the
    group chart (an iterable of account codes) or None to skip that check
    (the caller decides whether skipping is acceptable; the doctype does not).

    Partners (konsol#159): `entity` is the submitting entity, and a row may
    not name it as its own partner. known_entities is every entity a partner
    may be (the non-group Entities), or None to skip that check. A blank
    partner is always valid: the partner is optional (decision 2).

    chart (konsol#182) is the group chart, konsol.group_chart.chart_accounts();
    when given it decides the accounts (chart_errors) and known_accounts is
    not read.

    konsol#305 A35 (decision P1): every rule is konsol.close.tb_model.check_rows';
    this function only words its per-line problems per file. The amount basis
    is left to validate() (basis_problems against the form).
    """
    judged = chart
    if chart is None and known_accounts is not None:
        # A bare list of codes is a chart of posting accounts.
        judged = {code: {"is_group": 0, "is_posting": 1} for code in known_accounts}
    result = check_rows(rows, judged, entity, known_entities, None, tolerance)
    errors = []

    dupes = sorted({_row_label({"main_account": row["main_account"], PARTNER: row["partner"]})
                    for row, _ in _flagged(result, _tb_model.DUPLICATE_ROW)})
    if dupes:
        errors.append(
            f"Duplicate account rows: {', '.join(dupes)} — "
            "one row per account and partner; merge them before submitting"
        )

    own = sorted({row["main_account"] for row, _ in _flagged(result, _tb_model.SELF_PARTNER)})
    if own:
        errors.append(
            f"Partner is the entity itself ({entity}) on: {', '.join(own)} — "
            "a partner is the other group entity; leave it blank for a third party"
        )

    suggested = {}
    for row, p in _flagged(result, _tb_model.UNKNOWN_PARTNER):
        # check_rows words a case-only match as "Did you mean <entity>?"
        match = p["suggestion"][len("Did you mean "):-1] if p["suggestion"] else ""
        suggested[row["partner"]] = match
    if suggested:
        unknown = sorted(suggested)
        named = [f"{u} (did you mean {suggested[u]}?)" if suggested[u] else u for u in unknown]
        errors.append(
            f"Unknown partner entit{'y' if len(unknown) == 1 else 'ies'}: {', '.join(named)} — "
            "a partner must be an existing entity that is not a group"
        )

    negative = sorted({row["main_account"] for row, _ in _flagged(result, _tb_model.NEGATIVE_AMOUNT)})
    if negative:
        errors.append(
            f"Negative amounts on: {', '.join(negative)} — post the value to "
            "the opposite column instead of using a sign"
        )

    # The balance, and any file problem check_rows gains later. NO_CHART is
    # worded with the chart below; the form's basis is validate()'s.
    not_here = {NO_CHART, *basis_problems(None, [])}
    errors.extend(p for p in result["file_problems"] if p not in not_here)

    for row in result["rows"]:
        errors.extend(f"Line {row['line']}: {p['message']}" for p in row["problems"]
                      if p["code"] not in _FILE_WORDED)

    if chart is not None:
        errors.extend(_chart_messages(result, chart))
    elif known_accounts is not None:
        if judged:
            errors.extend(_chart_messages(result, judged))
        elif rows:
            # No known account at all: every account is outside the chart.
            errors.append(
                f"Account(s) not in the group chart: {', '.join(sorted({r['main_account'] for r in rows}))}"
            )

    return errors


def partnerless_ic_accounts(rows, ic_accounts):
    """The intercompany-account rows that name no partner. Pure; host-testable.

    Not an error (decision 2: the partner is optional). Such a row loads and is
    never eliminated; consolidation lists it as unmatched, so the uploader is
    warned. Returns the accounts, one per row (sorted).
    """
    ic = set(ic_accounts or ())
    return sorted(r["main_account"] for r in rows
                  if r["main_account"] in ic and not r.get(PARTNER))


def partnerless_warning(accounts):
    """The warning shown for partnerless_ic_accounts(), or '' for none."""
    if not accounts:
        return ""
    n = len(accounts)
    return (
        f"{n} intercompany row{'' if n == 1 else 's'} without a partner "
        f"(account{'' if n == 1 else 's'} {', '.join(accounts)}). "
        f"{'It loads' if n == 1 else 'They load'}, but {'is' if n == 1 else 'are'} never eliminated: "
        "consolidation lists them as unmatched. Add partner_data_area_id to eliminate them."
    )


def _sql_str(value):
    """Escape a value for a single-quoted ClickHouse string literal."""
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


#: Claim tuples per control-table INSERT (the same size _land_rows uses).
_CLAIM_BATCH = 1000


def _claim_values(doc, basis):
    """One ``(...)`` tuple claiming ``doc``'s batch with ``basis``.

    ``doc`` is the Document (on_submit) or the locked row set_amount_basis
    read (``name, batch_id, data_area_id, fiscal_year, fiscal_period,
    row_count`` by attribute). One text for the first claim and a re-claim:
    ReplacingMergeTree(claimed_at) keeps the newest row per batch_id, so a
    later tuple with the same batch_id and now() supersedes the earlier one."""
    return (
        f"('{_sql_str(doc.batch_id)}', '{_sql_str(doc.name)}', "
        f"'{_sql_str(doc.data_area_id)}', {int(doc.fiscal_year)}, "
        f"{int(doc.fiscal_period)}, {int(doc.row_count)}, now(), "
        f"'{_sql_str(basis)}')"
    )


def _claim_insert(values):
    """The control-table INSERT landing the ``_claim_values`` tuples ``values``."""
    return (
        f"INSERT INTO {CONTROL_TABLE} "
        "(batch_id, submission_name, data_area_id, fiscal_year, "
        "fiscal_period, row_count, claimed_at, amount_basis) VALUES "
        + ", ".join(values)
    )


_NAMES_HELP = "names must be a JSON list of Trial Balance Submission names"


@frappe.whitelist(methods=["POST"])
def set_amount_basis(names, amount_basis):
    """Declare the Amount Basis of already-submitted trial balances (konsolidat#199).

    Batches claimed before the basis existed carry ``''`` and the build
    preflight refuses them; this is the way to say what they hold without
    cancel-amend-resubmit, which would give every one a new batch_id.

    Why a NEW claim row rather than an UPDATE: ClickHouse has no transactions
    and ALTER ... UPDATE is an asynchronous mutation, while the claim is the
    batch's commit point (module docstring). The control table is a
    ReplacingMergeTree(claimed_at), so inserting the same batch_id with now()
    and the basis is exactly "the latest claim wins": bronze takes the newest
    claim per batch, the landed rows are never touched, and a cancel still
    deletes every claim of the batch.

    Order of work, and why:

    1. Each document is read with a row lock (``for_update=True``, the read
       _check_no_other_submission does), so a concurrent cancel either waits
       for this request to commit or this read already sees docstatus 2 and
       skips it. Judging docstatus on an unlocked read could re-claim a batch
       whose cancel is deleting the claim at the same moment.
    2. Every distinct (fiscal_year, fiscal_period) is gated Open once, before
       anything is written: a closed period on the last document must not
       leave the first ones re-claimed.
    3. MariaDB is written for every document, then ClickHouse gets ONE
       INSERT carrying every tuple (batches of _CLAIM_BATCH). Frappe commits
       MariaDB only after this request returns, so a ClickHouse failure rolls
       every form value back and leaves nothing newly declared in the
       warehouse; the two sides never disagree about which batches have a
       basis. Per-document INSERTs would have left a half-declared set on a
       mid-way failure.

    EPM Admin only. ``names`` is a list (or its JSON) of Trial Balance
    Submission names; ``amount_basis`` is matched case- and space-insensitively
    against the three bases. Drafts, cancelled and unknown names are skipped,
    not refused: a draft's basis is set on its form. Returns
    ``{"updated": n, "skipped": [(name, why), ...]}``.
    """
    from konsol.schema_lifecycle import check_epm_admin

    try:
        check_epm_admin()
    except frappe.PermissionError:
        frappe.throw("EPM Admin only: Set Amount Basis", frappe.PermissionError)
    basis = canonical(amount_basis)
    if basis is None:
        allowed = ", ".join(f'"{b}"' for b in AMOUNT_BASES)
        frappe.throw(f"{amount_basis!r} is not an amount basis: use one of {allowed}.")
    if isinstance(names, str):
        try:
            names = json.loads(names)
        except ValueError:
            frappe.throw(_NAMES_HELP)
    if not isinstance(names, list):
        frappe.throw(_NAMES_HELP)

    rows, skipped = [], []
    for name in names:
        row = frappe.db.get_value(
            "Trial Balance Submission", name,
            ["name", "docstatus", "fiscal_year", "fiscal_period", "batch_id",
             "data_area_id", "row_count"],
            as_dict=True, for_update=True,
        )
        if row is None:
            skipped.append((name, "not found"))
        elif row.docstatus != 1:
            skipped.append((name, "cancelled" if row.docstatus == 2 else "not submitted"))
        else:
            rows.append(row)

    for fiscal_year, fiscal_period in sorted({(r.fiscal_year, r.fiscal_period) for r in rows}):
        assert_open(fiscal_year, fiscal_period,
                    action="set the amount basis of a trial balance")

    for row in rows:
        # The documents are submitted and amount_basis is allow_on_submit;
        # the full save path is neither needed nor allowed. The form's
        # modified stamp is left alone: nothing the user wrote changed.
        frappe.db.set_value("Trial Balance Submission", row.name, "amount_basis", basis,
                            update_modified=False)
    values = [_claim_values(row, basis) for row in rows]
    for i in range(0, len(values), _CLAIM_BATCH):
        execute(_claim_insert(values[i:i + _CLAIM_BATCH]))
    return {"updated": len(rows), "skipped": skipped}


class TrialBalanceSubmission(Document):

    def before_insert(self):
        # R4 (konsol#297, konsol#305 A18): record whether this TB was uploaded
        # on the entity's behalf, from the uploader's roles and assigned
        # entities. Always computed here; whatever the request sent is
        # overwritten. Amending runs insert again, so an amendment is judged
        # by its own uploader.
        from konsol.entity_permissions import assigned_entities, subtree_codes

        on_behalf = _tb_model.is_on_behalf(
            frappe.get_roles(), self.data_area_id, subtree_codes(assigned_entities()))
        self.uploaded_on_behalf = "Yes" if on_behalf else "No"

    def before_validate(self):
        """A saved TB keeps the flag it was inserted with: a draft edit that
        sends another value is put back. A TB from before the field existed
        stays blank ("unknown", Problems 16). Frappe runs this before
        validate on every insert and save."""
        if self.is_new():
            return
        self.uploaded_on_behalf = frappe.db.get_value(
            "Trial Balance Submission", self.name, "uploaded_on_behalf") or ""

    def validate(self):
        if not self.batch_id:
            self.batch_id = uuid.uuid4().hex

        # konsol#189 (konsol/period_status.py): a trial balance posts only to a
        # period declared in EPM Fiscal Year, of a type this site lets trial
        # balances post to (Regular always; Opening/Closing/Adjustment when
        # ticked in EPM Settings), and Open. assert_postable refuses an
        # undeclared period (PeriodNotDeclared) or a type the site does not
        # post to; assert_open below refuses one that is Closed or Locked.
        assert_postable(self.fiscal_year, self.fiscal_period)

        self._check_entity_access()
        assert_open(self.fiscal_year, self.fiscal_period,
                    action="submit a trial balance")
        # Serialize submissions for one entity: without this lock two
        # concurrent submits (a bulk load and a month-view upload) could both
        # pass the duplicate check below and both claim, doubling the entity
        # in consolidation (#151 review). Held until the request commits.
        frappe.db.sql("SELECT `name` FROM `tabEntity` WHERE `name` = %s FOR UPDATE", self.data_area_id)
        self._check_no_other_submission()

        rows = self._parse_file()
        # konsol#182: the one chart reader, the Published Main Accounts in
        # MariaDB. No warehouse read: a site with nothing built still validates.
        from konsol.group_chart import chart_accounts

        errors = validate_tb_rows(rows, chart=chart_accounts(),
                                  entity=self.data_area_id,
                                  known_entities=self._partner_entities(rows))
        # konsolidat#199: the form's basis is required, and a file that
        # carries its own amount_basis column may only confirm it. Rows with
        # a blank cell are "not given" and are not judged. Data rows start on
        # line 2, right after the header.
        errors.extend(basis_problems(self.amount_basis, [
            (lineno, r[BASIS]) for lineno, r in enumerate(rows, start=2) if r[BASIS]
        ]))

        self.row_count = len(rows)
        self.total_debit = round(sum(r["debit"] for r in rows), 2)
        self.total_credit = round(sum(r["credit"] for r in rows), 2)
        if errors:
            # No "Invalid" status is persisted: frappe.throw rolls the save
            # back, so a stored Invalid state could never exist anyway — the
            # message IS the feedback.
            frappe.throw("Trial balance failed validation:\n" + "\n".join(errors))
        self.validation_status = "Valid"
        # A warning, not an error (decision 2): kept on the document so the
        # submitter and the reviewer both see it.
        self.validation_message = partnerless_warning(
            partnerless_ic_accounts(rows, self._ic_accounts()))
        if self.validation_message:
            frappe.msgprint(self.validation_message, title="Intercompany rows without a partner",
                            indicator="orange")

    def on_submit(self):
        rows = self._parse_file()
        self._ensure_tables()
        # Idempotent landing: a failed claim rolls the document back to draft
        # with the SAME batch_id, and ClickHouse has no transactions — so a
        # resubmit must replace, never append, or every amount doubles.
        # mutations_sync=1 because the INSERT follows immediately.
        execute(
            f"ALTER TABLE {RAW_TABLE} DELETE "
            f"WHERE batch_id = '{_sql_str(self.batch_id)}' "
            "SETTINGS mutations_sync = 1"
        )
        self._land_rows(rows)
        # The claim is the commit point. Nothing before this line is visible
        # to bronze; a crash before it leaves unclaimed rows for the reaper.
        # The claim also carries the amount basis (konsolidat#199): it is a
        # property of the batch, not of a row, and bronze normalises on it.
        execute(_claim_insert([_claim_values(self, self.amount_basis)]))

    def before_cancel(self):
        """validate() isn't run on cancel, so its period gate never applied
        here: a cancel dropped the batch from a closed period (#143 review)."""
        assert_open(self.fiscal_year, self.fiscal_period, action="cancel a trial balance submission")

    def on_cancel(self):
        # Deleting the claim removes the batch from consolidation without
        # touching the landed rows — they age out via the reaper.
        # mutations_sync=1: the delete must be VISIBLE before this returns —
        # an async mutation leaves a window where cancel + amend + resubmit has
        # both batches claimed and the entity double-counted.
        execute(
            f"ALTER TABLE {CONTROL_TABLE} DELETE "
            f"WHERE batch_id = '{_sql_str(self.batch_id)}' "
            "SETTINGS mutations_sync = 1"
        )

    # ── helpers ──────────────────────────────────────────────────────────

    def _check_entity_access(self):
        """The submitter must be allowed to see the entity they submit for.

        frappe.get_list applies konsol's entity-scoped permission conditions;
        frappe.get_all would not (see konsol-gotchas) — a scoped user must not
        be able to submit numbers for an entity they cannot read.
        """
        visible = frappe.get_list(
            "Entity", filters={"name": self.data_area_id}, pluck="name"
        )
        if not visible:
            frappe.throw(
                f"You do not have access to entity {self.data_area_id}"
            )

    def _check_no_other_submission(self):
        """One live submission per entity-period.

        Every claimed batch flows additively into consolidation, so a second
        submitted TB for the same entity and period would double the numbers —
        each batch balancing individually, no test firing. A correction is
        cancel (or amend, which cancels) first, then submit anew.
        """
        other = frappe.db.get_value(
            "Trial Balance Submission",
            {
                "data_area_id": self.data_area_id,
                "fiscal_year": self.fiscal_year,
                "fiscal_period": self.fiscal_period,
                "docstatus": 1,
                "name": ["!=", self.name],
            },
            "name",
            # a locking read sees the latest committed rows (REPEATABLE READ)
            for_update=True,
        )
        if other:
            frappe.throw(
                f"{other} is already submitted for {self.data_area_id} "
                f"{self.fiscal_year} P{self.fiscal_period}. Cancel or amend it "
                "first — consolidation would otherwise count both."
            )

    def _parse_file(self):
        if not self.tb_file:
            frappe.throw("Attach a trial balance CSV first")
        file_doc = frappe.get_doc("File", {"file_url": self.tb_file})
        content = file_doc.get_content()
        # Excel's "CSV UTF-8" starts with a byte-order mark, which get_content
        # may already have decoded into the string.
        content = content.decode("utf-8-sig") if isinstance(content, bytes) else content.lstrip("\ufeff")
        try:
            return parse_tb_csv(content)
        except ValueError as e:
            frappe.throw(f"Could not read the trial balance file: {e}")

    @staticmethod
    def _partner_entities(rows):
        """Every entity a partner may name: the non-group Entities, or None
        when no row names a partner (nothing to check). get_all, not
        get_list: a partner is named here, not read, so the submitter's entity
        scope does not limit which counterparty they may name."""
        if not any(r.get(PARTNER) for r in rows):
            return None
        return set(frappe.get_all("Entity", filters={"is_group": 0}, pluck="name",
                                  limit_page_length=0))

    @staticmethod
    def _ic_accounts():
        from konsol.consolidation.doctype.intercompany_account.intercompany_account import (
            intercompany_accounts,
        )
        return intercompany_accounts()

    def _ensure_tables(self):
        # The DDL lives in konsol.clickhouse (KEEP IN SYNC with konsolidat's
        # clickhouse/init-db.sql); this also adds the partner column to a
        # table created before konsol#159, and the control table's
        # amount_basis to one created before konsolidat#199.
        ensure_raw_tables()

    def _land_rows(self, rows):
        values = []
        for r in rows:
            values.append(
                f"('{_sql_str(self.batch_id)}', "
                f"'{_sql_str(self.data_area_id)}', "
                f"{int(self.fiscal_year)}, {int(self.fiscal_period)}, "
                f"'{_sql_str(r['main_account'])}', "
                f"{float(r['debit'])}, {float(r['credit'])}, "
                f"'{_sql_str(r['description'])}', "
                f"'{_sql_str(self.name)}', now(), "
                f"'{_sql_str(r.get(PARTNER) or '')}')"
            )
        batch_size = 1000
        for i in range(0, len(values), batch_size):
            execute(
                f"INSERT INTO {RAW_TABLE} (batch_id, data_area_id, "
                "fiscal_year, fiscal_period, main_account, debit_amount, "
                "credit_amount, description, submission_name, submitted_at, "
                f"{PARTNER}) "
                "VALUES " + ", ".join(values[i:i + batch_size])
            )


def on_doctype_update():
    """Index the one-live-submission check: it is a locking read on
    (entity, year, period), and without an index InnoDB locks every row of
    the table for it (#151 review). Runs on doctype sync and after migrate."""
    frappe.db.add_index("Trial Balance Submission", ["data_area_id", "fiscal_year", "fiscal_period"])


def reap_unclaimed_submissions():
    """Delete landed rows whose batch was never claimed (daily scheduler).

    A crash between landing and claiming leaves rows bronze will never read;
    after REAP_AFTER_DAYS they cannot correspond to a live draft worth keeping.
    Claimed batches are never touched — cancellation removes the claim but
    deliberately leaves the rows, and those are excluded here only once their
    claim is gone AND they are old enough.
    """
    try:
        execute(
            f"ALTER TABLE {RAW_TABLE} DELETE WHERE "
            f"submitted_at < now() - INTERVAL {REAP_AFTER_DAYS} DAY "
            f"AND batch_id NOT IN (SELECT batch_id FROM {CONTROL_TABLE})"
        )
    except Exception:
        frappe.logger().warning("trial balance reaper skipped", exc_info=True)
