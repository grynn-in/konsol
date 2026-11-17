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

In Excel, the `forecast` and `latest_estimate` reads refuse such a year and name the missing Current Forecast; they never show 0 for it. With no scenario named, they read the one forecast scenario declared for the year, and refuse when there are none or several. The `budget` read refuses a forecast scenario, because it would add the rounds together, and with no scenario named it reads every scenario except the forecasts.

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

## Cancelling a round

A Budget Cycle named by a submitted Current Forecast cannot be cancelled. Cancel the Current Forecast first (a period of its year must be open), then the Cycle. To change the round a year reports, cancel the Current Forecast and amend it to name the new round.
