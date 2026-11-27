"""report_choices: the entity and group lists the Excel add-in's report pickers
show, and the is_allowed check the server runs on submitted parameters.

Each list is filtered by the same rule the read paths use (entity_permissions
.entity_read_scope, the rule epm_batch applies), so a picker never offers a
code the server would refuse. Loaded under a stub frappe, so it runs on any
host; no pytest fixtures (the host runner skips those).
"""
import contextlib
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Row(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)


ENTITIES = [
    {"entity_code": "US01", "entity_name": "US Co", "is_group": 0,
     "functional_currency": "USD", "status": "Active", "lft": 1, "rgt": 2},
    {"entity_code": "DE01", "entity_name": "DE GmbH", "is_group": 0,
     "functional_currency": "EUR", "status": "Active", "lft": 3, "rgt": 4},
    {"entity_code": "AT01", "entity_name": "AT GmbH", "is_group": 0,
     "functional_currency": "EUR", "status": "Dormant", "lft": 5, "rgt": 6},
]

# Two groups. GRP-A's root sits in DE01 and its member in AT01; GRP-B's root
# sits in US01. A member row carries the root's code and a parent.
GROUPS = [
    {"consolidation_group": "GRP-A", "entity_name": "DE GmbH",
     "data_area_id": "DE01", "parent_consolidation_group": None},
    {"consolidation_group": "GRP-A", "entity_name": "AT GmbH",
     "data_area_id": "AT01", "parent_consolidation_group": "GRP-A"},
    {"consolidation_group": "GRP-B", "entity_name": "US Co",
     "data_area_id": "US01", "parent_consolidation_group": None},
]


def P(year, num, code, label, ptype="Regular", q="Q1", sd="2025-01-01", ed="2025-01-31"):
    return {"parent": str(year), "fiscal_period": num, "period_code": code,
            "period_label": label, "period_type": ptype, "quarter": q,
            "start_date": sd, "end_date": ed}


# 2024: Opening + 12 Regular + Closing. 2025: 13 Regular (label blank on one).
# 2023: Opening only (no Regular), so it is not a report year.
PERIODS = (
    [P(2023, 0, "OPEN", "Opening", "Opening")]
    + [P(2024, 0, "OPEN", "Opening", "Opening")]
    + [P(2024, n, f"P{n:02d}", f"Period {n}", sd=f"2024-{n:02d}-01", ed=f"2024-{n:02d}-28")
       for n in range(12, 0, -1)]
    + [P(2024, 13, "CLOSE", "Closing", "Closing"), P(2024, 14, "ADJ", "Adj", "Adjustment")]
    + [P(2025, n, f"P{n:02d}", "" if n == 13 else f"P{n}", sd=f"2025-{n:02d}-01",
         ed=f"2025-{n:02d}-28" if n < 13 else "2026-01-05")
       for n in range(1, 14)]
)

HIERARCHIES = [
    {"hierarchy_name": "REG", "label": "Regions", "dimension": "Region", "status": "Published"},
    {"hierarchy_name": "DRAFT", "label": "Draft", "dimension": "Region", "status": "Draft"},
]

# Tree as get_reporting_hierarchy_tree returns it (roots, nested children).
def _node(code, label, group, children=()):
    return {"member_code": code, "member_label": label, "is_group": group,
            "children": list(children)}

TREE = {"tree": [
    _node("ALL", "All", True, [
        _node("EMEA", "EMEA", True, [_node("DE", "Germany", False), _node("AT", "Austria", False)]),
        _node("US", "United States", False),
    ]),
    _node("OTHER", "Other", False),
]}

ACCOUNTS = [
    {"main_account": "4000", "account_name": "Revenue", "chart_of_accounts": "GRP",
     "is_group": 0, "status": "Published", "statement_section": "Profit and Loss",
     "account_type": "Revenue", "parent_account": "4"},
    {"main_account": "4", "account_name": "Income", "chart_of_accounts": "GRP",
     "is_group": 1, "status": "Published", "statement_section": "Profit and Loss",
     "account_type": "Revenue", "parent_account": None},
    {"main_account": "1000", "account_name": "Cash", "chart_of_accounts": "GRP",
     "is_group": 0, "status": "Published", "statement_section": "Balance Sheet",
     "account_type": "Bank", "parent_account": "1"},
    {"main_account": "9999", "account_name": "Draft acct", "chart_of_accounts": "GRP",
     "is_group": 0, "status": "Draft", "statement_section": "Profit and Loss",
     "account_type": "Expense", "parent_account": None},
]


