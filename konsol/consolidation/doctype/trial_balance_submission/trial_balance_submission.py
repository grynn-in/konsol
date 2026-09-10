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
import uuid
from datetime import date

import frappe
from frappe.model.document import Document

from konsol.clickhouse import execute

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
        item = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
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
        rows.append({
            "main_account": account,
            "debit": debit,
            "credit": credit,
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
        self._check_period_open()

        rows = self._parse_file()
        errors = validate_tb_rows(rows, known_accounts=self._chart_accounts())

        self.row_count = len(rows)
        self.total_debit = round(sum(r["debit"] for r in rows), 2)
        self.total_credit = round(sum(r["credit"] for r in rows), 2)
        if errors:
            self.validation_status = "Invalid"
            self.validation_message = "\n".join(errors)
            frappe.throw(
                "Trial balance failed validation:\n" + self.validation_message
            )
        self.validation_status = "Valid"
        self.validation_message = ""

    def before_submit(self):
        # Re-run the full validation at submit time: the file, the chart, or
        # the period may all have changed since the draft was saved.
        self.validate()

    def on_submit(self):
        rows = self._parse_file()
        self._ensure_tables()
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

    def on_cancel(self):
        # Deleting the claim removes the batch from consolidation without
        # touching the landed rows — they age out via the reaper.
        execute(
            f"ALTER TABLE {CONTROL_TABLE} DELETE "
            f"WHERE batch_id = '{_sql_str(self.batch_id)}'"
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

    def _check_period_open(self):
        """Submissions only land in an Open period (Period Status, #92)."""
        status = frappe.db.get_value(
            "Period Status",
            {"fiscal_year": str(self.fiscal_year),
             "fiscal_period": self.fiscal_period},
            "status",
        )
        if status is None:
            frappe.throw(
                f"No Period Status exists for {self.fiscal_year} "
                f"P{self.fiscal_period} — create it (and open the period) "
                "before submitting a trial balance"
            )
        if status != "Open":
            frappe.throw(
                f"Period {self.fiscal_year} P{self.fiscal_period} is "
                f"{status} — a trial balance can only be submitted into an "
                "Open period"
            )

    def _parse_file(self):
        if not self.tb_file:
            frappe.throw("Attach a trial balance CSV first")
        file_doc = frappe.get_doc("File", {"file_url": self.tb_file})
        content = file_doc.get_content()
        if isinstance(content, bytes):
            content = content.decode("utf-8-sig")
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
        execute(
            f"CREATE TABLE IF NOT EXISTS {CONTROL_TABLE} ("
            "batch_id String, submission_name String, data_area_id String, "
            "fiscal_year UInt16, fiscal_period UInt8, row_count UInt32, "
            "claimed_at DateTime"
            ") ENGINE = MergeTree ORDER BY batch_id"
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
