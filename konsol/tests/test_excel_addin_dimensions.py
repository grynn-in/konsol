"""The Excel add-in passes dimensions as one generic argument (konsol#287).

Decided by Deepak Pai, 5 Nov 2026 (decision 1, option A): the fixed cost
centre / department positional arguments of every K.* function are replaced
by ONE ``dimensions`` argument, sent as the ``dimensions`` dict the API
already accepts, and ``_LEGACY_DIM_MAP`` is deleted with its only caller.
Rejected: declared Excel slots (B), no dimensions in Excel (C).

Three layers are tested here:

- **the add-in's metadata** (functions.json): the signature Excel shows;
- **the add-in's code** (functions.js), run under node by
  ``excel_addin_harness.mjs`` with a stub server: what each function POSTs,
  and that a malformed argument is refused rather than dropped;
- **the API** (api.py, loaded with a stub frappe): a request that names a
  dimension the old way, or one the site has not declared, is refused, never
  ignored.

The examples use neutral dimension names (``dim_region``, ``dim_product``);
the issue exists because one customer's names were shipped.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDIN_DIR = os.path.join(APP_DIR, "public", "excel-addin")
HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "excel_addin_harness.mjs")

#: Where the one ``dimensions`` argument sits in each signature: where the
#: first of the two fixed arguments used to be.
DIMENSIONS_POSITION = {
    "EPM": 6,
    "EPM_BUDGET": 4,
    "EPM_VARIANCE": 4,
    "EPM_DEBIT": 4,
    "EPM_CREDIT": 4,
    "EPMSAVE": 7,
}

#: The parameter lists after the change, in order. A later positional
#: argument moves one place left; this is the rewrite the PR documents.
SIGNATURES = {
    "EPM": ["entity", "year", "period", "account", "measure", "scenario",
            "dimensions", "scenarioId", "hierarchy", "node", "layer"],
    "EPM_BUDGET": ["entity", "year", "period", "account", "dimensions",
                   "scenarioId", "hierarchy", "node", "layer"],
    "EPM_VARIANCE": ["entity", "year", "period", "account", "dimensions",
                     "scenarioId", "hierarchy", "node"],
    "EPM_DEBIT": ["entity", "year", "period", "account", "dimensions",
                  "hierarchy", "node"],
    "EPM_CREDIT": ["entity", "year", "period", "account", "dimensions",
                   "hierarchy", "node"],
    "EPMSAVE": ["amount", "entity", "year", "period", "account", "scenarioId",
                "layer", "dimensions", "hierarchy", "node"],
}


def _metadata():
    with open(os.path.join(ADDIN_DIR, "functions.json")) as f:
        return {fn["id"]: fn for fn in json.load(f)["functions"]}


def _js_source():
    with open(os.path.join(ADDIN_DIR, "functions.js")) as f:
        return f.read()


# -- functions.json: the signature Excel shows -------------------------------

def test_every_dimensional_function_has_the_new_signature():
    meta = _metadata()
    for fn_id, expected in SIGNATURES.items():
        names = [p["name"] for p in meta[fn_id]["parameters"]]
        assert names == expected, (fn_id, names)


def test_the_dimensions_parameter_takes_text_or_a_two_column_range():
    meta = _metadata()
    for fn_id, pos in DIMENSIONS_POSITION.items():
        param = meta[fn_id]["parameters"][pos]
        assert param["name"] == "dimensions"
        assert param.get("optional") is True, fn_id
        # A matrix parameter receives a typed string as [["..."]] and a range
        # as rows, so one parameter accepts both forms.
        assert param.get("type") == "any", fn_id
        assert param.get("dimensionality") == "matrix", fn_id
        assert "dim_region=EU;dim_product=X" in param["description"], fn_id


def test_no_function_takes_a_fixed_dimension_argument():
    meta = _metadata()
    for fn in meta.values():
        for p in fn["parameters"]:
            assert p["name"] not in ("costCenter", "department"), fn["id"]


def test_cash_flow_function_is_unchanged():
    names = [p["name"] for p in _metadata()["CF"]["parameters"]]
    assert names == ["group", "year", "period", "line"]


# -- functions.js under node: what each function sends -----------------------

def _call(fn, *args, reply=None, status=200):
    node = shutil.which("node")
    # Not a skip: a host without node would report this file green while
    # testing nothing (the konsol#248 family).
    assert node, "node is required to run the Excel add-in tests"
    payload = {"fn": fn, "args": list(args), "status": status}
    if reply is not None:
        payload["reply"] = reply
    proc = subprocess.run([node, HARNESS, json.dumps(payload)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


_READ = ("ZZE", 2026, 1, "ZZ4000")


def test_epm_sends_text_pairs_as_the_dimensions_dict():
    out = _call("EPM", *_READ, "", "", [["dim_region=EU;dim_product=X"]])
    assert out["error"] is None, out
    assert out["result"] == 42
    (req,) = out["requests"]
    assert req["url"] == "/api/method/konsol.api.epm_batch"
    (row,) = req["body"]
    assert row["dimensions"] == {"dim_region": "EU", "dim_product": "X"}


def test_a_plain_string_is_read_like_a_one_cell_range():
    # Excel passes a matrix parameter as rows; a caller outside Excel may not.
    out = _call("EPM", *_READ, "", "", "dim_region=EU")
    assert out["error"] is None, out
    assert out["requests"][0]["body"][0]["dimensions"] == {"dim_region": "EU"}


def test_spaces_around_names_values_and_a_trailing_separator_are_trimmed():
    out = _call("EPM", *_READ, "", "", [[" dim_region = EU ; dim_product=X ;"]])
    assert out["error"] is None, out
    assert out["requests"][0]["body"][0]["dimensions"] == {
        "dim_region": "EU", "dim_product": "X"}


def test_a_two_column_range_is_read_as_name_value_rows():
    rng = [["dim_region", "EU"], ["", ""], ["dim_product", 42]]
    out = _call("EPM_BUDGET", *_READ, rng)
    assert out["error"] is None, out
    (row,) = out["requests"][0]["body"]
    # the blank row is skipped; a number is sent as text
    assert row["dimensions"] == {"dim_region": "EU", "dim_product": "42"}
    assert row["scenario"] == "budget"


def test_no_dimensions_sends_no_dimensions_key():
    for blank in (None, "", [[""]], [[None]]):
        out = _call("EPM", *_READ, "", "", blank)
        assert out["error"] is None, (blank, out)
        assert "dimensions" not in out["requests"][0]["body"][0], blank


def test_no_request_carries_a_fixed_dimension_key():
    out = _call("EPM", *_READ, "", "", [["dim_region=EU"]])
    row = out["requests"][0]["body"][0]
    assert "cost_center" not in row
    assert "department" not in row


def test_arguments_after_dimensions_move_one_place_left():
    out = _call("EPM", *_READ, "period_amount", "budget", [["dim_region=EU"]],
                "ZZ_PLAN", "ZZ_H", "ZZ_N", "base")
    assert out["error"] is None, out
    row = out["requests"][0]["body"][0]
    assert row["scenario_id"] == "ZZ_PLAN"
    assert row["hierarchy"] == "ZZ_H"
    assert row["hierarchy_node"] == "ZZ_N"
    assert row["layer"] == "base"

    for fn, extra, expect in (
        ("EPM_BUDGET", ["ZZ_PLAN", "ZZ_H", "ZZ_N", "base"],
         {"scenario_id": "ZZ_PLAN", "hierarchy": "ZZ_H",
          "hierarchy_node": "ZZ_N", "layer": "base"}),
        ("EPM_VARIANCE", ["ZZ_PLAN", "ZZ_H", "ZZ_N"],
         {"scenario_id": "ZZ_PLAN", "hierarchy": "ZZ_H", "hierarchy_node": "ZZ_N"}),
        ("EPM_DEBIT", ["ZZ_H", "ZZ_N"], {"hierarchy": "ZZ_H", "hierarchy_node": "ZZ_N"}),
        ("EPM_CREDIT", ["ZZ_H", "ZZ_N"], {"hierarchy": "ZZ_H", "hierarchy_node": "ZZ_N"}),
    ):
        out = _call(fn, *_READ, [["dim_region=EU"]], *extra)
        assert out["error"] is None, (fn, out)
        row = out["requests"][0]["body"][0]
        assert row["dimensions"] == {"dim_region": "EU"}, fn
        for k, v in expect.items():
            assert row[k] == v, (fn, k, row)


def test_a_malformed_dimensions_argument_is_refused_not_dropped():
    bad = (
        [["dim_region"]],                      # no "="
        [["=EU"]],                             # no name
        [["dim_region="]],                     # no value
        [["dim_region=EU;dim_region=US"]],     # the same name twice
        [["dim_region", "EU"], ["dim_region", "US"]],
        [["dim_region", ""]],                  # a name with no value
        [["", "EU"]],                          # a value with no name
        [["dim_region", "EU", "x"]],           # three columns
        [["dim_region=EU"], ["dim_product=X"]],  # one column, several rows
    )
    for arg in bad:
        out = _call("EPM", *_READ, "", "", arg)
        assert out["error"] is not None, (arg, out)
        assert out["error"]["code"] == "#VALUE!", (arg, out)
        assert "dimensions" in out["error"]["message"], (arg, out)
        assert out["requests"] == [], arg


def test_an_argument_past_the_last_slot_is_refused():
    # An old formula that passed the last argument (e.g. K.EPM's layer) now
    # has one argument more than the signature. Dropped, its layer filter
    # would be lost and the cell would sum every layer.
    for fn, n in SIGNATURES.items():
        args = ([100] if fn == "EPMSAVE" else []) + list(_READ)
        args += [""] * (len(n) - len(args)) + ["base"]
        out = _call(fn, *args)
        assert out["error"] is not None, (fn, out)
        assert "argument" in out["error"]["message"], (fn, out)
        assert out["requests"] == [], fn


def test_empty_slots_past_the_signature_are_accepted():
    # Excel passes every parameter its CACHED functions.json declares, an
    # omitted one as null. An Excel that still holds the 12-argument metadata
    # from before konsol#287 calls K.EPM("DE_ECL", 2024, 12, "4000") with 12
    # arguments, 8 of them null (found on demo.konsolidat.com, 6 Nov 2026:
    # every cell the pane's Apply painted said "takes at most 11 arguments").
    # Nothing past the signature carries a value, so nothing is lost.
    for fn, n in SIGNATURES.items():
        args = ([100] if fn == "EPMSAVE" else []) + list(_READ)
        args += [None] * (len(n) - len(args) + 1) + [""]
        out = _call(fn, *args, reply={"message": {"values": [1.0], "errors": [None]}})
        assert out["error"] is None, (fn, out)


def test_an_invocation_object_past_the_signature_is_accepted():
    # Excel Online appends an invocation object after the declared
    # parameters (6 Nov 2026: K.EPM("DE_ECL", 2024, 12, "4000") still said
    # "takes at most 11 arguments, not 12" with the 11-parameter metadata
    # served). An object that is not a range is Excel's, not the formula's.
    for fn, n in SIGNATURES.items():
        args = ([100] if fn == "EPMSAVE" else []) + list(_READ)
        args += [None] * (len(n) - len(args)) + [{"address": "Sheet1!B2", "functionName": "EPM"}]
        out = _call(fn, *args, reply={"message": {"values": [1.0], "errors": [None]}})
        assert out["error"] is None, (fn, out)


def test_an_object_that_is_not_last_is_still_refused():
    args = list(_READ) + [""] * 7 + [{"x": 1}, {"address": "Sheet1!B2"}]
    out = _call("EPM", *args)
    assert out["error"] is not None, out


def test_the_refusal_names_what_the_extra_argument_was():
    args = list(_READ) + [""] * 7 + ["base"]
    out = _call("EPM", *args)
    assert out["error"] is not None and "base" in out["error"]["message"], out


def test_epmsave_sends_the_dimensions_dict():
    reply = {"message": {"status": "ok", "value": 100}}
    out = _call("EPMSAVE", 100, "ZZE", 2026, 1, "ZZ6100", "ZZ_PLAN", "base",
                [["dim_region=EU;dim_product=X"]], "ZZ_H", "ZZ_N", reply=reply)
    assert out["error"] is None, out
    assert out["result"] == 100
    (req,) = out["requests"]
    assert req["url"] == "/api/method/konsol.api.budget_cell_save"
    body = req["body"]
    assert body["dimensions"] == {"dim_region": "EU", "dim_product": "X"}
    assert body["hierarchy"] == "ZZ_H"
    assert body["hierarchy_node"] == "ZZ_N"
    assert not [k for k in body if k.startswith("dim_")], body


def test_epmsave_refuses_a_malformed_dimensions_argument():
    out = _call("EPMSAVE", 100, "ZZE", 2026, 1, "ZZ6100", "ZZ_PLAN", "base",
                [["dim_region"]])
    assert out["error"] is not None, out
    assert out["requests"] == []


def test_epmsave_shows_the_servers_refusal():
    # Before: any failed write returned the amount, so a refused dimension
    # looked saved. The server's message now reaches the cell.
    reply = {"exception": "frappe.exceptions.ValidationError: x",
             "_server_messages": json.dumps([json.dumps(
                 {"message": "Dimension 'dim_zz' is not a budget dimension."})])}
    out = _call("EPMSAVE", 100, "ZZE", 2026, 1, "ZZ6100", "ZZ_PLAN", "base",
                [["dim_zz=EU"]], reply=reply, status=417)
    assert out["error"] is not None, out
    assert "dim_zz" in out["error"]["message"]


def test_functions_js_names_no_fixed_dimension():
    src = _js_source()
    for name in ("costCenter", "department", "cost_center"):
        assert name not in src, name


# -- api.py: legacy keys and undeclared names are refused --------------------

class _ValidationError(Exception):
    pass


def _throw(msg, exc=_ValidationError, **kw):
    raise exc(msg)


def _load_api():
    fake_frappe = types.ModuleType("frappe")
    fake_frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    fake_frappe.throw = _throw
    fake_frappe.ValidationError = _ValidationError
    fake_frappe.PermissionError = type("PermissionError", (Exception,), {})
    fake_utils = types.ModuleType("frappe.utils")
    fake_utils.now_datetime = lambda: None
    fake_frappe.utils = fake_utils
    stubs = {
        "frappe": fake_frappe,
        "frappe.utils": fake_utils,
        "konsol.clickhouse": types.SimpleNamespace(
            connection_url=lambda s: "", get_connection=lambda: {}),
    }
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location(
            "_host_api_k287", os.path.join(APP_DIR, "api.py"))
        api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(api)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return api


def test_legacy_dimension_map_is_gone():
    api = _load_api()
    assert not hasattr(api, "_LEGACY_DIM_MAP")
    with open(os.path.join(APP_DIR, "api.py")) as f:
        assert "_LEGACY_DIM_MAP" not in f.read()


def test_batch_dimensions_come_only_from_the_dimensions_dict():
    api = _load_api()
    assert api._extract_batch_dimensions(
        {"dimensions": {"dim_region": "EU", "dim_product": ""}}
    ) == {"dim_region": "EU"}
    assert api._extract_batch_dimensions({}) == {}


def test_a_batch_row_naming_a_dimension_outside_the_dict_is_refused():
    # An add-in loaded before this change still sends the old top-level key.
    # Ignoring it would answer with the total over every value of that
    # dimension, so the row is refused and the message says where it goes.
    api = _load_api()
    for key in ("cost_center", "department", "dim_region"):
        err = api._batch_row_error({"entity": "ZZE", key: "ZZ1"})
        assert err and f"'{key}'" in err, (key, err)
        assert "dimensions" in err, err


def test_a_batch_row_with_dimensions_that_are_not_an_object_is_refused():
    api = _load_api()
    for bad in ("dim_region=EU", ["dim_region", "EU"], 7):
        err = api._batch_row_error({"entity": "ZZE", "dimensions": bad})
        assert err and "dimensions" in err, (bad, err)


def test_a_well_formed_batch_row_has_no_error():
    api = _load_api()
    row = {"entity": "ZZE", "year": 2026, "period": 1, "account": "ZZ4000",
           "measure": "period_net_amount", "scenario": "actuals", "fact": "zz",
           "scenario_id": "", "hierarchy": "", "hierarchy_name": "",
           "hierarchy_node": "", "node": "", "layer": "",
           "dimensions": {"dim_region": "EU"}}
    assert api._batch_row_error(row) is None
    assert api._batch_row_error({"dimensions": None}) is None


def test_a_batch_row_naming_a_hierarchy_without_a_node_is_refused():
    # The silent case of the signature change: an old
    # =K.EPM_BUDGET(e, y, p, a, "", "", "PLAN26") now puts PLAN26 in the
    # hierarchy slot. A hierarchy with no node used to be ignored, so the
    # cell would sum every budget scenario instead of reading PLAN26.
    api = _load_api()
    err = api._batch_row_error({"entity": "ZZE", "hierarchy": "ZZ_PLAN"})
    assert err and "'ZZ_PLAN'" in err and "node" in err, err
    assert api._batch_row_error(
        {"entity": "ZZE", "hierarchy": "ZZ_H", "node": "ZZ_N"}) is None
    assert api._batch_row_error({"entity": "ZZE", "hierarchy": ""}) is None


def test_a_batch_row_with_a_non_text_hierarchy_or_node_is_refused():
    # .strip() on a number raised AttributeError and failed the whole batch.
    api = _load_api()
    for key in ("hierarchy", "hierarchy_name", "hierarchy_node", "node"):
        err = api._batch_row_error({"entity": "ZZE", key: 2026})
        assert err and f"'{key}'" in err and "text" in err, (key, err)


def _api_with_datasets(datasets, calls=None):
    """api with _get_fact answering from {scenario_key: dimensions list};
    each lookup is appended to ``calls`` when given."""
    api = _load_api()

    def get_fact(fact=None, scenario=None):
        if calls is not None:
            calls.append((fact, scenario))
        dims = datasets.get(fact or scenario)
        if dims is None:
            return None
        return types.SimpleNamespace(
            fact_name=f"zz_{fact or scenario}", dimensions=json.dumps(dims))

    api._get_fact = get_fact
    return api


def test_hierarchy_dimensions_must_be_declared_on_the_dataset():
    # Hierarchy mode checked names only against an identifier pattern, so
    # data_area_id, layer or any column was accepted as a filter.
    api = _api_with_datasets({"actuals": ["dim_region"]})
    assert api._hierarchy_dimension_error("actuals", {"dim_region"}) is None
    assert api._hierarchy_dimension_error("actuals", set()) is None
    for bad in ("data_area_id", "layer", "dim_zz"):
        err = api._hierarchy_dimension_error("actuals", {bad})
        assert err and f"'{bad}'" in err and "dim_region" in err, (bad, err)


def test_hierarchy_dimensions_without_a_dataset_are_refused():
    api = _api_with_datasets({})
    err = api._hierarchy_dimension_error("budget", {"dim_region"})
    assert err and "dim_region" in err, err
    # no dimensions asked for: nothing to check, no Dataset needed
    assert api._hierarchy_dimension_error("budget", set()) is None


def test_hierarchy_dimension_lookup_normalises_the_scenario():
    # The hierarchy read itself normalises the scenario; the Dataset lookup
    # must too, or " budget" passes the read and fails the dimension check.
    calls = []
    api = _api_with_datasets({"budget": ["dim_region"]}, calls)
    assert api._hierarchy_dimension_error(" Budget", {"dim_region"}) is None
    assert calls == [(None, "budget")]


def test_hierarchy_dimension_lookup_is_cached_per_call():
    calls = []
    api = _api_with_datasets({"budget": ["dim_region"]}, calls)
    cache = {}
    for scenario in ("budget", " Budget", "budget"):
        assert api._hierarchy_dimension_error(
            scenario, {"dim_region"}, cache) is None
    assert api._hierarchy_dimension_error("budget", {"dim_zz"}, cache)
    assert calls == [(None, "budget")]


def test_epm_batch_checks_hierarchy_dimensions_before_reading():
    block = _read_api().split("def epm_batch")[1].split("\n@frappe.whitelist")[0]
    hier = block.split("if _is_hierarchy_mode(req):")[1].split("normalized[i] = {")[0]
    assert "_hierarchy_dimension_error(" in hier
    # one cache for the whole batch, made before the row loop
    assert block.index("dataset_dims = {}") < block.index("for i, req in enumerate")
    assert "dataset_dims)" in hier


def test_a_batch_row_with_fact_and_a_hierarchy_node_is_refused():
    # The hierarchy table comes from the scenario, not from a Dataset, so a
    # `fact` on a hierarchy row would decide only which dimensions are checked.
    api = _load_api()
    err = api._batch_row_error(
        {"entity": "ZZE", "fact": "zz_budget", "hierarchy": "ZZ_H", "node": "ZZ_N"})
    assert err and "'fact'" in err and "node" in err, err
    assert api._batch_row_error({"entity": "ZZE", "fact": "zz_budget"}) is None


def test_epm_value_refuses_fact_with_a_hierarchy_node():
    block = _read_api().split("def epm_value")[1].split("\n@frappe.whitelist")[0]
    assert "_fact_with_node_error(" in block


def test_no_function_makes_excel_append_an_invocation_argument():
    # checkArity refuses more arguments than the signature. Excel appends an
    # invocation argument to a function declared with any of these options,
    # which the guard would then refuse on every call.
    for fn in _metadata().values():
        options = fn.get("options", {})
        for key in ("requiresInvocation", "stream", "cancelable",
                    "requiresAddress", "requiresParameterAddresses"):
            assert not options.get(key), (fn["id"], key)
    assert "requiresInvocation" in _js_source()  # the guard's comment says why


def test_budget_cell_save_refuses_a_hierarchy_without_a_node():
    block = _read_api().split("def budget_cell_save")[1].split("\n@frappe.whitelist")[0]
    check = block.index("_hierarchy_without_node_error(data)")
    assert check < block.index("_assert_budget_write_access(data)")


def test_epm_batch_refuses_the_row_before_reading_it():
    block = _read_api().split("def epm_batch")[1].split("\n@frappe.whitelist")[0]
    check = block.index("_batch_row_error(req)")
    assert check < block.index("_extract_batch_dimensions(req)")


def _read_api():
    with open(os.path.join(APP_DIR, "api.py")) as f:
        return f.read()


def test_budget_write_merges_declared_dimensions():
    api = _load_api()
    data = {"main_account": "ZZ6100",
            "dimensions": {"dim_region": "EU", "dim_product": ""}}
    api._apply_budget_dimensions(data, ["dim_region", "dim_product"])
    assert data["dim_region"] == "EU"
    assert data.get("dim_product", "") == ""
    assert "dimensions" not in data


def test_budget_write_refuses_an_undeclared_dimension():
    api = _load_api()
    data = {"main_account": "ZZ6100", "dimensions": {"dim_zz": "EU"}}
    try:
        api._apply_budget_dimensions(data, ["dim_region"])
    except _ValidationError as exc:
        msg = str(exc)
    else:
        raise AssertionError("an undeclared budget dimension was accepted")
    assert "'dim_zz'" in msg
    assert "dim_region" in msg


def test_budget_write_refuses_dimensions_that_are_not_an_object():
    api = _load_api()
    for bad in ("dim_region=EU", ["dim_region"], 7):
        try:
            api._apply_budget_dimensions({"dimensions": bad}, ["dim_region"])
        except _ValidationError:
            continue
        raise AssertionError(f"dimensions={bad!r} was accepted")


def test_budget_write_refuses_a_dimension_given_twice_with_two_values():
    api = _load_api()
    data = {"dim_region": "US", "dimensions": {"dim_region": "EU"}}
    try:
        api._apply_budget_dimensions(data, ["dim_region"])
    except _ValidationError as exc:
        assert "dim_region" in str(exc)
    else:
        raise AssertionError("two values for one dimension were accepted")


def test_budget_cell_save_applies_dimensions_before_any_check():
    block = _read_api().split("def budget_cell_save")[1].split("\n@frappe.whitelist")[0]
    applied = block.index("_apply_budget_dimensions(data, dims)")
    assert applied < block.index("validate_hierarchy_write")
    assert applied < block.index("_assert_budget_write_access(data)")
    assert applied < block.index("_line_identity(data, dims)")