def _matches(row, filters):
    for key, cond in (filters or {}).items():
        if isinstance(cond, (list, tuple)):
            op, val = cond
            if op == "in" and row.get(key) not in val:
                return False
        elif row.get(key) != cond:
            return False
    return True


def _fake_frappe(user, roles, perms, accounts=None):
    fake = types.ModuleType("frappe")
    fake.PermissionError = type("PermissionError", (Exception,), {})
    fake.session = types.SimpleNamespace(user=user)
    fake.get_roles = lambda u=None: list(roles)
    fake.permissions = types.SimpleNamespace(
        get_user_permissions=lambda u=None: {"Entity": [{"doc": p} for p in perms]})
    fake.get_cached_value = lambda *a, **k: None
    fake.db = types.SimpleNamespace(sql=lambda *a, **k: [])

    def throw(msg, exc=None):
        raise (exc or Exception)(msg)

    fake.throw = throw
    fake.ValidationError = type("ValidationError", (Exception,), {})
    fake.DIM_CALLS = []

    def get_all(doctype, filters=None, fields=None, order_by=None,
                pluck=None, limit_page_length=None, **k):
        # The picker lists must say their order: get_all sorts by creation
        # otherwise. The subtree lookup (lft/rgt) is order-free and is skipped.
        if not (fields and "lft" in fields):
            assert order_by, f"get_all({doctype}) without order_by"
        if doctype == "Entity":
            rows = ENTITIES
            if filters and "name" in filters:
                _, names = filters["name"]
                rows = [r for r in rows if r["entity_code"] in names]
            rows = [_Row({**r, "name": r["entity_code"]}) for r in rows]
        elif doctype == "Consolidation Group":
            rows = [_Row({**r, "name": f"CG-{r['consolidation_group']}-{r['data_area_id']}"})
                    for r in GROUPS]
        elif doctype == "EPM Fiscal Year Period":
            assert k.get("parent_doctype") == "EPM Fiscal Year"
            rows = [_Row(r) for r in PERIODS if _matches(r, filters)]
        elif doctype == "Reporting Hierarchy":
            rows = [_Row({**r, "name": r["hierarchy_name"]}) for r in HIERARCHIES
                    if _matches(r, filters)]
        elif doctype == "Main Account":
            rows = [_Row({**r, "name": r["main_account"]})
                    for r in (accounts if accounts is not None else ACCOUNTS)
                    if _matches(r, filters)]
        elif doctype == "Dimension Value":
            fake.DIM_CALLS.append(filters)
            rows = [_Row({"name": f"Region:{v}", "dimension": "Region", "value": v})
                    for v in ("DACH", "NA", "APAC")
                    if _matches({"dimension": "Region"}, filters)]
        else:
            return []
        # Frappe sorts by order_by; the stub must too, or a picker's order is untested.
        if order_by:
            for clause in reversed(order_by.split(",")):
                key, _, direction = clause.strip().partition(" ")
                rows.sort(key=lambda r: r.get(key) or "",
                          reverse=direction.lower() == "desc")
        return rows

    fake.get_all = get_all
    return fake


@contextlib.contextmanager
def _stubbed(fake):
    """Put the stub frappe, and the real entity_permissions, where the code under
    test imports them, for the length of one call."""
    names = ("frappe", "konsol.entity_permissions")
    saved = {n: sys.modules.get(n) for n in names}
    sys.modules["frappe"] = fake
    spec = importlib.util.spec_from_file_location(
        "_report_choices_ep", os.path.join(APP_DIR, "entity_permissions.py"))
    ep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ep)
    sys.modules["konsol.entity_permissions"] = ep
    try:
        yield
    finally:
        for n, v in saved.items():
            if v is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = v


