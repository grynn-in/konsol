"""Rate Sheet controller (konsol#592 pair 1, T8 validate and T9 lifecycle).

The controller ports Group Exchange Rate's governance to sheets: provenance
flags, the move threshold, supersede (#312-3), no plain cancel of a
Statutory sheet (#312-S3-2), the year lock before the row lock, and the
publish after the commit. Constant sheets lock when their whole year is
closed (P1-Q2 B) and may be cancelled while it is not (P1-Q3 C). The rules
live in konsol.rate_sheet_model (loaded for real here); frappe, the period
gates and group_rates are stubbed."""
import contextlib
import datetime
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RS_DIR = os.path.join(APP_DIR, "consolidation", "doctype", "rate_sheet")
CONTROLLER = os.path.join(RS_DIR, "rate_sheet.py")
MODEL = os.path.join(APP_DIR, "rate_sheet_model.py")


class Refused(Exception):
    pass


class _Flags(dict):
    """frappe._dict: a dict read and written as attributes."""
    __getattr__ = dict.get

    def __setattr__(self, name, value):
        self[name] = value


class Row:
    """A child row: attribute and .get access, as a Frappe child Document."""
    def __init__(self, **fields):
        self.__dict__.update(fields)

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return None

    def get(self, name, default=None):
        return self.__dict__.get(name, default)


def _calendar(year=2025):
    """A declared year: P0 Opening on 1 Jan, P1-P12 Regular, P13 Closing."""
    rows = [dict(fiscal_year=year, period=0, type="Opening", start_date=datetime.date(year, 1, 1),
                 end_date=datetime.date(year, 1, 1), period_code="P00", period_label="Opening")]
    for m in range(1, 13):
        end = (datetime.date(year + (m == 12), m % 12 + 1, 1) - datetime.timedelta(days=1))
        rows.append(dict(fiscal_year=year, period=m, type="Regular", start_date=datetime.date(year, m, 1),
                         end_date=end, period_code=f"P{m:02d}", period_label=f"{year}-{m:02d}"))
    rows.append(dict(fiscal_year=year, period=13, type="Closing", start_date=datetime.date(year, 12, 31),
                     end_date=datetime.date(year, 12, 31), period_code="P13", period_label="Closing"))
    return rows


class World:
    """What the stub database holds, and what the controller did to it."""

    def __init__(self, **kw):
        self.calendar = kw.get("calendar", _calendar(2025) + _calendar(2024))
        self.group_currencies = kw.get("group_currencies", ("USD", "CHF"))
        self.existing_names = set(kw.get("existing_names", ()))
        #: name -> {rate_basis, fiscal_year, fiscal_period, docstatus}
        self.sheets = dict(kw.get("sheets", {}))
        #: name -> [row dicts]
        self.sheet_rows = dict(kw.get("sheet_rows", {}))
        self.previous = kw.get("previous")            # name the previous-approved query returns
        self.move_threshold = kw.get("move_threshold", 0.5)
        self.magnitude = kw.get("magnitude", {})      # (from, to) -> problem text
        #: konsol#179: the declared Magnitude Warning Factor (None: undeclared),
        #: and what group_rates.magnitude_warning says, keyed on
        #: (from, to, quote, quoted_per). Every call is recorded.
        self.magnitude_factor = kw.get("magnitude_factor")
        self.warnings = kw.get("warnings", {})
        self.warned = []
        self.msgprints = []
        self.period_open = kw.get("period_open", True)
        self.year_open = kw.get("year_open", True)
        self.flags = kw.get("flags", {})
        self.user = kw.get("user", "approver@example.com")
        self.sql = []
        self.gates = []
        self.published = []
        self.queued = []
        self.cancelled = []
        self.cancel_state = []
        self.thrown = []
        self.indexes = []


def _key_matches(meta, params, statutory):
    basis, year = params[0], params[1]
    if meta["rate_basis"] != basis or int(meta["fiscal_year"]) != int(year):
        return False
    return not statutory or int(meta["fiscal_period"] or 0) == int(params[2])


def _frappe(w):
    frappe = types.ModuleType("frappe")
    for name in ("ValidationError", "MandatoryError", "DuplicateEntryError", "PermissionError"):
        setattr(frappe, name, type(name, (Exception,), {}))

    def throw(msg, exc=None, *a, **k):
        w.thrown.append(getattr(exc, "__name__", None))
        raise Refused(msg)

    def sql(query, params=(), *a, **k):
        w.sql.append(query)
        if "FROM `tabEPM Fiscal Year` y" in query and "JOIN" in query:
            return [dict(r) for r in w.calendar if int(r["fiscal_year"]) == int(params[0])]
        if "FROM `tabEPM Fiscal Year`" in query:
            return [("FY", "Open")]
        if "FROM `tabConsolidation Group`" in query:
            return [(1,)] if params[0] in w.group_currencies else []
        if "FROM `tabRate Sheet`" in query and "ORDER BY fiscal_year DESC" in query:
            return [(w.previous,)] if w.previous else []
        if "FROM `tabRate Sheet`" in query and "FOR UPDATE" in query:
            statutory = "fiscal_period = %s" in query
            own = params[-1]
            out = []
            for name, meta in sorted(w.sheets.items()):
                if name == own or not _key_matches(meta, params, statutory):
                    continue
                if "docstatus = 1" in query and int(meta["docstatus"]) != 1:
                    continue
                if "docstatus < 2" in query and int(meta["docstatus"]) >= 2:
                    continue
                out.append((name, meta["docstatus"]) if "docstatus < 2" in query else (name,))
            return out
        return []

    def get_value(doctype, name, fields=None, as_dict=False, **k):
        meta = w.sheets.get(name)
        if meta is None:
            return None
        return types.SimpleNamespace(**{f: meta.get(f) for f in fields})

    def get_all(doctype, filters=None, fields=None, **k):
        assert doctype == "Rate Sheet Row", doctype
        return [types.SimpleNamespace(**r) for r in w.sheet_rows.get(filters["parent"], [])]

    def get_doc(doctype, name, for_update=False):
        doc = types.SimpleNamespace(name=name, doctype=doctype, flags=_Flags(), for_update=for_update)

        def cancel():
            w.cancelled.append((name, dict(doc.flags), list(getattr(doc, "ignore_linked_doctypes", []))))
        doc.cancel = cancel
        return doc

    frappe.throw = throw
    frappe._ = lambda s: s
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe.flags = types.SimpleNamespace(**w.flags)
    frappe.flags.get = lambda k, d=None: getattr(frappe.flags, k, d)
    frappe.session = types.SimpleNamespace(user=w.user)
    frappe.get_all = get_all
    frappe.get_doc = get_doc
    frappe.msgprint = lambda msg, *a, **k: w.msgprints.append(msg)
    frappe.db = types.SimpleNamespace(
        sql=sql, get_value=get_value,
        exists=lambda dt, name=None: name in w.existing_names,
        add_index=lambda *a, **k: w.indexes.append((a, k)),
    )
    return frappe


