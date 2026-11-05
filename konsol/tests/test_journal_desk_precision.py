"""konsol#353 option C, Desk follow-up: the Consolidation Journal form types and
shows amounts in the journal currency's ISO minor unit.

The Desk is the only product UI (Deepak Pai, 5 Nov 2026). With no precision on
the Currency fields, the form rounds what is typed to the site's number format
(8069: 2 places), so a BHD 10.125 reached the server as 10.13. The form script
(consolidation_journal.js) sets the precision of the line amounts (per form,
on the grid) and of the totals from ISO Currency.minor_unit: USD 2, JPY 0,
BHD 3; the warehouse's 4 places until the currency is known or when it has no
minor unit. No site-wide setting changes.

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
global.frappe = {
  ui: { form: { on: (dt, h) => { handlers[dt] = h; } } },
  db: {
    get_value: async (doctype, filters, fields) => {
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
    const frm = {
      doc: c.doc,
      set_df_property: (f, p, v) => { set[`${f}.${p}`] = v; },
      fields_dict: { lines: { grid: { update_docfield_property: (f, p, v) => { set[`lines.${f}.${p}`] = v; } } } },
      refresh_field: () => {},
    };
    await handlers["Consolidation Journal"][c.event](frm);
    out.push(set);
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


def _expect(places):
    return {"total_debit.precision": places, "total_credit.precision": places,
            "lines.debit_amount.precision": places, "lines.credit_amount.precision": places}


ISO = {"ISO Currency": {
    "USD": {"minor_unit": 2, "minor_unit_declared": 0},
    "JPY": {"minor_unit": 0, "minor_unit_declared": 0},
    "BHD": {"minor_unit": 3, "minor_unit_declared": 0},
    "XDR": {"minor_unit": 4, "minor_unit_declared": 1},
    "ZZN": {"minor_unit": None, "minor_unit_declared": 0},
}}


def test_the_precision_is_the_currency_minor_unit():
    cases = [{"event": "refresh", "doc": {"currency": cur, "consolidation_group": "G"}, "tables": ISO}
             for cur in ("USD", "JPY", "BHD", "XDR")]
    assert _run(cases) == [_expect(2), _expect(0), _expect(3), _expect(4)]


def test_a_new_journal_takes_the_currency_of_its_groups_root():
    tables = dict(ISO, **{"Consolidation Group": {_group_key("ZZG"): {"reporting_currency": "BHD"}}})
    out = _run([{"event": "consolidation_group", "doc": {"consolidation_group": "ZZG"}, "tables": tables},
                # a changed group drops the currency saved for the old one
                {"event": "consolidation_group", "doc": {"consolidation_group": "ZZG", "currency": "USD"},
                 "tables": tables}])
    assert out == [_expect(3), _expect(3)]


def test_unknown_currency_or_minor_unit_keeps_the_warehouse_four_places_never_two():
    out = _run([{"event": "refresh", "doc": {}, "tables": ISO},
                {"event": "refresh", "doc": {"currency": "ZZN"}, "tables": ISO},
                {"event": "refresh", "doc": {"currency": "ZZQ"}, "tables": ISO}])
    assert out == [_expect(4), _expect(4), _expect(4)]


def test_no_site_wide_number_setting_is_changed():
    with open(SCRIPT) as f:
        src = f.read()
    for forbidden in ("System Settings", "currency_precision", "use_number_format_from_currency",
                      "set_value(\"System"):
        assert forbidden not in src, forbidden
