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

## Deploy to demo

```bash
konsol_cli/scripts/deploy-excel-full.sh
```

M365 Admin Center manifest URL:

```
https://demo.konsolidat.com/assets/konsol/excel-addin/manifest.xml
```

See `konsol_cli/HANDOFF-EXCEL-ONLINE.md` for regression checklist.