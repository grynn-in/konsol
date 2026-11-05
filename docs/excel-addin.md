# Konsolidat Excel add-in

Office.js shared-runtime add-in for **live `=K.EPM()` custom functions** on Excel
Desktop and Excel Online, plus a task pane for sign-in, Apply report, and diagnostics.

**Source of truth:** `konsol/public/excel-addin/` in this repo (`grynn-in/konsol`).

Served by Frappe at `/assets/konsol/excel-addin/`.

## Architecture

```
Excel (Desktop / Online)
  |
  |-- Cells: =K.EPM() / =K.PING()  <-- functions.js
  |
  +-- Task pane: index.html        <-- login, Apply report, Messages
           |
           v
  https://demo.konsolidat.com
    /assets/konsol/excel-addin/   (static assets)
    /api/method/konsol.api.*      (epm_batch, excel_addin_auth, build_cell_map, ...)
```

## Files

```
konsol/public/excel-addin/
  index.html          Shared runtime page
  functions.js        =K.* custom function implementations
  functions.json      Custom function metadata
  manifest.demo.xml   Source manifest (copy to manifest.xml on deploy)
  manifest.xml        Hosted manifest for M365 Admin Center
  assets/             Ribbon icons
```

Report templates and `build_cell_map` live in `konsol/report_compiler.py` and
`konsol/api.py`.

## Functions

Arguments in `[brackets]` are optional. Leave one out with an empty slot
(`, ,`) to reach a later one.

```
=K.EPM(entity, year, period, account, [measure], [scenario], [dimensions], [scenarioId], [hierarchy], [node], [layer])
=K.EPM_BUDGET(entity, year, period, account, [dimensions], [scenarioId], [hierarchy], [node], [layer])
=K.EPM_VARIANCE(entity, year, period, account, [dimensions], [scenarioId], [hierarchy], [node])
=K.EPM_DEBIT(entity, year, period, account, [dimensions], [hierarchy], [node])
=K.EPM_CREDIT(entity, year, period, account, [dimensions], [hierarchy], [node])
=K.EPMSAVE(amount, entity, year, period, account, scenarioId, layer, [dimensions], [hierarchy], [node])
=K.CF(group, year, period, line)
```

### Dimensions

`dimensions` is one argument for any number of dimensions (konsol#287). Give it
in one of two forms:

- **One text cell:** `"dim_region=EU;dim_product=X"`. Spaces around names and
  values are ignored. This form cannot carry a value that contains `;`. Use
  the range form for such a value.
- **A 2-column range:** names in the first column, values in the second, for
  example `$H$1:$I$2` holding `dim_region | EU` and `dim_product | X`. The
  range has no header row: every row is a name/value pair. A fully blank row
  is skipped.

A name is a dimension the site declared (a Published Dimension record). The
server refuses a name the Dataset (reads) or the budget (EPMSAVE) does not
have. The add-in refuses a malformed argument: a pair without `=`, a name
without a value, a value without a name, a name given twice, or a range that
is not 2 columns wide. A refusal shows as `#VALUE!` with the reason.

EPMSAVE shows the server's refusal in the cell. Before, a failed write still
returned the amount.

### Rewriting formulas from before konsol#287

The add-in used to take two fixed arguments, cost centre and department, where
`dimensions` now is. Every argument after them moved one place to the left.
A formula breaks if it passes either fixed argument or any later argument.
Formulas with only the required arguments (and, for `K.EPM`, `measure` and
`scenario`) are unchanged.

| Before | After |
|---|---|
| `=K.EPM("E1",2026,3,"4000","","","EU","X")` | `=K.EPM("E1",2026,3,"4000","","","dim_region=EU;dim_product=X")` |
| `=K.EPM("E1",2026,3,"4000","","","","","","H1","N1")` | `=K.EPM("E1",2026,3,"4000","","","","","H1","N1")` |
| `=K.EPM_BUDGET("E1",2026,3,"6100","","","PLAN26")` | `=K.EPM_BUDGET("E1",2026,3,"6100","","PLAN26")` |
| `=K.EPM_VARIANCE("E1",2026,"FY","6100","EU","X")` | `=K.EPM_VARIANCE("E1",2026,"FY","6100","dim_region=EU;dim_product=X")` |
| `=K.EPM_DEBIT("E1",2026,3,"1300","","","H1","N1")` | `=K.EPM_DEBIT("E1",2026,3,"1300","","H1","N1")` |
| `=K.EPMSAVE(B5,"E1",2026,1,"6100","PLAN26","base","EU")` | `=K.EPMSAVE(B5,"E1",2026,1,"6100","PLAN26","base","dim_region=EU")` |
| `=K.EPMSAVE(B5,"E1",2026,1,"6100","PLAN26","base","","","H1","N1")` | `=K.EPMSAVE(B5,"E1",2026,1,"6100","PLAN26","base","","H1","N1")` |

The rule: where the old formula had two slots for cost centre and department,
the new one has one slot. Put both values in it as `name=value` pairs, or
leave it empty. Then copy the remaining arguments unchanged. Each name is the
one your site declared for the dimension the old argument filtered on; the
examples use `dim_region` and `dim_product`.

A formula left unrewritten shows an error, not a wrong number. Each old value
lands one slot to the left, and each landing is refused:

- a bare value such as `"EU"` in `dimensions` is not `name=value`;
- an old department value in `scenarioId` is refused when the Dataset has no
  scenario column (actuals), and when it names no Scenario (budget);
- an old scenario id in `hierarchy` is refused because it has no node;
- an old last argument (`layer`, `node`) is one argument too many, and the
  add-in refuses a formula with more arguments than the signature.

Two consequences of these refusals:

- **A hierarchy needs a node on every row.** A sheet that fixes the
  hierarchy and leaves `node` blank on some rows now shows an error on those
  rows. Before, the hierarchy was ignored there. This is the accepted
  trade-off of the guard against a scenario id moved into the `hierarchy`
  slot. Leave `hierarchy` blank on rows that read no node.
- **A scenario id must exist and must have a column to filter.** A
  `scenarioId` on actuals, or one that names no Scenario, is refused, not
  ignored or read as 0.

A workbook still on the old add-in sends the old keys. The server now refuses
those rows ("Unknown request key"), so reload the add-in.

## Deploy to demo

```bash
konsol_cli/scripts/deploy-excel-full.sh
```

M365 Admin Center manifest URL:

```
https://demo.konsolidat.com/assets/konsol/excel-addin/manifest.xml
```

See `konsol_cli/HANDOFF-EXCEL-ONLINE.md` for regression checklist.