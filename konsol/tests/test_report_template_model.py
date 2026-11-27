"""Report template files: load and validate (konsol#548 v1, T2).

Templates are JSON files in konsol/report_templates/<id>.json (Deepak Pai,
27 Nov 2026, Q2). konsol/report_template_model.py is pure: it loads them and
refuses every broken rule by name (file, key path, rule). Its module docstring
is the schema. Every case in INVALID breaks exactly one rule of a valid
template and asserts the message that names it.
"""
import copy
import importlib.util
import json
import os
import tempfile

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(APP_DIR, rel))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


model = _load("konsol_report_template_model", "report_template_model.py")


FORMULAS = {
    "id": "zz_min",
    "version": 1,
    "order": 10,
    "title": "ZZ Min",
    "description": "A minimal formulas template.",
    "mode": "formulas",
    "sheet_name": "ZZ Min",
    "params": [
        {"name": "scope", "type": "scope", "kinds": ["entity", "group"], "required": True},
        {"name": "year", "type": "fiscal_year", "required": True, "label": "Fiscal Year"},
        {"name": "period", "type": "period", "depends_on": ["year"]},
        {"name": "budget", "type": "scenario", "scenario_type": "budget",
         "depends_on": ["year"], "default": "rule"},
        {"name": "sign", "type": "choice", "choices": ["debit_positive", "credit_positive"],
         "default": "debit_positive"},
        {"name": "note", "type": "text", "default": "", "echo_only": True},
    ],
    "sign": {"param": "sign", "sentences": {"debit_positive": "Debits are positive.",
                                            "credit_positive": "Credits are positive."}},
    "header": [
        {"at": "A1", "text": "{title}: {scope.name} ({scope.kind}) {note}"},
        {"at": "C1", "param": "year"},
    ],
    "series": {
        "act": {"caption": "Actual", "read": {"entity": "TB", "group": "CTB"}},
        "bud": {"caption": "Budget", "read": {"entity": "BUD", "group": "BUD"},
                "scenario_id": "{budget}", "measure": "amount"},
        "var": {"caption": "Variance", "formula": "act - bud"},
    },
    "columns": {
        "start_col": "B",
        "header_rows": [2, 3],
        "groups": [
            {"over": "regular_periods", "label": "{month_abbrev}", "series": ["act", "bud", "var"]},
            {"over": "ytd", "label": "YTD {period.code}", "series": ["act", "bud"],
             "through": "period"},
        ],
    },
    "rows": {
        "start_row": 4,
        "blocks": [
            {"id": "rev", "type": "accounts",
             "filter": {"statement_section": "Profit and Loss", "account_type": ["Income"]},
             "caption": "{account.name}"},
            {"id": "exp", "type": "accounts", "filter": {"account_type": ["Expense"]},
             "one_chart": True, "caption": "{account.code} {account.name}"},
            {"type": "blank"},
            {"id": "net", "type": "total", "of": ["rev", "exp"], "caption": "Net {sign.sentence}",
             "bold": True},
            {"type": "heading", "caption": "{param.year}"},
        ],
    },
    "number_format": {"default": "#,##0", "var": "#,##0;[Red]-#,##0"},
    "errors": {"no_data": "No data for {scope.code}."},
}

SNAPSHOT = {
    "id": "zz_snap",
    "version": 1,
    "order": 20,
    "title": "ZZ Snap",
    "description": "A minimal snapshot template.",
    "mode": "snapshot",
    "sheet_name": "ZZ TB",
    "params": [
        {"name": "entity", "type": "entity", "required": True},
        {"name": "year", "type": "fiscal_year", "required": True},
        {"name": "period", "type": "period", "depends_on": ["year"]},
    ],
    "header": [{"at": "A1", "text": "{title} {period.code}"}],
    "source": "entity_trial_balance",
    "max_rows": 50000,
    "rows": {"start_row": 3, "header_row": True},
    "fields": [
        {"h": "Account", "field": "account"},
        {"h": "Balance", "field": "balance", "number": True},
        {"h": "Entity", "field": "entity", "default": "{entity}"},
    ],
}


def _set(path, value):
    def mutate(t):
        node = t
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
    return mutate


def _delete(path):
    def mutate(t):
        node = t
        for key in path[:-1]:
            node = node[key]
        del node[path[-1]]
    return mutate


def _append(path, value):
    def mutate(t):
        node = t
        for key in path:
            node = node[key]
        node.append(value)
    return mutate


def _ytd_not_in_regular(t):
    t["columns"]["groups"][0]["series"] = ["act"]