def _call(name, *args, user="Administrator", roles=(), perms=(), accounts=None, tree=None):
    fake = _fake_frappe(user, roles, perms, accounts)
    with _stubbed(fake):
        api = types.ModuleType("konsol.api")
        api.TREE_CALLS = []

        def get_reporting_hierarchy_tree(hierarchy_name=None, as_of=None):
            api.TREE_CALLS.append((hierarchy_name, as_of))
            return tree or TREE

        api.get_reporting_hierarchy_tree = get_reporting_hierarchy_tree
        sys.modules["konsol.api"] = api
        _call.api = api
        spec = importlib.util.spec_from_file_location(
            "_report_choices_under_test", os.path.join(APP_DIR, "report_choices.py"))
        rc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rc)
        try:
            return getattr(rc, name)(*args)
        finally:
            sys.modules.pop("konsol.api", None)


RESTRICTED = {"user": "viewer", "roles": [], "perms": ["DE01"]}


def test_unrestricted_user_sees_every_entity_sorted_by_code():
    rows = _call("entities")
    assert [r["code"] for r in rows] == ["AT01", "DE01", "US01"]
    first = rows[0]
    assert set(first) == {"code", "name", "is_group", "currency", "status"}
    assert first["name"] == "AT GmbH"
    assert first["currency"] == "EUR"
    assert first["status"] == "Dormant"


def test_restricted_user_sees_only_allowed_entities():
    rows = _call("entities", **RESTRICTED)
    assert [r["code"] for r in rows] == ["DE01"]


def test_unrestricted_user_sees_every_group_code_once():
    rows = _call("groups")
    assert [r["code"] for r in rows] == ["GRP-A", "GRP-B"]
    assert len({r["code"] for r in rows}) == len(rows)


def test_group_name_comes_from_the_root_row():
    rows = {r["code"]: r for r in _call("groups")}
    assert rows["GRP-A"]["name"] == "DE GmbH"
    assert rows["GRP-B"]["name"] == "US Co"


def test_restricted_user_sees_only_groups_whose_root_entity_is_allowed():
    rows = _call("groups", **RESTRICTED)
    assert [r["code"] for r in rows] == ["GRP-A"]


def test_is_allowed_accepts_listed_code_and_refuses_unknown():
    assert _call("is_allowed", "entity", "DE01") is True
    assert _call("is_allowed", "entity", "ZZ99") is False
    assert _call("is_allowed", "group", "GRP-A") is True
    assert _call("is_allowed", "group", "GRP-NOPE") is False


def test_is_allowed_applies_the_reader_permission():
    assert _call("is_allowed", "entity", "US01", **RESTRICTED) is False
    assert _call("is_allowed", "group", "GRP-B", **RESTRICTED) is False
    assert _call("is_allowed", "group", "GRP-A", **RESTRICTED) is True


def test_is_allowed_refuses_an_unknown_kind():
    try:
        _call("is_allowed", "report", "DE01")
    except ValueError:
        return
    raise AssertionError("an unknown kind must be refused, not answered")


# --- T7: years, periods, hierarchies, dimension values, accounts ----------------------

def test_fiscal_years_are_those_with_a_regular_period_ascending():
    assert _call("fiscal_years") == [2024, 2025]


def test_periods_are_regular_only_in_fiscal_period_order():
    rows = _call("periods", 2024)
    assert [r["number"] for r in rows] == list(range(1, 13))
    assert set(rows[0]) == {"number", "code", "label", "start_date", "end_date", "quarter"}
    assert rows[0]["code"] == "P01" and rows[0]["label"] == "Period 1"
    assert rows[0]["start_date"] == "2024-01-01" and rows[0]["end_date"] == "2024-01-28"


def test_a_13_period_year_returns_13():
    assert [r["number"] for r in _call("periods", 2025)] == list(range(1, 14))


def test_period_label_falls_back_to_the_code():
    assert _call("periods", 2025)[12]["label"] == "P13"


