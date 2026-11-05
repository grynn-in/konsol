"""konsol#353 option C, Desk: the Consolidation Journal form takes amounts at
the warehouse's 4 places and shows them in the journal currency's minor unit.

The Desk is the only product UI (Deepak Pai, 5 Nov 2026). With no precision on
the Currency fields, the form rounded what is typed to the site's number format
(8069: 2 places), so a BHD 10.125 reached the server as 10.13.

PR #394 review: an input precision of the currency's minor unit rounds too
(JPY 10.5 -> 11, BHD 10.1255 -> 10.126), so the server's refusal of extra
places never fired from the Desk. Never silently change a number; refuse with
a reason (Deepak Pai). So input is at 4 places for every currency, and only
the display uses the minor unit, through a formatter on the four docfields.
Precision 0 is passed as a number, so JPY shows 0 places, not Frappe's 2.

The script runs under node against a stub of the frappe calls it makes.
"""
import json
import os
import shutil
import subprocess

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(APP_DIR, "consolidation", "doctype", "consolidation_journal",
                      "consolidation_journal.js")

HARNESS = r"""
const fs = require("fs");
const [script, casesJson] = process.argv.slice(1);
const cases = JSON.parse(casesJson);
const handlers = {};
let tables = {};
const metaFields = {};
global.locals = { "Consolidation Journal": {} };
global.frappe = {
  ui: { form: { on: (dt, h) => { handlers[dt] = h; } } },
  form: { formatters: { Currency: (value, df) => `fmt(${value},${df.precision})` } },
  meta: { get_docfield: (dt, f) => (metaFields[`${dt}.${f}`] = metaFields[`${dt}.${f}`] || {}) },
  db: {
    get_value: async (doctype, filters) => {
      const key = typeof filters === "string" ? filters : JSON.stringify(filters);
      const row = (tables[doctype] || {})[key];
      return { message: row === undefined ? {} : row };
    },
  },
};
eval(fs.readFileSync(script, "utf8"));
(async () => {
  const out = [];
  for (const c of cases) {
    tables = c.tables;
    const set = {};
    const doc = Object.assign({ doctype: "Consolidation Journal", name: "CJ-ZZ" }, c.doc);
    locals["Consolidation Journal"][doc.name] = doc;
    const frm = {
      doc,
      set_df_property: (f, p, v) => { set[`${f}.${p}`] = v; },
      fields_dict: { lines: { grid: { update_docfield_property: (f, p, v) => { set[`lines.${f}.${p}`] = v; } } } },
      refresh_field: () => {},
    };
    await handlers["Consolidation Journal"][c.event](frm);
    const line = { doctype: "Consolidation Journal Line", parent: doc.name };
    const shown = {};
    for (const key of ["Consolidation Journal.total_debit", "Consolidation Journal Line.debit_amount"]) {
      const f = metaFields[key] && metaFields[key].formatter;
      shown[key] = f ? f(c.value, { fieldname: key }, {}, key.endsWith("debit_amount") ? line : doc) : null;
    }
    out.push({ input: set, shown });
  }
  console.log(JSON.stringify(out));
})();
"""


def _group_key(group):
    return json.dumps({"consolidation_group": group, "data_area_id": ["is", "not set"]},
                      separators=(",", ":"))


def _run(cases):
    node = shutil.which("node")
    assert node, "node is required to run the Consolidation Journal form script"
    assert os.path.exists(SCRIPT), "consolidation_journal.js (the Desk form script) is missing"
    result = subprocess.run([node, "-e", HARNESS, SCRIPT, json.dumps(cases)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


INPUT_AT_4 = {"total_debit.precision": 4, "total_credit.precision": 4,
              "lines.debit_amount.precision": 4, "lines.credit_amount.precision": 4}


def _shown(value, places):
    return {"Consolidation Journal.total_debit": f"fmt({value},{places})",
            "Consolidation Journal Line.debit_amount": f"fmt({value},{places})"}


ISO = {"ISO Currency": {
    "USD": {"minor_unit": 2, "minor_unit_declared": 0},
    "JPY": {"minor_unit": 0, "minor_unit_declared": 0},
    "BHD": {"minor_unit": 3, "minor_unit_declared": 0},
    "XDR": {"minor_unit": 4, "minor_unit_declared": 1},
    "ZZN": {"minor_unit": None, "minor_unit_declared": 0},
}}


def test_input_is_at_four_places_for_every_currency_so_nothing_typed_is_rounded():
    """JPY 10.5 and BHD 10.1255 reach the server as typed; it refuses them."""
    cases = [{"event": "refresh", "doc": {"currency": cur}, "tables": ISO, "value": 10.5}
             for cur in ("USD", "JPY", "BHD", "XDR")]
    assert [c["input"] for c in _run(cases)] == [INPUT_AT_4] * 4


def test_the_display_is_the_currency_minor_unit_zero_included():
    cases = [{"event": "refresh", "doc": {"currency": cur, "name": f"CJ-{cur}"}, "tables": ISO,
              "value": 10.125} for cur in ("USD", "JPY", "BHD", "XDR")]
    assert [c["shown"] for c in _run(cases)] == [
        _shown(10.125, 2), _shown(10.125, 0), _shown(10.125, 3), _shown(10.125, 4)]


def test_a_new_journal_takes_the_currency_of_its_groups_root():
    tables = dict(ISO, **{"Consolidation Group": {_group_key("ZZG"): {"reporting_currency": "BHD"}}})
    out = _run([{"event": "consolidation_group", "doc": {"consolidation_group": "ZZG", "currency": "USD"},
                 "tables": tables, "value": 1}])
    assert out[0]["input"] == INPUT_AT_4
    assert out[0]["shown"] == _shown(1, 3)


def test_unknown_currency_or_minor_unit_shows_the_warehouse_four_places():
    out = _run([{"event": "refresh", "doc": {"name": "CJ-A"}, "tables": ISO, "value": 1},
                {"event": "refresh", "doc": {"currency": "ZZN", "name": "CJ-B"}, "tables": ISO, "value": 1},
                {"event": "refresh", "doc": {"currency": "ZZQ", "name": "CJ-C"}, "tables": ISO, "value": 1}])
    assert [c["shown"] for c in out] == [_shown(1, 4)] * 3
    assert [c["input"] for c in out] == [INPUT_AT_4] * 3


def test_no_site_wide_number_setting_is_changed():
    with open(SCRIPT) as f:
        src = f.read()
    for forbidden in ("System Settings", "currency_precision", "use_number_format_from_currency",
                      "set_value(\"System"):
        assert forbidden not in src, forbidden