# (case, base, mutate, substrings the message must contain)
INVALID = [
    ("unknown top-level key", FORMULAS, _set(["colour"], "red"),
     ["colour", "unknown top-level key"]),
    ("missing required top-level key", FORMULAS, _delete(["sheet_name"]),
     ["sheet_name", "is required"]),
    ("version not an int", FORMULAS, _set(["version"], "1"), ["version", "must be an integer"]),
    ("order not an int", FORMULAS, _set(["order"], True), ["order", "must be an integer"]),
    ("unknown mode", FORMULAS, _set(["mode"], "pivot"), ["mode", "must be one of"]),
    ("unknown block type", FORMULAS, _set(["rows", "blocks", 0, "type"], "chart"),
     ["rows.blocks[0].type", "unknown block type 'chart'"]),
    ("unknown key in a block", FORMULAS, _set(["rows", "blocks", 0, "colour"], "x"),
     ["rows.blocks[0].colour", "unknown key"]),
    ("duplicate block id", FORMULAS, _set(["rows", "blocks", 1, "id"], "rev"),
     ["rows.blocks[1].id", "used twice"]),
    ("unknown accounts filter key", FORMULAS, _set(["rows", "blocks", 0, "filter", "colour"], "x"),
     ["rows.blocks[0].filter.colour", "unknown key"]),
    ("unknown param type", FORMULAS, _set(["params", 0, "type"], "colour"),
     ["params[0].type", "unknown param type 'colour'"]),
    ("unknown key in a param", FORMULAS, _set(["params", 1, "kinds"], ["entity"]),
     ["params[1].kinds", "unknown key"]),
    ("param name not an identifier", FORMULAS, _set(["params", 5, "name"], "my note"),
     ["params[5].name", "not an identifier"]),
    ("param name used twice", FORMULAS, _set(["params", 2, "name"], "year"),
     ["params[2].name", "'year' is used twice"]),
    ("depends_on an unknown param", FORMULAS, _set(["params", 2, "depends_on"], ["yr"]),
     ["params[2].depends_on[0]", "unknown param 'yr'"]),
    ("period depends_on a non-year param", FORMULAS, _set(["params", 2, "depends_on"], ["sign"]),
     ["params[2].depends_on[0]", "not a fiscal_year param"]),
    ("scope kinds outside entity/group/node", FORMULAS, _set(["params", 0, "kinds"], ["company"]),
     ["params[0].kinds", "entity, group, node"]),
    ("choice default not a choice", FORMULAS, _set(["params", 4, "default"], "both"),
     ["params[4].default", "not one of the choices"]),
    ("scenario_type unknown", FORMULAS, _set(["params", 3, "scenario_type"], "plan"),
     ["params[3].scenario_type", "must be one of"]),
    ("unknown column group", FORMULAS, _set(["columns", "groups", 0, "over"], "weeks"),
     ["columns.groups[0].over", "unknown column group 'weeks'"]),
    ("ytd without its regular_periods series", FORMULAS, _ytd_not_in_regular,
     ["columns.groups[1].series", "no regular_periods group carries series 'bud'"]),
    ("ytd through not a period param", FORMULAS, _set(["columns", "groups", 1, "through"], "year"),
     ["columns.groups[1].through", "'year' is not a period param"]),
    ("through on a non-ytd group", FORMULAS, _set(["columns", "groups", 0, "through"], "period"),
     ["columns.groups[0].through", "only a ytd group"]),
    ("column group naming an unknown series", FORMULAS,
     _set(["columns", "groups", 0, "series"], ["act", "fc"]),
     ["columns.groups[0].series[1]", "unknown series 'fc'"]),
    ("start_col not a column", FORMULAS, _set(["columns", "start_col"], "B2"),
     ["columns.start_col", "not a column letter"]),
    ("header_rows of three", FORMULAS, _set(["columns", "header_rows"], [2, 3, 4]),
     ["columns.header_rows", "one or two"]),
    ("formula naming an unknown series", FORMULAS, _set(["series", "var", "formula"], "act - fc"),
     ["series.var.formula", "unknown series 'fc'"]),
    ("formula naming itself", FORMULAS, _set(["series", "var", "formula"], "var - act"),
     ["series.var.formula", "references itself"]),
    ("formula cycle", FORMULAS, _set(["series", "bud"], {"caption": "B", "formula": "var + act"}),
     ["series", "cycle"]),
    ("series with read and formula", FORMULAS, _set(["series", "var", "read"], {"entity": "TB"}),
     ["series.var", "exactly one of read or formula"]),
    ("series missing a read word for a scope kind", FORMULAS, _delete(["series", "act", "read", "group"]),
     ["series.act.read", "no read word for kind 'group'"]),
    ("series scenario_id not a scenario param", FORMULAS,
     _set(["series", "bud", "scenario_id"], "{year}"),
     ["series.bud.scenario_id", "not a scenario param"]),
    ("total of an unknown block", FORMULAS, _set(["rows", "blocks", 3, "of"], ["rev", "zzz"]),
     ["rows.blocks[3].of[1]", "unknown block 'zzz'"]),
    ("total of a total", FORMULAS,
     _append(["rows", "blocks"], {"id": "net2", "type": "total", "of": ["net"], "caption": "N"}),
     ["rows.blocks[5].of[0]", "'net' is a total"]),
    ("account_list param of the wrong type", FORMULAS,
     _append(["rows", "blocks"], {"type": "account_list", "param": "year", "caption": "x"}),
     ["rows.blocks[5].param", "not an account_list param"]),
    ("account_tree root param of the wrong type", FORMULAS,
     _append(["rows", "blocks"], {"type": "account_tree", "root": "year", "caption": "x"}),
     ["rows.blocks[5].root", "not an account_heading param"]),
    ("hierarchy_nodes hierarchy param of the wrong type", FORMULAS,
     _append(["rows", "blocks"], {"type": "hierarchy_nodes", "hierarchy": "year",
                                  "account": "year", "caption": "x"}),
     ["rows.blocks[5].hierarchy", "not a hierarchy param"]),
    ("header cell not A1-style", FORMULAS, _set(["header", 0, "at"], "R1C1"),
     ["header[0].at", "not an A1-style cell"]),
    ("header param unknown", FORMULAS, _set(["header", 1, "param"], "yr"),
     ["header[1].param", "unknown param 'yr'"]),
    ("sign param not a choice param", FORMULAS, _set(["sign", "param"], "year"),
     ["sign.param", "'year' is not a choice param"]),
    ("sign sentences not the choices", FORMULAS,
     _set(["sign", "sentences"], {"debit_positive": "D", "both": "B"}),
     ["sign.sentences", "do not equal the choices"]),
    ("sign param choices not conventions", FORMULAS,
     _set(["params", 4, "choices"], ["debit", "credit_positive"]),
     ["params[4].choices", "'debit'", "debit_positive, credit_positive"]),
    ("formula series without its sibling in the group", FORMULAS,
     _set(["columns", "groups", 0, "series"], ["act", "var"]),
     ["columns.groups[0].series", "'var'", "'bud'"]),
    ("one header row, several series, no series caption", FORMULAS,
     _set(["columns", "header_rows"], [2]),
     ["columns.groups[0].label", "one header row", "{series.caption}"]),
    ("series.caption token outside a group label", FORMULAS,
     _set(["rows", "blocks", 0, "caption"], "{series.caption}"),
     ["rows.blocks[0].caption", "unknown token '{series.caption}'"]),
    ("sign convention unknown", FORMULAS, _set(["sign"], {"convention": "up", "sentence": "x"}),
     ["sign.convention", "must be one of"]),
    ("unknown token", FORMULAS, _set(["rows", "blocks", 0, "caption"], "{acount.name}"),
     ["rows.blocks[0].caption", "unknown token '{acount.name}'"]),
    ("bare token of an unknown param", FORMULAS, _set(["columns", "groups", 0, "label"], "{nope}"),
     ["columns.groups[0].label", "unknown token '{nope}'"]),
    (".code token on a non-period param", FORMULAS, _set(["series", "act", "caption"], "{year.code}"),
     ["series.act.caption", "unknown token '{year.code}'"]),
    ("number_format for an unknown series", FORMULAS, _set(["number_format", "fc"], "0"),
     ["number_format.fc", "unknown series 'fc'"]),
    ("formulas missing series", FORMULAS, _delete(["series"]),
     ["series", "a formulas template must declare it"]),
    ("formulas missing columns", FORMULAS, _delete(["columns"]),
     ["columns", "a formulas template must declare it"]),
    ("formulas with a snapshot key", FORMULAS, _set(["max_rows"], 10),
     ["max_rows", "only a snapshot template"]),
    ("snapshot missing source", SNAPSHOT, _delete(["source"]),
     ["source", "a snapshot template must declare it"]),
    ("snapshot missing fields", SNAPSHOT, _delete(["fields"]),
     ["fields", "a snapshot template must declare it"]),
    ("snapshot unknown source", SNAPSHOT, _set(["source"], "ledger"),
     ["source", "'ledger' is not a registered snapshot source"]),
    ("snapshot with a formulas key", SNAPSHOT, _set(["series"], {}),
     ["series", "only a formulas template"]),
    ("snapshot field default with an unknown token", SNAPSHOT,
     _set(["fields", 2, "default"], "{account.nam}"),
     ["fields[2].default", "unknown token"]),
    ("snapshot rows with blocks", SNAPSHOT, _set(["rows", "blocks"], []),
     ["rows.blocks", "unknown key"]),
    ("snapshot max_rows not positive", SNAPSHOT, _set(["max_rows"], 0),
     ["max_rows", "positive integer"]),
]


