---
title: Excel add-in
audience: Everyone
section: Concepts
order: 150
---
The Konsolidat add-in reads konsol's numbers into Excel (desktop and on the web) with `=K.*` formulas.

## Install

Each konsol site serves its own add-in manifest. [Download Excel add-in](/api/method/konsol.excel_addin.manifest) saves it as `konsolidat-<site>.xml`; Close Settings → Excel Add-in has the same link. Every address in the file is this site's, the add-in's name in Excel names the site, and its Id is derived from the site, so two sites install as two add-ins. A site that has no https `host_name` refuses the download and says so; a System Manager sets it with `bench --site <site> set-config host_name https://<public address>`.

Office loads add-ins over HTTPS only, a local stack included: the site's address needs a certificate the computer trusts.

- **Excel for Mac:** copy the file into `~/Library/Containers/com.microsoft.Excel/Data/Documents/wef/` (create the `wef` folder if it is missing), restart Excel, and pick the add-in under Home → Add-ins.
- **Excel for Windows:** put the file in a shared network folder, add that folder under File → Options → Trust Center → Trust Center Settings → Trusted Add-in Catalogs with "Show in Menu" ticked, restart Excel, and pick the add-in under Home → Add-ins → Shared Folder.
- **Excel on the web:** Home → Add-ins → More Add-ins → My Add-ins → Upload My Add-in, and choose the file.
- **For everyone (centralized deployment):** your Microsoft 365 administrator opens the Microsoft 365 admin center, Settings → Integrated apps → Upload custom apps, and uploads the file or gives the download link above as the link to the manifest file.

## Another site

The pane's **Server** panel shows the site the add-in talks to. By default that is the site it was installed from. To read another site with the same install, enter that site's address, such as `https://site.example` (https, the address only, no path), and choose **Use this server**; clear the field to go back. The choice belongs to the workbook: it is saved with the file, so two open workbooks can read two sites, and changing it in one leaves the other alone. A workbook with no choice reads the site the add-in was installed from. The add-in keeps a separate sign-in for each site and never sends one site's sign-in to another.

The other site must allow it: a System Manager of that site lists this add-in's site, for example `https://site.example`, in Close Settings → Excel Add-in Origins, one address per line. Listing a site trusts every page served from it with this site's add-in sign-ins, so list only sites you control. Until then the pane says "Could not reach … ask an admin of … to add … to Close Settings → Excel Add-in Origins". Only the add-in's own calls are opened to a listed site, and they carry the add-in's sign-in token, never the browser's cookies.

## Sign in

Open the add-in's pane (the **Konsolidat** button on the Home ribbon) and sign in with your konsol user and password. The add-in keeps a sign-in token and sends it with every formula. The token lasts 8 hours; after that the pane asks you to sign in again. You read only what your konsol user may read.

## =K.EPM

```
=K.EPM(entity, year, period, account, [measure], [scenario], [dimensions], [scenarioId], [hierarchy], [node], [layer])
```

- `period` is a period number or code of the declared fiscal calendar (the Closing period included), or a range: Q1 to Q4, H1, H2 or FY.
- Arguments in brackets are optional. To reach a later one, leave the slot empty: `, ,`.
- `dimensions` is **one** argument for any number of dimensions. Give one text cell such as `"region=EU;product=X"`, or a 2-column range with a name and a value on each row (no header row). A name must be a dimension this site declared.

For example, `=K.EPM("E1",2026,3,"4000","","","region=EU")`.

`scenario` is a word that picks what to read: `actuals` (the default), `budget`, `forecast`, `latest_estimate`, `variance`, `consolidated`, `consolidated_budget`, `forecast_accuracy`, `forecast_accuracy_group` or `cashflow`. The planning words, and which forecast round they read, are explained on the Forecast rounds page. The accuracy words have no error % measure: compute it in the sheet as `forecast_error / ABS(actual_amount)`. The Forecast Accuracy report shows it per row.

