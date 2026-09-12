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
    main_account,debit,credit[,description]
Amounts are in the entity's accounting currency. One row per account.
"""

import csv
import io
import math
import uuid

import frappe
from frappe.model.document import Document

from konsol.clickhouse import execute
from konsol.period_status import assert_open

RAW_TABLE = "epm_raw.trial_balance_submissions"
CONTROL_TABLE = "epm_raw.trial_balance_submission_control"
REAP_AFTER_DAYS = 7

#: sum(debit) and sum(credit) may differ by at most this much (currency units).
BALANCE_TOLERANCE = 0.01

_REQUIRED_COLUMNS = ("main_account", "debit", "credit")


def parse_tb_csv(text):
    """Parse trial-balance CSV text into row dicts. Pure; host-testable.

    Returns a list of {main_account, debit, credit, description}.
    Raises ValueError with a human-readable message on structural problems —
    a missing header, a non-numeric amount, a blank account. Business
    validation (balance, duplicates, chart membership) is validate_tb_rows()'s
    job, so a file can be parsed and then reported on as a whole.
    """
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("The file is empty — expected a CSV header row")
    headers = [h.strip().lower() for h in reader.fieldnames]
    missing = [c for c in _REQUIRED_COLUMNS if c not in headers]
    if missing:
        raise ValueError(
            f"Missing column(s) {', '.join(missing)} — the header must be "
            "main_account,debit,credit[,description]"
        )

    rows = []
    for lineno, raw in enumerate(reader, start=2):
        # csv.DictReader parks surplus cells under the None restkey as a LIST;
        # without this check a stray trailing comma becomes an AttributeError
        # deep in the strip() below instead of a readable message.
        if raw.get(None):
            raise ValueError(
                f"Line {lineno}: more cells than the header has columns "
                "(a stray comma?)"
            )
        item = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()
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
        })
    if not rows:
        raise ValueError("The file has a header but no data rows")
    return rows


def validate_tb_rows(rows, known_accounts=None, tolerance=BALANCE_TOLERANCE):
    """Business validation over parsed rows. Pure; host-testable.

    Returns a list of error strings — empty means valid. known_accounts is the
    group chart (an iterable of account codes) or None to skip that check
    (the caller decides whether skipping is acceptable; the doctype does not).
    """
    errors = []

    seen, dupes = set(), set()
    for r in rows:
        acct = r["main_account"]
        if acct in seen:
            dupes.add(acct)
        seen.add(acct)
    if dupes:
        errors.append(
            f"Duplicate account rows: {', '.join(sorted(dupes))} — "
            "one row per account; merge them before submitting"
        )

    negative = sorted({r["main_account"] for r in rows
                       if r["debit"] < 0 or r["credit"] < 0})
    if negative:
        errors.append(
            f"Negative amounts on: {', '.join(negative)} — post the value to "
            "the opposite column instead of using a sign"
        )

    total_debit = sum(r["debit"] for r in rows)
    total_credit = sum(r["credit"] for r in rows)
    if abs(total_debit - total_credit) > tolerance:
        errors.append(
            f"Debits ({total_debit:,.2f}) do not equal credits "
            f"({total_credit:,.2f}); difference "
            f"{total_debit - total_credit:,.2f} exceeds the "
            f"{tolerance} tolerance"
        )

    if known_accounts is not None:
        known = set(known_accounts)
        unknown = sorted({r["main_account"] for r in rows
                          if r["main_account"] not in known})
        if unknown:
            errors.append(
                f"Account(s) not in the group chart: {', '.join(unknown)}"
            )

    return errors


def _sql_str(value):
    """Escape a value for a single-quoted ClickHouse string literal."""
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


class TrialBalanceSubmission(Document):

    def validate(self):
        if not self.batch_id:
            self.batch_id = uuid.uuid4().hex

        if not (1 <= int(self.fiscal_period or 0) <= 12):
            frappe.throw("Fiscal period must be 1–12 for a trial balance submission")

        self._check_entity_access()
        # App-wide convention (konsol/period_status.py): a period nobody has
        # closed has no record, and IS Open — records are created on demand.
        # Requiring a record here would block every submission into a normal
        # untouched period. assert_open throws on Closed/Locked and on nothing
        # else.
        assert_open(self.fiscal_year, self.fiscal_period,
                    action="submit a trial balance")
        # Serialize submissions for one entity: without this lock two
        # concurrent submits (a bulk load and a month-view upload) could both
        # pass the duplicate check below and both claim, doubling the entity
        # in consolidation (#151 review). Held until the request commits.
        frappe.db.sql("SELECT `name` FROM `tabEntity` WHERE `name` = %s FOR UPDATE", self.data_area_id)
        self._check_no_other_submission()

        rows = self._parse_file()
        errors = validate_tb_rows(rows, known_accounts=self._chart_accounts())

        self.row_count = len(rows)
        self.total_debit = round(sum(r["debit"] for r in rows), 2)
        self.total_credit = round(sum(r["credit"] for r in rows), 2)
        if errors:
            # No "Invalid" status is persisted: frappe.throw rolls the save
            # back, so a stored Invalid state could never exist anyway — the
            # message IS the feedback.
            frappe.throw("Trial balance failed validation:\n" + "\n".join(errors))
        self.validation_status = "Valid"
        self.validation_message = ""

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
        execute(
            f"INSERT INTO {CONTROL_TABLE} "
            "(batch_id, submission_name, data_area_id, fiscal_year, "
            "fiscal_period, row_count, claimed_at) VALUES "
            f"('{_sql_str(self.batch_id)}', '{_sql_str(self.name)}', "
            f"'{_sql_str(self.data_area_id)}', {int(self.fiscal_year)}, "
            f"{int(self.fiscal_period)}, {int(self.row_count)}, now())"
        )

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

    def _chart_accounts(self):
        """The group chart, from the warehouse (silver_main_accounts).

        Deliberately NOT best-effort: if the warehouse cannot be reached, the
        submission is rejected rather than accepted unverified — financial
        data must never land on the strength of a connection error.
        """
        try:
            text = execute(
                "SELECT DISTINCT main_account_id FROM epm_silver.silver_main_accounts"
            )
        except Exception as e:
            frappe.throw(
                "Cannot validate accounts against the group chart — "
                f"ClickHouse is unreachable ({e}). Try again once the "
                "warehouse is up; submissions are never accepted unvalidated."
            )
        return {line.strip() for line in text.splitlines() if line.strip()}

    def _ensure_tables(self):
        execute(
            f"CREATE TABLE IF NOT EXISTS {RAW_TABLE} ("
            "batch_id String, data_area_id String, fiscal_year UInt16, "
            "fiscal_period UInt8, main_account String, "
            "debit_amount Float64, credit_amount Float64, "
            "description String, submission_name String, "
            "submitted_at DateTime"
            ") ENGINE = MergeTree ORDER BY (batch_id, main_account)"
        )
        # KEEP IN SYNC with clickhouse/init-db.sql in konsolidat (the fresh-
        # install owner of the same two schemas). ReplacingMergeTree keyed on
        # batch_id: a duplicated claim collapses instead of fanning out the
        # bronze join.
        execute(
            f"CREATE TABLE IF NOT EXISTS {CONTROL_TABLE} ("
            "batch_id String, submission_name String, data_area_id String, "
            "fiscal_year UInt16, fiscal_period UInt8, row_count UInt32, "
            "claimed_at DateTime"
            ") ENGINE = ReplacingMergeTree(claimed_at) ORDER BY batch_id"
        )

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
                f"'{_sql_str(self.name)}', now())"
            )
        batch_size = 1000
        for i in range(0, len(values), batch_size):
            execute(
                f"INSERT INTO {RAW_TABLE} (batch_id, data_area_id, "
                "fiscal_year, fiscal_period, main_account, debit_amount, "
                "credit_amount, description, submission_name, submitted_at) "
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