def test_a_year_with_no_regular_period_is_empty():
    assert _call("periods", 2023) == []
    assert _call("periods", 1999) == []


def test_hierarchies_are_published_only():
    assert _call("hierarchies") == [
        {"name": "REG", "label": "Regions", "dimension": "Region"}]


def test_hierarchy_nodes_flatten_in_tree_order_with_depth_and_parent():
    rows = _call("hierarchy_nodes", "REG", 2025)
    assert [(r["code"], r["depth"], r["parent"]) for r in rows] == [
        ("ALL", 0, None), ("EMEA", 1, "ALL"), ("DE", 2, "EMEA"), ("AT", 2, "EMEA"),
        ("US", 1, "ALL"), ("OTHER", 0, None)]
    assert rows[0] == {"code": "ALL", "label": "All", "depth": 0, "is_group": True,
                       "parent": None}
    assert rows[2]["is_group"] is False


def test_hierarchy_nodes_read_the_tree_as_of_the_years_last_regular_period_end():
    _call("hierarchy_nodes", "REG", 2025)
    assert _call.api.TREE_CALLS == [("REG", "2026-01-05")]
    _call("hierarchy_nodes", "REG", 2024)
    assert _call.api.TREE_CALLS == [("REG", "2024-12-28")]


def test_hierarchy_nodes_of_a_year_with_no_regular_period_is_refused():
    try:
        _call("hierarchy_nodes", "REG", 2023)
    except Exception as exc:
        assert "Regular" in str(exc)
        return
    raise AssertionError("no Regular period means no as-of date")


def test_dimension_values_go_through_the_dimension_value_list():
    rows = _call("dimension_values", "Region")
    assert [r["code"] for r in rows] == ["APAC", "DACH", "NA"]
    assert _call("dimension_values", "Nope") == []


def test_accounts_are_published_leaves_in_code_order():
    rows = _call("accounts")
    assert [r["code"] for r in rows] == ["1000", "4000"]
    assert rows[1] == {"code": "4000", "name": "Revenue",
                       "statement_section": "Profit and Loss",
                       "account_type": "Revenue", "parent": "4"}


def test_accounts_filter_by_section():
    assert [r["code"] for r in _call("accounts", "Profit and Loss")] == ["4000"]


def test_account_headings_are_the_published_groups():
    assert [r["code"] for r in _call("account_headings")] == ["4"]
    assert _call("account_headings", "Balance Sheet") == []


def test_accounts_refuse_more_than_one_chart():
    two = [dict(ACCOUNTS[0]), {**ACCOUNTS[2], "chart_of_accounts": "OTHER"}]
    try:
        _call("accounts", accounts=two)
    except Exception as exc:
        assert "one chart" in str(exc)
        return
    raise AssertionError("leaves in two charts must be refused")


def test_guest_is_refused_by_every_kind():
    calls = [("entities",), ("groups",), ("fiscal_years",), ("periods", 2025),
             ("hierarchies",), ("hierarchy_nodes", "REG", 2025),
             ("dimension_values", "Region"), ("accounts",), ("account_headings",),
             ("is_allowed", "fiscal_year", 2025)]
    for c in calls:
        try:
            _call(*c, user="Guest")
        except Exception as exc:
            assert type(exc).__name__ == "PermissionError", (c, exc)
        else:
            raise AssertionError(f"{c} answered a Guest")


def test_is_allowed_covers_the_new_kinds():
    assert _call("is_allowed", "fiscal_year", 2025) is True
    assert _call("is_allowed", "fiscal_year", "2025") is True
    assert _call("is_allowed", "fiscal_year", 2023) is False
    assert _call("is_allowed", "hierarchy", "REG") is True
    assert _call("is_allowed", "hierarchy", "DRAFT") is False
    assert _call("is_allowed", "account", "4000") is True
    assert _call("is_allowed", "account", "4") is False
    assert _call("is_allowed", "account_heading", "4") is True
    assert _call("is_allowed", "dimension_value", "Region:DACH") is True
    assert _call("is_allowed", "dimension_value", "Region:XX") is False
    assert _call("is_allowed", "dimension_value", "nocolon") is False