An actuals read of a period with no trial balance shows `#N/A` with the message "No trial balance for …". A loaded period where the account has no balance shows 0. A range such as Q1 reads if at least one of its periods is loaded. A malformed argument shows `#VALUE!` with the reason.

The other functions have their own argument order. `dimensions` is the fifth argument in the first four:

```
=K.EPM_BUDGET(entity, year, period, account, [dimensions], [scenarioId], [hierarchy], [node], [layer])
=K.EPM_VARIANCE(entity, year, period, account, [dimensions], [scenarioId], [hierarchy], [node])
=K.EPM_DEBIT(entity, year, period, account, [dimensions], [hierarchy], [node])
=K.EPM_CREDIT(entity, year, period, account, [dimensions], [hierarchy], [node])
=K.EPMSAVE(amount, entity, year, period, account, scenarioId, layer, [dimensions], [hierarchy], [node], currency, amountBasis, [asOfDate])
=K.CF(group, year, period, line)
```

`K.EPMSAVE` writes a budget figure. Its last two arguments are required: `currency` is the ISO code the amount is in (the entity's functional currency, for example `EUR`), and `amountBasis` is `Movement` or `Year-to-date`. Neither is defaulted. They come after `node`, so leave the optional arguments empty to reach them:

```
=K.EPMSAVE(1000, "E1", 2026, 3, "6100", "PLAN26", "base", , , , "EUR", "Movement")
```

A formula written without them shows `#VALUE!` and names the missing argument.

`asOfDate` names the forecast round the figure is for: the Budget Cycle of the scenario and year with that as-of date, as `YYYY-MM-DD` (a date cell works too). It is required when the scenario is a forecast and refused for any other scenario, so a budget formula leaves it out. A round that has no Budget Cycle yet is refused by name; create the Cycle first, the formula never does.

```
=K.EPMSAVE(1000, "E1", 2026, 9, "6100", "FC26", "base", , , , "EUR", "Movement", "2026-06-30")
```

## Reports

The pane's **Reports** panel paints two templates:

- **Monthly P&L** applies live `=K.EPM()` formulas, one per Published Profit and Loss account and month.
- **Entity TB — local currency, account level (pre-consolidation)** snapshots frozen numbers onto a sheet named `TB`: one row per account and period, with debit, credit and net. It is the entity's own trial balance in its local currency, with dimensions summed. It is read before FX translation, ownership and eliminations, so it reconciles to a group figure at account level in local currency only. Row 2 of the sheet carries this note. It is not the trial balance the group was consolidated from; that is konsol#428's `K.CTB`.

## Refresh

The add-in's group on the Home ribbon has **Refresh sheet** and **Refresh workbook**, and the pane's **Refresh** panel has the same two buttons:

- **Refresh sheet** takes the active sheet's snapshot again (if it is a snapshot sheet), recalculates every `=K.*` formula on it, and writes `Values as of <date time> UTC from <site>` in row 1, naming the site the values came from. A snapshot taken from another site than the workbook now reads is refused, not repainted.
- **Refresh workbook** does the same for every snapshot and every sheet with a `=K.*` formula.

If you are not signed in, nothing is refreshed and the pane opens and says why.

## When the add-in changes

Excel keeps the add-in's manifest, which lists its buttons and functions. When a konsol release raises the manifest version (the ribbon buttons came with 2.0.2.0, `K.EPMSAVE`'s currency and amountBasis arguments with 2.0.3.0, its asOfDate argument with 2.0.4.0, the scenario words in `K.EPM`'s tooltip with 2.0.5.0, and the server kept per workbook with 2.0.6.0), download the manifest again and install it the same way. With centralized deployment by link, Microsoft 365 reads the new file itself. Until then Excel keeps the old buttons and functions. A workbook on an old add-in sends requests the server refuses; reload the add-in.

The manifest built per site (konsol#605) has a new Id. An add-in installed from the old shared file is a different add-in to Excel: remove it, then install this site's.