def _group_rates(w):
    gr = types.ModuleType("konsol.group_rates")
    gr.MIN_SIGNIFICANT_DIGITS = 6
    gr.move_threshold = lambda: w.move_threshold
    gr.usd_references = lambda codes: {c: 0.0 for c in codes}
    gr.magnitude_problem = lambda f, t, rate, refs=None: w.magnitude.get((f, t))
    gr.significant_digits = lambda q: 9
    gr.quote_label = lambda q, per, f, t: f"{float(q or 0):.9g} {t} per {int(per or 1)} {f}"
    gr.publish_rates = lambda force=False: w.published.append(("publish_rates", force))
    gr.MAGNITUDE_WARNING_UNDECLARED = ("Magnitude Warning Factor is not declared (Close Settings): "
                                       "no rate is checked against it.")
    gr.magnitude_warning_factor = lambda: w.magnitude_factor

    def magnitude_warning(f, t, quote, per, factor, refs=None):
        w.warned.append((f, t, float(quote or 0), int(per or 1), factor))
        return w.warnings.get((f, t, float(quote or 0), int(per or 1))) if factor is not None else None
    gr.magnitude_warning = magnitude_warning
    return gr


@contextlib.contextmanager
def harness(**kw):
    """(module, world), with the stubs in sys.modules for the whole block:
    the controller imports group_rates and clickhouse at call time."""
    w = World(**kw)
    frappe = _frappe(w)

    class Document:
        def __init__(self, **fields):
            self.__dict__.update(fields)
            self.__dict__.setdefault("flags", _Flags())

        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)
            return None

        def get(self, name, default=None):
            return self.__dict__.get(name, default)

        def is_new(self):
            return self.__dict__.get("_before") is None

        def get_doc_before_save(self):
            before = self.__dict__.get("_before")
            return types.SimpleNamespace(**before) if before is not None else None

        def check_if_latest(self):
            w.sql.append("BASE_CHECK_IF_LATEST")

    def assert_open(fy, fp, action="run"):
        w.gates.append(("assert_open", fy, fp, action))
        if not w.period_open:
            raise Refused(f"Cannot {action}: period closed")

    def assert_year_not_closed(fy, action="run"):
        w.gates.append(("assert_year_not_closed", fy, action))
        if not w.year_open:
            raise Refused(f"Cannot {action}: every period of FY{fy} is closed")

    mods = {n: types.ModuleType(n) for n in (
        "frappe.model", "frappe.model.document", "konsol", "konsol.period_status", "konsol.workflows",
        "konsol.clickhouse")}
    mods["frappe"] = frappe
    mods["frappe.model.document"].Document = Document
    mods["konsol.period_status"].assert_open = assert_open
    mods["konsol.period_status"].assert_year_not_closed = assert_year_not_closed
    mods["konsol.workflows"].set_cancelled_state = lambda doc: w.cancel_state.append(
        (doc.name, list(w.gates)))
    def after_commit_once(key, fn):
        """Queued, not run: the test runs what was queued."""
        if all(k != key for k, _ in w.queued):
            w.queued.append((key, fn))
    mods["konsol.clickhouse"].after_commit_once = after_commit_once
    mods["konsol.group_rates"] = _group_rates(w)
    spec = importlib.util.spec_from_file_location("konsol.rate_sheet_model", MODEL)
    model = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(model)
    mods["konsol.rate_sheet_model"] = model
    for name in ("group_rates", "clickhouse", "rate_sheet_model", "period_status", "workflows"):
        setattr(mods["konsol"], name, mods[f"konsol.{name}"])
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("rate_sheet_under_test", CONTROLLER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module, w
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


def _row(**fields):
    base = dict(name="row1", from_currency="EUR", to_currency="USD", quoted_per="1",
                average_quote=1.08, closing_quote=1.09, source="Manual")
    return Row(**dict(base, **fields))


def _crow(**fields):
    base = dict(name="row1", from_currency="EUR", to_currency="USD", quoted_per="1", rate=1.08,
                source="Manual")
    return Row(**dict(base, **fields))


def _sheet(module, rows=None, **fields):
    base = dict(doctype="Rate Sheet", name="RS-S-2025-06", docstatus=0, rate_basis="Statutory",
                fiscal_year=2025, fiscal_period=6)
    merged = dict(base, **fields)
    if merged["rate_basis"] == "Constant" and "name" not in fields:
        merged["name"] = "RS-C-2025"
    merged["rates"] = rows if rows is not None else (
        [_crow()] if merged["rate_basis"] == "Constant" else [_row()])
    return module.RateSheet(**merged)


def _before(sheet, rows=None, **fields):
    """A saved version of ``sheet``: same key and rows unless given."""
    base = {f: sheet.get(f) for f in ("rate_basis", "fiscal_year", "fiscal_period", "copied_from",
                                       "source_note", "change_reason")}
    base["rates"] = rows if rows is not None else [Row(**dict(r.__dict__)) for r in sheet.rates]
    sheet.__dict__["_before"] = dict(base, **fields)
    return sheet


def _refused(fn, *a):
    try:
        fn(*a)
    except Refused as e:
        return str(e)
    return None


# == T8: naming ==========================================================================

def test_statutory_name_is_basis_year_and_padded_period():
    with harness() as (m, w):
        d = _sheet(m, name=None, fiscal_period=6)
        d.autoname()
        assert d.name == "RS-S-2025-06"


def test_constant_name_is_basis_and_year():
    with harness() as (m, w):
        d = _sheet(m, name=None, rate_basis="Constant", fiscal_period=None)
        d.autoname()
        assert d.name == "RS-C-2025"


def test_a_correction_is_named_dash_two_then_dash_three():
    with harness(existing_names={"RS-S-2025-06"}) as (m, w):
        d = _sheet(m, name=None)
        d.autoname()
        assert d.name == "RS-S-2025-06-2"
    with harness(existing_names={"RS-C-2025", "RS-C-2025-2"}) as (m, w):
        d = _sheet(m, name=None, rate_basis="Constant", fiscal_period=None)
        d.autoname()
        assert d.name == "RS-C-2025-3"


def test_a_statutory_sheet_without_a_period_cannot_be_named():
    with harness() as (m, w):
        d = _sheet(m, name=None, fiscal_period=None)
        assert _refused(d.autoname)


# == T8: the window ======================================================================

def test_statutory_window_comes_from_the_calendar():
    with harness() as (m, w):
        d = _sheet(m)
        d.validate()
        assert (d.valid_from, d.valid_to) == (datetime.date(2025, 6, 1), datetime.date(2025, 6, 30))
        assert d.period_label == "2025-06"
        assert d.title == "Statutory FY2025 P6"


def test_opening_period_is_allowed_and_closing_refused():
    with harness() as (m, w):
        d = _sheet(m, fiscal_period=0, name="RS-S-2025-00")
        d.validate()
        assert d.valid_from == datetime.date(2025, 1, 1)
    with harness() as (m, w):
        why = _refused(_sheet(m, fiscal_period=13, name="RS-S-2025-13").validate)
        assert why and "Closing" in why


def test_an_undeclared_year_is_refused():
    with harness() as (m, w):
        why = _refused(_sheet(m, fiscal_year=2030, name="RS-S-2030-06").validate)
        assert why and "FY2030 is not declared" in why


def test_constant_covers_the_year_and_stores_no_period():
    """A stored Int reads back 0, so 0 on a Constant sheet is unset; the
    controller stores None, never a period."""
    with harness() as (m, w):
        for blank in (None, 0, ""):
            d = _sheet(m, rate_basis="Constant", fiscal_period=blank)
            d.validate()
            assert d.fiscal_period is None
            assert (d.valid_from, d.valid_to) == (datetime.date(2025, 1, 1), datetime.date(2025, 12, 31))
            assert d.title == "Constant FY2025 Constant"


def test_constant_with_a_period_is_refused():
    with harness() as (m, w):
        why = _refused(_sheet(m, rate_basis="Constant", fiscal_period=5).validate)
        assert why and "no Fiscal Period" in why


# == T8: the rows ========================================================================

def test_row_problems_are_refused_together():
    with harness() as (m, w):
        rows = [_row(), _row(name="row2", from_currency="USD", to_currency="USD")]
        why = _refused(_sheet(m, rows=rows).validate)
        assert why and "same currency" in why


def test_a_statutory_row_needs_both_quotes():
    with harness() as (m, w):
        why = _refused(_sheet(m, rows=[_row(closing_quote=0)]).validate)
        assert why and "Closing Quote" in why


def test_to_currency_must_be_a_group_reporting_currency():
    with harness(group_currencies=("CHF",)) as (m, w):
        why = _refused(_sheet(m).validate)
        assert why and "USD is not the reporting currency" in why
        assert w.thrown[-1] == "ValidationError"


def test_an_implausible_magnitude_is_refused():
    with harness(magnitude={("EUR", "USD"): "about 100x above"}) as (m, w):
        why = _refused(_sheet(m).validate)
        assert why and "100x" in why


def test_quote_labels_are_set():
    with harness() as (m, w):
        d = _sheet(m)
        d.validate()
        assert "Average" in d.rates[0].quote_label and "Closing" in d.rates[0].quote_label
    with harness() as (m, w):
        d = _sheet(m, rate_basis="Constant", fiscal_period=None)
        d.validate()
        assert d.rates[0].quote_label == "1.08 USD per 1 EUR"


# == T8: provenance ======================================================================

def test_a_forged_erp_prefill_source_is_refused_with_403():
    with harness() as (m, w):
        why = _refused(_sheet(m, rows=[_row(source="ERP pre-fill")]).validate)
        assert why and "Pre-fill" in why
        assert w.thrown[-1] == "PermissionError"


def test_the_prefill_flag_may_set_the_source_and_erp_quotes():
    with harness(flags={"konsol_prefilling_rate_sheet": True}) as (m, w):
        d = _sheet(m, rows=[_row(source="ERP pre-fill", erp_average_quote=1.08, erp_closing_quote=1.09)],
                   source_note="ERP feed")
        d.validate()
        assert d.rates[0].source == "ERP pre-fill"


def test_forged_erp_quotes_are_refused():
    with harness() as (m, w):
        assert _refused(_sheet(m, rows=[_row(erp_average_quote=1.0)]).validate)
        assert w.thrown[-1] == "PermissionError"


def test_a_forged_duplicated_source_is_refused():
    with harness() as (m, w):
        assert _refused(_sheet(m, rows=[_row(source="Duplicated")]).validate)
        assert w.thrown[-1] == "PermissionError"
    with harness(flags={"konsol_duplicating_rate_sheet": True}) as (m, w):
        d = _sheet(m, rows=[_row(source="Duplicated")], copied_from="RS-S-2025-05",
                   source_note="Duplicated from RS-S-2025-05")
        d.validate()
        assert d.rates[0].source == "Duplicated"


def test_a_forged_copied_from_or_source_note_is_refused():
    with harness() as (m, w):
        assert _refused(_sheet(m, copied_from="RS-S-2025-05").validate)
        assert w.thrown[-1] == "PermissionError"
    with harness() as (m, w):
        assert _refused(_sheet(m, source_note="typed by a person").validate)
        assert w.thrown[-1] == "PermissionError"


def test_migrated_needs_the_flag_and_the_administrator():
    with harness() as (m, w):
        assert _refused(_sheet(m, rows=[_row(source="Migrated", legacy_documents="GER-1")]).validate)
        assert w.thrown[-1] == "PermissionError"
    with harness(flags={"konsol_migrating_rates": True}) as (m, w):   # not the Administrator
        assert _refused(_sheet(m, rows=[_row(source="Migrated")]).validate)
    with harness(flags={"konsol_migrating_rates": True}, user="Administrator") as (m, w):
        d = _sheet(m, rows=[_row(source="Migrated", legacy_documents="GER-1, GER-2")])
        d.validate()
        assert d.rates[0].source == "Migrated"


def test_forged_legacy_documents_are_refused():
    with harness() as (m, w):
        assert _refused(_sheet(m, rows=[_row(legacy_documents="GER-1")]).validate)
        assert w.thrown[-1] == "PermissionError"


def test_a_saved_source_is_kept_on_a_later_save_without_the_flag():
    with harness() as (m, w):
        d = _sheet(m, rows=[_row(source="ERP pre-fill", erp_average_quote=1.08, erp_closing_quote=1.09)],
                   source_note="ERP feed")
        _before(d)
        d.validate()
        assert d.rates[0].source == "ERP pre-fill"


# == T8: edits make a row Manual ===========================================================

def test_editing_a_prefilled_quote_makes_the_row_manual():
    with harness() as (m, w):
        d = _sheet(m, rows=[_row(source="ERP pre-fill", erp_average_quote=1.08, erp_closing_quote=1.09)],
                   source_note="ERP feed")
        _before(d)
        d.rates[0].average_quote = 1.10
        d.validate()
        assert d.rates[0].source == "Manual"


def test_editing_a_duplicated_quote_makes_the_row_manual():
    with harness() as (m, w):
        d = _sheet(m, rows=[_row(source="Duplicated")], copied_from="RS-S-2025-05", source_note="dup")
        _before(d)
        d.rates[0].closing_quote = 1.2
        d.validate()
        assert d.rates[0].source == "Manual"


def test_requoting_the_same_rate_keeps_the_source_and_rescales_erp():
    with harness() as (m, w):
        d = _sheet(m, rows=[_row(from_currency="JPY", source="ERP pre-fill", average_quote=0.0066,
                                 closing_quote=0.0067, erp_average_quote=0.0066, erp_closing_quote=0.0067)],
                   source_note="ERP feed")
        _before(d)
        d.rates[0].quoted_per, d.rates[0].average_quote, d.rates[0].closing_quote = "100", 0.66, 0.67
        d.validate()
        assert d.rates[0].source == "ERP pre-fill"
        assert abs(d.rates[0].erp_average_quote - 0.66) < 1e-12


def test_a_duplicated_row_moved_to_another_pair_is_manual():
    with harness() as (m, w):
        d = _sheet(m, rows=[_row(source="Duplicated")], copied_from="RS-S-2025-05", source_note="dup")
        _before(d)
        d.rates[0].to_currency = "CHF"
        d.validate()
        assert d.rates[0].source == "Manual"


def test_a_blank_source_is_manual():
    with harness() as (m, w):
        d = _sheet(m, rows=[_row(source=None)])
        d.validate()
        assert d.rates[0].source == "Manual"


# == T8: the move check ======================================================================

def _prev(rows, basis="Statutory", fy=2025, fp=5, name="RS-S-2025-05"):
    return dict(previous=name,
                sheets={name: dict(rate_basis=basis, fiscal_year=fy, fiscal_period=fp, docstatus=1)},
                sheet_rows={name: rows})


def test_a_move_over_the_threshold_needs_a_reason():
    prev = _prev([dict(from_currency="EUR", to_currency="USD", quoted_per="1", average_quote=0.5,
                       closing_quote=1.09)])
    with harness(**prev) as (m, w):
        why = _refused(_sheet(m).validate)
        assert why and "EUR → USD Average" in why and "RS-S-2025-05" in why
        assert w.thrown[-1] == "MandatoryError"
        q = next(s for s in w.sql if "ORDER BY fiscal_year DESC" in s)
        assert "rate_basis = %s" in q and "fiscal_period < %s" in q and "docstatus = 1" in q
    with harness(**prev) as (m, w):
        _sheet(m, change_reason="ECB fixing moved").validate()


def test_a_move_within_the_threshold_needs_no_reason():
    prev = _prev([dict(from_currency="EUR", to_currency="USD", quoted_per="1", average_quote=1.0,
                       closing_quote=1.0)])
    with harness(**prev) as (m, w):
        _sheet(m).validate()


def test_an_undeclared_threshold_names_close_settings():
    prev = _prev([dict(from_currency="EUR", to_currency="USD", quoted_per="1", average_quote=1.0,
                       closing_quote=1.0)])
    with harness(move_threshold=None, **prev) as (m, w):
        why = _refused(_sheet(m).validate)
        assert why and "Close Settings" in why


def test_constant_compares_with_the_previous_year():
    prev = _prev([dict(from_currency="EUR", to_currency="USD", quoted_per="1", rate=0.5)],
                 basis="Constant", fy=2024, fp=None, name="RS-C-2024")
    with harness(**prev) as (m, w):
        why = _refused(_sheet(m, rate_basis="Constant", fiscal_period=None).validate)
        assert why and "RS-C-2024" in why
        q = next(s for s in w.sql if "ORDER BY fiscal_year DESC" in s)
        assert "fiscal_year < %s" in q and "fiscal_period" not in q


def test_a_migrated_sheet_needs_no_move_reason():
    prev = _prev([dict(from_currency="EUR", to_currency="USD", quoted_per="1", average_quote=0.5,
                       closing_quote=0.5)])
    with harness(flags={"konsol_migrating_rates": True}, user="Administrator", **prev) as (m, w):
        _sheet(m, rows=[_row(source="Migrated")]).validate()


# == T8: supersede ===========================================================================

def _approved(name="RS-S-2025-06", basis="Statutory", fy=2025, fp=6, docstatus=1):
    return {name: dict(rate_basis=basis, fiscal_year=fy, fiscal_period=fp, docstatus=docstatus)}


def test_a_second_sheet_for_the_key_is_refused_by_a_locking_read():
    with harness(sheets=_approved()) as (m, w):
        why = _refused(_sheet(m, name="RS-S-2025-06-2").validate)
        assert why and "RS-S-2025-06" in why and "supersede" in why
        assert w.thrown[-1] == "DuplicateEntryError"
        q = next(s for s in w.sql if "docstatus < 2" in s)
        assert "FOR UPDATE" in q


def test_a_second_draft_is_refused_too():
    with harness(sheets=_approved(docstatus=0)) as (m, w):
        assert _refused(_sheet(m, name="RS-S-2025-06-2").validate)


def test_the_key_read_is_taken_after_the_year_lock():
    with harness() as (m, w):
        _sheet(m).validate()
        year = next(i for i, s in enumerate(w.sql) if "FROM `tabEPM Fiscal Year` WHERE" in s)
        key = next(i for i, s in enumerate(w.sql) if "docstatus < 2" in s)
        assert year < key and "LOCK IN SHARE MODE" in w.sql[year]


def test_a_correction_names_the_approved_sheet_and_says_why():
    with harness(sheets=_approved()) as (m, w):
        d = _sheet(m, name="RS-S-2025-06-2", supersedes="RS-S-2025-06", change_reason="wrong GBP")
        d.validate()
    with harness(sheets=_approved()) as (m, w):
        why = _refused(_sheet(m, name="RS-S-2025-06-2", supersedes="RS-S-2025-06").validate)
        assert why and "Reason for Change" in why
        assert w.thrown[-1] == "MandatoryError"


def test_supersedes_must_be_the_approved_sheet_of_the_same_key():
    with harness(sheets=_approved(docstatus=0)) as (m, w):
        assert _refused(_sheet(m, name="RS-S-2025-06-2", supersedes="RS-S-2025-06", change_reason="x").validate)
    with harness(sheets=_approved(name="RS-S-2025-05", fp=5)) as (m, w):
        assert _refused(_sheet(m, name="RS-S-2025-06-2", supersedes="RS-S-2025-05", change_reason="x").validate)
    with harness(sheets=_approved(name="RS-C-2025", basis="Constant", fp=0)) as (m, w):
        assert _refused(_sheet(m, supersedes="RS-C-2025", change_reason="x").validate)
    with harness() as (m, w):
        assert _refused(_sheet(m, supersedes="RS-S-2025-06", change_reason="x").validate)  # itself


def test_a_correction_beside_another_draft_is_refused():
    sheets = dict(_approved(), **_approved(name="RS-S-2025-06-2", docstatus=0))
    with harness(sheets=sheets) as (m, w):
        assert _refused(_sheet(m, name="RS-S-2025-06-3", supersedes="RS-S-2025-06",
                               change_reason="x").validate)


def test_constant_key_ignores_the_stored_period():
    """A Constant sheet's period is stored as 0; its key is (basis, year)."""
    sheets = _approved(name="RS-C-2025", basis="Constant", fp=0)
    with harness(sheets=sheets) as (m, w):
        d = _sheet(m, name="RS-C-2025-2", rate_basis="Constant", fiscal_period=None,
                   supersedes="RS-C-2025", change_reason="wrong GBP")
        d.validate()
        q = next(s for s in w.sql if "docstatus < 2" in s)
        assert "fiscal_period" not in q
    with harness(sheets=sheets) as (m, w):
        assert _refused(_sheet(m, name="RS-C-2025-2", rate_basis="Constant", fiscal_period=None).validate)


def test_an_amendment_needs_a_reason():
    with harness() as (m, w):
        why = _refused(_sheet(m, name="RS-S-2025-06-1", amended_from="RS-S-2025-06").validate)
        assert why and "Reason for Change" in why


# == T8: locking =============================================================================

def test_a_saved_sheet_locks_its_fiscal_year_before_its_own_row():
    with harness() as (m, w):
        d = _before(_sheet(m))
        d.check_if_latest()
        assert "LOCK IN SHARE MODE" in w.sql[0] and "`tabEPM Fiscal Year`" in w.sql[0]
        assert w.sql[1] == "BASE_CHECK_IF_LATEST"
    with harness() as (m, w):
        _sheet(m).check_if_latest()   # new: Frappe locks nothing, nor does the sheet
        assert w.sql == ["BASE_CHECK_IF_LATEST"]


def test_the_key_index_backs_the_locking_reads():
    with harness() as (m, w):
        m.on_doctype_update()
        (args, kw), = w.indexes
        assert args == ("Rate Sheet", ["rate_basis", "fiscal_year", "fiscal_period"])


# == T9: before_submit =======================================================================

def test_lifecycle_statutory_approval_needs_an_open_period():
    with harness() as (m, w):
        _sheet(m).before_submit()
        assert w.gates[0][:3] == ("assert_open", 2025, 6)
    with harness(period_open=False) as (m, w):
        assert _refused(_sheet(m).before_submit)


def test_lifecycle_constant_approval_is_refused_once_the_year_is_closed():
    with harness() as (m, w):
        _sheet(m, rate_basis="Constant", fiscal_period=None).before_submit()
        assert [g[0] for g in w.gates] == ["assert_year_not_closed"]
    with harness(year_open=False) as (m, w):
        assert _refused(_sheet(m, rate_basis="Constant", fiscal_period=None).before_submit)


def test_lifecycle_the_migration_approves_into_a_closed_period():
    with harness(period_open=False, flags={"konsol_migrating_rates": True}, user="Administrator") as (m, w):
        _sheet(m, rows=[_row(source="Migrated")]).before_submit()
        assert not w.gates


def test_lifecycle_a_second_approved_sheet_is_refused_by_a_locking_read():
    with harness(sheets=_approved()) as (m, w):
        why = _refused(_sheet(m, name="RS-S-2025-06-2").before_submit)
        assert why and "RS-S-2025-06" in why
        assert w.thrown[-1] == "DuplicateEntryError"
        q = next(s for s in w.sql if "docstatus = 1" in s and "FOR UPDATE" in s)
        assert "name != %s" in q


def test_lifecycle_the_superseded_sheet_is_exempt_but_must_still_be_approved():
    with harness(sheets=_approved()) as (m, w):
        _sheet(m, name="RS-S-2025-06-2", supersedes="RS-S-2025-06", change_reason="x").before_submit()
    with harness() as (m, w):
        why = _refused(_sheet(m, name="RS-S-2025-06-2", supersedes="RS-S-2025-06",
                              change_reason="x").before_submit)
        assert why and "no longer approved" in why


# == T9: on_submit / on_cancel =================================================================

def test_lifecycle_approving_a_correction_cancels_the_superseded_sheet_through_its_cancel():
    with harness() as (m, w):
        _sheet(m, name="RS-S-2025-06-2", supersedes="RS-S-2025-06", docstatus=1).on_submit()
        (name, flags, ignored), = w.cancelled
        assert name == "RS-S-2025-06"
        assert flags.get("superseded_by") == "RS-S-2025-06-2"   # cancel_event.record reads it
        assert ignored == ["Rate Sheet"]
        assert _queued_publish(w)


def test_lifecycle_a_plain_approval_cancels_nothing_and_publishes():
    with harness() as (m, w):
        _sheet(m, docstatus=1).on_submit()
        assert not w.cancelled and _queued_publish(w)


def test_lifecycle_a_draft_save_publishes_nothing():
    with harness() as (m, w):
        _sheet(m).validate()
        assert not w.queued and not w.published


def test_lifecycle_cancel_publishes_after_the_commit():
    with harness() as (m, w):
        _sheet(m, docstatus=2).on_cancel()
        assert _queued_publish(w)


# == T9: before_cancel =========================================================================

def test_lifecycle_a_plain_cancel_of_a_statutory_sheet_is_refused():
    with harness() as (m, w):
        why = _refused(_sheet(m, docstatus=1).before_cancel)
        assert why and "supersed" in why
        assert not w.gates, "refused before the period gate: a reopen would not change it"
        assert not w.cancel_state


def test_lifecycle_the_supersede_cancel_runs_the_period_gate_then_sets_the_state():
    with harness() as (m, w):
        d = _sheet(m, docstatus=1)
        d.flags.superseded_by = "RS-S-2025-06-2"
        d.before_cancel()
        assert w.gates[0][:3] == ("assert_open", 2025, 6)
        assert w.cancel_state == [("RS-S-2025-06", w.gates)], "set_cancelled_state last"
    with harness(period_open=False) as (m, w):
        d = _sheet(m, docstatus=1)
        d.flags.superseded_by = "RS-S-2025-06-2"
        assert _refused(d.before_cancel)


def test_lifecycle_a_constant_sheet_cancels_while_its_year_is_open():
    with harness() as (m, w):
        _sheet(m, rate_basis="Constant", fiscal_period=None, docstatus=1).before_cancel()
        assert [g[0] for g in w.gates] == ["assert_year_not_closed"]
        assert w.cancel_state
    with harness(year_open=False) as (m, w):
        assert _refused(_sheet(m, rate_basis="Constant", fiscal_period=None, docstatus=1).before_cancel)


# == T9: on_trash ==============================================================================

def test_lifecycle_a_cancelled_sheet_is_kept_and_a_draft_can_be_deleted():
    with harness() as (m, w):
        assert _refused(_sheet(m, docstatus=2).on_trash)
        assert w.thrown[-1] == "PermissionError"
        _sheet(m, docstatus=0).on_trash()


# == T9: publish after the commit ================================================================

def _queued_publish(w):
    """One resync_staging queued after the commit, nothing published yet; run
    the queue and publish_rates runs once."""
    assert [k for k, _ in w.queued] == [("resync_staging", "Rate Sheet")], w.queued
    assert not w.published, "queued, not run in the transaction"
    w.queued[0][1]()
    assert w.published == [("publish_rates", False)]
    return True


def test_lifecycle_resync_staging_is_the_one_publish_entry_point():
    with harness() as (m, w):
        assert m.RateSheet.CH_STAGING_TABLE == "epm_staging.group_exchange_rates"
        assert m.RateSheet.CH_STAGING_COLUMNS == ["to_currency", "from_currency", "fiscal_year",
                                                  "fiscal_period", "rate_type", "rate", "document", "rate_set"]
        m.RateSheet.resync_staging(force=True)
        assert w.published == [("publish_rates", True)]


def test_lifecycle_two_approvals_in_one_transaction_publish_once():
    with harness() as (m, w):
        _sheet(m, docstatus=1).on_submit()
        _sheet(m, name="RS-S-2025-07", fiscal_period=7, docstatus=1).on_submit()
        assert len(w.queued) == 1


# == T26b: the magnitude warning (konsol#179, PR #385), ported from Group Exchange Rate ======
#
# GER warned when a quote was more than the declared Magnitude Warning Factor
# (Close Settings) from the ISO references but within the hard 10x: a wrong
# Quoted Per is the usual cause. Past the factor a Reason for Change is
# required; while the factor is undeclared every save says so (msgprint) and
# nothing is refused. The rule itself is group_rates.magnitude_warning
# (test_group_rates_rules); these tests pin the controller's use of it, per
# row and per quote.

#: JPY -> USD quoted per 10 where 100 was meant (0.990 decades off, #179).
JPY_WARNING = ("0.6607 USD per 10 JPY is about 9.8x above the roughly 0.676 USD per 100 JPY the ISO "
               "Currency references imply: more than the Magnitude Warning Factor (3x, Close Settings). "
               "A wrong Quoted Per is the likely cause: did you mean 0.6607 USD per 100 JPY? "
               "If the rate is right, say why (Reason for Change).")
JPY_PER_10 = {("JPY", "USD", 0.6607, 10): JPY_WARNING}


def _jpy(**fields):
    return _row(**dict(dict(from_currency="JPY", quoted_per="10", average_quote=0.6607,
                            closing_quote=0.6607), **fields))


def _jpy_c(**fields):
    return _crow(**dict(dict(from_currency="JPY", quoted_per="10", rate=0.6607), **fields))


def test_magnitude_an_undeclared_factor_is_reported_never_refused():
    with harness(warnings=JPY_PER_10) as (m, w):
        _sheet(m, rows=[_jpy()]).validate()
        assert any("Magnitude Warning Factor" in msg and "Close Settings" in msg for msg in w.msgprints), \
            w.msgprints


def test_magnitude_a_declared_factor_needs_a_reason_for_a_wrong_unit():
    with harness(magnitude_factor=3.0, warnings=JPY_PER_10) as (m, w):
        why = _refused(_sheet(m, rows=[_jpy()]).validate)
        assert why and w.thrown[-1] == "MandatoryError"
        assert "0.6607 USD per 100 JPY" in why and "Reason for Change" in why, why
        assert "Average" in why and "Closing" in why, "each quote of a Statutory row is named"
        assert ("JPY", "USD", 0.6607, 10, 3.0) in w.warned
    with harness(magnitude_factor=3.0, warnings=JPY_PER_10) as (m, w):
        _sheet(m, rows=[_jpy()], change_reason="Checked: this source quotes per 10").validate()
        assert not w.msgprints
    with harness(magnitude_factor=3.0, warnings=JPY_PER_10) as (m, w):
        _sheet(m, rows=[_jpy(quoted_per="100")]).validate()
        assert not w.msgprints, "an in-line rate needs no reason and warns nothing"


def test_magnitude_each_row_and_quote_is_checked():
    """Only the quote past the factor is named: a sheet's other rows and its
    other quote are not."""
    warnings = {("JPY", "USD", 0.06607, 10): "JPY closing past the factor."}
    with harness(magnitude_factor=3.0, warnings=warnings) as (m, w):
        why = _refused(_sheet(m, rows=[_row(), _jpy(name="row2", closing_quote=0.06607)]).validate)
        assert why and "Closing" in why and "JPY closing past the factor." in why, why
        assert "Average" not in why, why
        assert ("EUR", "USD", 1.08, 1, 3.0) in w.warned and ("EUR", "USD", 1.09, 1, 3.0) in w.warned


def test_magnitude_a_constant_sheet_checks_its_rate():
    with harness(magnitude_factor=3.0, warnings=JPY_PER_10) as (m, w):
        why = _refused(_sheet(m, rate_basis="Constant", fiscal_period=None, rows=[_jpy_c()]).validate)
        assert why and w.thrown[-1] == "MandatoryError" and "0.6607 USD per 100 JPY" in why, why
    with harness(magnitude_factor=3.0, warnings=JPY_PER_10) as (m, w):
        _sheet(m, rate_basis="Constant", fiscal_period=None, rows=[_jpy_c()],
               change_reason="Checked").validate()


def test_magnitude_system_writes_are_not_asked_for_a_reason():
    """The pre-fill has no person to give one (its summary lists the warning;
    the reason is asked for when a person saves or approves the draft), nor
    has Duplicate Previous; the migration records what a period was already
    translated at."""
    prefilled = _jpy(source="ERP pre-fill", erp_average_quote=0.6607, erp_closing_quote=0.6607)
    with harness(magnitude_factor=3.0, warnings=JPY_PER_10,
                 flags={"konsol_prefilling_rate_sheet": True}) as (m, w):
        _sheet(m, rows=[prefilled], source_note="ERP feed").validate()
        assert not w.msgprints
    with harness(magnitude_factor=3.0, warnings=JPY_PER_10,
                 flags={"konsol_duplicating_rate_sheet": True}) as (m, w):
        _sheet(m, rows=[_jpy(source="Duplicated")], copied_from="RS-S-2025-05",
               source_note="Duplicated from RS-S-2025-05").validate()
    with harness(magnitude_factor=3.0, warnings=JPY_PER_10, flags={"konsol_migrating_rates": True},
                 user="Administrator") as (m, w):
        _sheet(m, rows=[_jpy(source="Migrated", legacy_documents="GER-1")]).validate()
    with harness(warnings=JPY_PER_10, flags={"konsol_prefilling_rate_sheet": True}) as (m, w):
        _sheet(m, rows=[_jpy(source="ERP pre-fill", erp_average_quote=0.6607, erp_closing_quote=0.6607)],
               source_note="ERP feed").validate()
        assert not w.msgprints, "the undeclared notice is for a person's save"
    # the same pre-filled draft, saved later by a person, is asked
    with harness(magnitude_factor=3.0, warnings=JPY_PER_10) as (m, w):
        d = _sheet(m, rows=[_jpy(source="ERP pre-fill", erp_average_quote=0.6607, erp_closing_quote=0.6607)],
                   source_note="ERP feed")
        _before(d)
        assert _refused(d.validate)
        assert w.thrown[-1] == "MandatoryError"


# -- PR #385 review: a reason given earlier does not cover a later wrong unit ---------------

REASON = "Rate moved: BoJ intervention"


def test_magnitude_a_reason_from_an_earlier_save_does_not_cover_a_changed_quoted_per():
    with harness(magnitude_factor=3.0, warnings=JPY_PER_10) as (m, w):
        d = _sheet(m, rows=[_jpy()], change_reason=REASON)
        _before(d, rows=[_jpy(quoted_per="100")], change_reason=REASON)
        why = _refused(d.validate)
        assert why and "0.6607 USD per 100 JPY" in why and "given before" in why, why
        assert w.thrown[-1] == "MandatoryError"


def test_magnitude_a_reason_updated_with_the_quote_covers_it():
    with harness(magnitude_factor=3.0, warnings=JPY_PER_10) as (m, w):
        d = _sheet(m, rows=[_jpy()], change_reason="Checked: this source quotes per 10")
        _before(d, rows=[_jpy(quoted_per="100")], change_reason=REASON)
        d.validate()


def test_magnitude_a_reason_saved_with_the_same_quote_still_covers_it():
    with harness(magnitude_factor=3.0, warnings=JPY_PER_10) as (m, w):
        d = _sheet(m, rows=[_jpy()], change_reason=REASON)
        _before(d, change_reason=REASON)
        d.validate()


def test_magnitude_a_reason_does_not_cover_a_row_added_since():
    """The reason was saved with the sheet's other rows; the JPY row is new."""
    with harness(magnitude_factor=3.0, warnings=JPY_PER_10) as (m, w):
        d = _sheet(m, rows=[_row(), _jpy(name="row2")], change_reason=REASON)
        _before(d, rows=[_row()], change_reason=REASON)
        why = _refused(d.validate)
        assert why and "given before" in why, why


def test_magnitude_an_amendment_with_the_amended_reason_does_not_cover_a_changed_unit():
    amended = {"RS-S-2025-06": dict(rate_basis="Statutory", fiscal_year=2025, fiscal_period=6, docstatus=2,
                                    change_reason=REASON)}
    old_rows = {"RS-S-2025-06": [dict(from_currency="JPY", to_currency="USD", quoted_per="100",
                                      average_quote=0.6607, closing_quote=0.6607, source="Manual")]}

    def amend(rows, reason):
        with harness(magnitude_factor=3.0, warnings=JPY_PER_10, sheets=amended, sheet_rows=old_rows) as (m, w):
            return _refused(_sheet(m, name="RS-S-2025-06-1", amended_from="RS-S-2025-06", rows=rows,
                                   change_reason=reason).validate)

    why = amend([_jpy()], REASON)
    assert why and "given before" in why, why
    assert not amend([_jpy()], "Amended: the source quotes per 10")
    assert not amend([_jpy(quoted_per="100")], REASON), "the amendment keeps the amended unit"


# == T26b: a sheet's build scope (konsol#592 D2) ==============================================

def test_build_scope_a_constant_sheet_requests_scenarios():
    """A Constant sheet drives the budget and forecast translation, which the
    scenarios domain builds (gold_consolidated_budget_tb, domain:scenarios); a
    Statutory sheet drives the actuals, so it leaves DOCTYPE_BUILD_MAP's
    "consolidation" in place (None)."""
    with harness() as (m, w):
        assert _sheet(m, rate_basis="Constant", fiscal_period=None).build_scope() == "scenarios"
        assert _sheet(m).build_scope() is None


# == PR #595 review 5: Duplicate Previous while the move threshold is undeclared ==============

DUP = {"konsol_duplicating_rate_sheet": True}


def test_duplicate_previous_on_a_site_with_no_threshold_succeeds():
    """A fresh site declares no Rate Move threshold. Duplicating the approved
    P1 sheet into P2 proposes the rates it copies, a move of 0, so it needs no
    reason whether or not a threshold is declared (GER's #586-review rule)."""
    p1 = _prev([dict(from_currency="EUR", to_currency="USD", quoted_per="1", average_quote=1.08,
                     closing_quote=1.09)], fp=1, name="RS-S-2025-01")
    with harness(move_threshold=None, flags=DUP, **p1) as (m, w):
        d = _sheet(m, name="RS-S-2025-02", fiscal_period=2, rows=[_row(source="Duplicated")],
                   copied_from="RS-S-2025-01", source_note="Duplicated from RS-S-2025-01")
        d.validate()
        assert d.rates[0].source == "Duplicated"


def test_duplicate_previous_compares_true_rates_with_the_copied_sheet():
    """The same rate quoted per another unit is still the copied rate."""
    p1 = _prev([dict(from_currency="EUR", to_currency="USD", quoted_per="10", average_quote=10.8,
                     closing_quote=10.9)], fp=1, name="RS-S-2025-01")
    with harness(move_threshold=None, flags=DUP, **p1) as (m, w):
        _sheet(m, name="RS-S-2025-02", fiscal_period=2, rows=[_row(source="Duplicated")],
               copied_from="RS-S-2025-01", source_note="dup").validate()


def test_a_duplicate_whose_rate_differs_from_the_copy_is_still_checked():
    p1 = _prev([dict(from_currency="EUR", to_currency="USD", quoted_per="1", average_quote=0.5,
                     closing_quote=1.09)], fp=1, name="RS-S-2025-01")
    with harness(move_threshold=None, flags=DUP, **p1) as (m, w):
        why = _refused(_sheet(m, name="RS-S-2025-02", fiscal_period=2, rows=[_row(source="Duplicated")],
                              copied_from="RS-S-2025-01", source_note="dup").validate)
        assert why and "Close Settings" in why
    with harness(flags=DUP, **p1) as (m, w):
        why = _refused(_sheet(m, name="RS-S-2025-02", fiscal_period=2, rows=[_row(source="Duplicated")],
                              copied_from="RS-S-2025-01", source_note="dup").validate)
        assert why and "EUR → USD Average" in why and "RS-S-2025-01" in why


def test_an_unchanged_rate_is_exempt_only_under_duplicate_previous():
    """Without DUPLICATE_FLAG the previous approved sheet is no copy: an
    undeclared threshold is named, as before."""
    p1 = _prev([dict(from_currency="EUR", to_currency="USD", quoted_per="1", average_quote=1.08,
                     closing_quote=1.09)], fp=1, name="RS-S-2025-01")
    with harness(move_threshold=None, **p1) as (m, w):
        why = _refused(_sheet(m, name="RS-S-2025-02", fiscal_period=2).validate)
        assert why and "Close Settings" in why
