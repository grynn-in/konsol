---
title: Forecast rounds
audience: Everyone
section: Concepts
order: 175
---
A forecast is re-estimated during the year. Each re-estimate is a **round**: an approved **Budget Cycle** of a forecast scenario, with an **as-of date** (the last day of actuals the round knows about) and a **Horizon** (how many periods after the as-of period it forecasts). A scenario can hold several approved rounds for one year side by side. They are never added together.

## Rounds and the as-of date

The as-of date falls in one Regular period of the declared fiscal calendar: that is the round's as-of period. The horizon counts Regular periods after it, as the calendar declares them, never calendar months. A horizon that runs past the year end needs one Budget Cycle per fiscal year, each with the same as-of date: together they are one round.

## The Current Forecast

Which round a year reports is declared, not guessed. A **Current Forecast** names one approved round for a forecast scenario and fiscal year. There is one per scenario and fiscal year at a time. Submit is the approval: the document records who submitted it and when (**Approved By**, **Approved On**).

Every report and read that shows "the forecast" for a year reads the declared round, and only that round:

- the scenario trial balance in the warehouse;
- cost allocation on a forecast scenario (the pool is the declared round's);
- the Excel add-in's forecast read, flat and by hierarchy node.

The Consolidated Statement's budget columns show the group's approved budget, not a forecast.

A year with approved rounds and no Current Forecast reads nothing: never the sum of its rounds, and not the latest round either. The warehouse build warns, naming the scenario and the year. Declare a Current Forecast to show the year.

In Excel, the forecast reads refuse such a year and name the missing Current Forecast; they never show 0 for it (see "Forecast reads in Excel" below).

## The latest estimate

The **latest estimate** of a round is the year as that round sees it: the actuals for each period up to and including the as-of period, and the round's forecast after it, to the end of the horizon. It covers the entities the round forecasts. The forecast is the total of all its layers. Where an entity has no actuals for a period up to the as-of, the period is missing from the estimate (it is never filled with the forecast or with a zero) and the warehouse build warns, naming the entity and the period.

The warehouse keeps the latest estimate of every approved round, so rounds can be compared. The Excel add-in's `latest_estimate` read shows the declared round's.

## Horizon coverage

The **Forecast Horizon Coverage** report (Planning, Forecast) lists every round with its as-of period and the periods its horizon reaches, and says whether each period has an approved Budget Cycle for its year:

| The report says | What to do |
|---|---|
| Covered | Nothing: every period of the horizon has an approved Cycle. |
| Uncovered, with a draft Cycle named | Approve the draft. |
| Uncovered, with no Cycle for a year | Create a Budget Cycle for that year with the same as-of date, and approve it. |
| Periods after the last declared period | Declare the next fiscal year in EPM Fiscal Year. |
| The as-of date is in no declared Regular period | Declare the period in EPM Fiscal Year, or correct the Cycle's as-of date. |

## Forecast accuracy

The **Forecast Accuracy** report (Planning, Forecast) compares every approved round with the actuals. It covers each **closed** period after the round's as-of period, up to the end of its horizon. A period is closed when its status in EPM Fiscal Year is Closed or Locked. An open period is never compared, because its actuals can still change.

Each row is one round, period, entity and account, in the entity's currency, with the round's layers added together:

| Column | Meaning |
|---|---|
| Lag | How many Regular periods after the as-of period the period is. Lag 1 is the first period the round forecast. |
| Error | The amount forecast minus actual, signed as the ledger (debit positive). On an income account (credit), a negative error means the round expected more income than came in. On a cost account, a positive error means it expected more cost. |
| Absolute Error | The error without its sign, added up over the dimensions. |
| Error % | The error divided by the actual (without its sign), times 100. It shows n/a where the actual is 0. |

To see **bias**, filter one lag (for example lag 1) and read the error down the periods. Errors that keep the same sign are a bias; errors that change sign are noise. There is no separate bias figure.

The report shows every round side by side, so you can compare an early round with a later one for the same period. An Entity Accountant sees their own entities only.

Accuracy is measured against today's actuals. If a closed period is reopened and corrected, the next warehouse build compares the rounds with the **restated** actuals. An approved round never changes, so the forecast side of the comparison stays as it was approved.

The warehouse keeps the same comparison in the consolidation group's currency too. There, an entity whose currency is not the group's carries the movement in exchange rates in its error: the forecast is translated at the round's rates and the actual at the period's rates.

## Forecast reads in Excel

The add-in reads a forecast by its scenario word (the sixth argument of `=K.EPM`):

| Word | What it reads |
|---|---|
| `forecast` | The declared round's forecast, by entity. |
| `latest_estimate` | The declared round's latest estimate: actuals to the as-of period, the forecast after it. |
| `consolidated_budget` | The group's consolidated budget, translated and before eliminations. The entity argument is the consolidation group. Name a budget scenario in `scenarioId`, or a forecast scenario to read its declared round. With no scenario named, it reads the one budget scenario of the year, never a forecast, and refuses when there are none or several. `layer` reads one layer. |
| `forecast_accuracy` | The declared round's accuracy, in entity currency. The measure is `forecast_error` unless you name `forecast_abs_error`, `forecast_amount` or `actual_amount`. |
| `forecast_accuracy_group` | The declared round's accuracy for a consolidation group, in the group's currency, its entities added together. The entity argument is the consolidation group. |

For example, `=K.EPM("G1",2026,3,"4000","","consolidated_budget",,"BUD26")` reads the consolidated budget of group G1 for scenario BUD26.

- A forecast read of a year with no Current Forecast is refused, and the message names the missing declaration. With no scenario named, the `forecast`, `latest_estimate` and accuracy reads take the one forecast scenario declared for the year, and refuse when there are none or several.
- The `budget` read refuses a forecast scenario, because it would add the rounds together. With no scenario named, it reads the one budget scenario of the year, never a forecast, and refuses when there are none or several.
- In Excel, the accuracy reads show the declared round only. The Forecast Accuracy report shows every round.
- A `budget`, `variance` or `consolidated_budget` read is refused when its scenario has no approved Budget Cycle for the year. For one entity, it is also refused when the approved Cycle holds no Budget Entry or Annual Input for that entity. The message names the scenario, the entity or group, and the year: approve a Budget Cycle to read it. A draft Cycle does not count.
- Where an approved Cycle budgets the entity and has no row for the cell, the cell reads 0: no budget is a budget of zero. A forecast or accuracy cell with no row reads 0.
- There is no error % to read in Excel. Compute it in the sheet as `forecast_error / ABS(actual_amount)`, from two `=K.EPM` cells of the same range, and guard an actual of 0. The Forecast Accuracy report shows the error % per row, and n/a where the actual is 0.

## Cancelling a round

A Budget Cycle named by a submitted Current Forecast cannot be cancelled. Cancel the Current Forecast first (a period of its year must be open), then the Cycle. To change the round a year reports, cancel the Current Forecast and amend it to name the new round.