def _problems(base, mutate, stem=None):
    t = copy.deepcopy(base)
    mutate(t)
    return model.validate(t, stem or base["id"])


def test_valid_formulas_template_has_no_problems():
    assert model.validate(copy.deepcopy(FORMULAS), "zz_min") == []


def test_valid_snapshot_template_has_no_problems():
    assert model.validate(copy.deepcopy(SNAPSHOT), "zz_snap") == []


def test_sign_convention_form_is_valid():
    t = copy.deepcopy(FORMULAS)
    t["sign"] = {"convention": "credit_positive", "sentence": "Credits are positive."}
    assert model.validate(t, "zz_min") == []


def test_every_invalid_case_is_refused_by_name():
    failures = []
    for case, base, mutate, expected in INVALID:
        problems = _problems(base, mutate)
        stem = base["id"]
        hits = [p for p in problems if all(s in p for s in expected) and f"{stem}.json" in p]
        if not hits:
            failures.append(f"{case}: expected {expected} in {problems}")
    assert not failures, "\n".join(failures)


def test_id_not_the_file_stem_is_refused():
    problems = model.validate(copy.deepcopy(FORMULAS), "other")
    assert any("other.json" in p and "id" in p and "does not match the file name" in p
               for p in problems), problems


def _write(directory, name, template):
    with open(os.path.join(directory, name), "w") as f:
        json.dump(template, f)


