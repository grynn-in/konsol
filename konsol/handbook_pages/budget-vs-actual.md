---
title: Budget against actuals
audience: Everyone
section: Concepts
order: 170
---
The Consolidated Statement shows three budget columns beside the actuals: **Budget (before eliminations)**, **Variance to budget** and **Budget year to date**. They read the group's approved budget for the fiscal year of the period you open. Where there is no approved budget, the cells are blank, never zero, and the legend under the statement says why.

## What "approved" means

An **Approved Budget** is a declaration per root consolidation group and fiscal year. It names one approved **Budget Cycle** of a budget scenario, and the layers that count: all layers, or one layer. Submit is the approval: the document records who submitted it and when (**Approved By**, **Approved On**), and the statement's legend names them. A group and year have one live declaration at a time.

The statement reads that declaration's Cycle and layers only. Two Cycles are never added together, and a forecast is not shown here.

## What the columns hold

- **Budget (before eliminations)**: the Cycle's rows for the period, translated at the Cycle's Rate Set and consolidated by ownership, in the group's reporting currency, with the signs of the actuals (income positive, costs in brackets).
- **Variance to budget**: this period's actual minus the budget, on profit and loss lines only.
- **Budget year to date**: the budget from the start of the fiscal year to the period.

The budget is **before intercompany eliminations and without group-level adjustments**: no elimination, deal journal, top-side or equity-method entry reaches it. The legend repeats this.

## Balance sheet lines

A budget has period movements only, so the balance sheet budget column shows the **budgeted movements year to date**, not balances. There is **no variance to budget on the balance sheet**: a movement against a balance is not a variance.

A Cycle whose entries do not balance within an entity and period carries a balancing plug. The plug shows as its own balance sheet line, **Unbalanced budget (no balancing entry)**, and counts in the residual, so the budget balance sheet adds up and the plug is visible.

## When the columns are blank

| The legend says | What to do |
|---|---|
| No approved budget for the group and year | Declare one in **Approved Budget** (the Budget Cycle and the layer policy) and submit it. A draft declares nothing. |
| The declaration names a Cycle that is cancelled or not approved | Cancel the declaration and amend it to name the approved Cycle. |
| The inputs of the Cycle have changed since it was approved | The warehouse no longer holds the approved figures. Review the Cycle; to approve the changed inputs, cancel and amend the Cycle and the declaration. |
| The declaration reads a layer that is no Budget Layer | Define the layer in Budget Layer, or amend the declaration. |
| No budget rows in the warehouse for the Cycle up to the period | Build the budget. The columns return when the build has run. |

## The drill and the rate

Click the Budget cell of a heading to open **Statement Drill** on the Budget basis: one row per entity in your scope, with its accounts. Each account shows the translation method and rate the warehouse used for it, and the Rate Set, as they were when the budget was built. The Source column opens the Budget Cycle, and the entity's **Budget Entries** are one click away. The Unbalanced line has its own drill, by entity.

## Export to Excel

**Export to Excel** adds the three budget columns and the legend line to the Statement sheet, and a **Budget drill** sheet with every heading's budget drill and its rates. The download is recorded as a close event that names the Cycle, layers and Rate Set.

## Cancelling a Cycle

A Budget Cycle named by a submitted Approved Budget cannot be cancelled. Cancel the Approved Budget first (a period of its year must be open), then the Cycle.

## An entity that leaves the group during the plan

Today the budget shows what the warehouse holds for every entity in the Cycle, whatever the entity's ownership window; how an exit should cut the budget is not decided yet.
