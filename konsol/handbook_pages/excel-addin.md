---
title: Excel add-in
audience: Everyone
section: Concepts
order: 150
---
The Konsolidat add-in reads konsol's numbers into Excel (desktop and on the web) with `=K.*` formulas.

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

An actuals read of a period with no trial balance shows `#N/A` with the message "No trial balance for …". A loaded period where the account has no balance shows 0. A range such as Q1 reads if at least one of its periods is loaded. A malformed argument shows `#VALUE!` with the reason.

The other functions have their own argument order. `dimensions` is the fifth argument in the first four:

```
=K.EPM_BUDGET(entity, year, period, account, [dimensions], [scenarioId], [hierarchy], [node], [layer])
=K.EPM_VARIANCE(entity, year, period, account, [dimensions], [scenarioId], [hierarchy], [node])
=K.EPM_DEBIT(entity, year, period, account, [dimensions], [hierarchy], [node])
=K.EPM_CREDIT(entity, year, period, account, [dimensions], [hierarchy], [node])
=K.EPMSAVE(amount, entity, year, period, account, scenarioId, layer, [dimensions], [hierarchy], [node], currency, amountBasis)
=K.CF(group, year, period, line)
```

`K.EPMSAVE` writes a budget figure. Its last two arguments are required: `currency` is the ISO code the amount is in (the entity's functional currency, for example `EUR`), and `amountBasis` is `Movement` or `Year-to-date`. Neither is defaulted. They come after `node`, so leave the optional arguments empty to reach them:

```
=K.EPMSAVE(1000, "E1", 2026, 3, "6100", "PLAN26", "base", , , , "EUR", "Movement")
```

A formula written without them shows `#VALUE!` and names the missing argument.

## Reports

The pane's **Reports** panel paints two templates:

- **Monthly P&L** applies live `=K.EPM()` formulas, one per Published Profit and Loss account and month.
- **Entity TB — local currency, account level (pre-consolidation)** snapshots frozen numbers onto a sheet named `TB`: one row per account and period, with debit, credit and net. It is the entity's own trial balance in its local currency, with dimensions summed. It is read before FX translation, ownership and eliminations, so it reconciles to a group figure at account level in local currency only. Row 2 of the sheet carries this note. It is not the trial balance the group was consolidated from; that is konsol#428's `K.CTB`.

## Refresh

The add-in's group on the Home ribbon has **Refresh sheet** and **Refresh workbook**:

- **Refresh sheet** takes the active sheet's snapshot again (if it is a snapshot sheet), recalculates every `=K.*` formula on it, and writes `Values as of <date time> UTC` in row 1.
- **Refresh workbook** does the same for every snapshot and every sheet with a `=K.*` formula.

If you are not signed in, nothing is refreshed and the pane opens and says why.

## When the add-in changes

Excel keeps the add-in's manifest, which lists its buttons and functions. When a konsol release raises the manifest version (the ribbon buttons came with 2.0.2.0, and `K.EPMSAVE`'s currency and amountBasis arguments with 2.0.3.0), your Microsoft 365 administrator uploads the manifest again in the Microsoft 365 admin center, from `/assets/konsol/excel-addin/manifest.xml` on your konsol site. Until then Excel keeps the old buttons and functions. A workbook on an old add-in sends requests the server refuses; reload the add-in.