def test_load_templates_sorts_by_order_then_id():
    with tempfile.TemporaryDirectory() as d:
        late = copy.deepcopy(FORMULAS)
        late["id"], late["order"] = "zz_late", 30
        tie = copy.deepcopy(FORMULAS)
        tie["id"], tie["order"] = "zz_aaa", 20
        _write(d, "zz_min.json", FORMULAS)
        _write(d, "zz_snap.json", SNAPSHOT)
        _write(d, "zz_late.json", late)
        _write(d, "zz_aaa.json", tie)
        with open(os.path.join(d, "README.txt"), "w") as f:
            f.write("not a template")
        loaded = model.load_templates(d)
    assert list(loaded) == ["zz_min", "zz_aaa", "zz_snap", "zz_late"]
    assert loaded["zz_snap"]["source"] == "entity_trial_balance"


def test_load_templates_lists_every_problem_in_every_file():
    with tempfile.TemporaryDirectory() as d:
        bad1 = copy.deepcopy(FORMULAS)
        bad1["colour"] = "red"
        bad1["rows"]["blocks"][0]["type"] = "chart"
        bad2 = copy.deepcopy(SNAPSHOT)
        del bad2["source"]
        _write(d, "zz_min.json", bad1)
        _write(d, "zz_snap.json", bad2)
        try:
            model.load_templates(d)
        except model.TemplateError as e:
            text = str(e)
            problems = e.problems
        else:
            raise AssertionError("load_templates accepted broken templates")
    assert "zz_min.json: colour: unknown top-level key" in text
    assert "unknown block type 'chart'" in text
    assert "zz_snap.json: source" in text
    assert len(problems) == 3, problems


def test_load_templates_refuses_a_duplicate_id_across_files():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "zz_min.json", FORMULAS)
        _write(d, "zz_copy.json", FORMULAS)
        try:
            model.load_templates(d)
        except model.TemplateError as e:
            problems = e.problems
        else:
            raise AssertionError("duplicate id accepted")
    assert any("zz_copy.json" in p and "id 'zz_min' is also declared by zz_min.json" in p
               for p in problems), problems


def test_load_templates_refuses_invalid_json_by_file():
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "zz_bad.json"), "w") as f:
            f.write("{not json")
        _write(d, "zz_min.json", FORMULAS)
        try:
            model.load_templates(d)
        except model.TemplateError as e:
            problems = e.problems
        else:
            raise AssertionError("invalid JSON accepted")
    assert len(problems) == 1 and problems[0].startswith("zz_bad.json:") and "not valid JSON" in problems[0]


def test_load_templates_refuses_a_missing_directory():
    try:
        model.load_templates(os.path.join(APP_DIR, "no_such_report_templates"))
    except model.TemplateError as e:
        assert "no_such_report_templates" in str(e)
    else:
        raise AssertionError("missing directory accepted")


def test_module_is_pure():
    with open(os.path.join(APP_DIR, "report_template_model.py")) as f:
        source = f.read()
    assert "import frappe" not in source and "from frappe" not in source
