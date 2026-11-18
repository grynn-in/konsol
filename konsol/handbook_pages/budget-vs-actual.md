---
title: Budget against actuals
audience: Everyone
section: Concepts
order: 170
---
The Consolidated Statement shows four budget columns beside the actuals: **Budget (before eliminations)**, **Variance to budget**, **Favourability** and **Budget year to date**. They read the group's approved budget for the fiscal year of the period you open. Where there is no approved budget to show, the columns are not shown at all and the legend under the statement says why: a visible state, never a blank column.

## What "approved" means

An **Approved Budget** is a declaration per root consolidation group and fiscal year. It names one approved **Budget Cycle** of a budget scenario, and the layers that count: all layers, or one layer. Submit is the approval: the document records who submitted it and when (**Approved By**, **Approved On**), and the statement's legend names them. A group and year have one live declaration at a time.

The statement reads that declaration's Cycle and layers only. Two Cycles are never added together, and a forecast is not shown here.

## What the columns hold

- **Budget (before eliminations)**: the Cycle's rows for the period, translated at the Cycle's Rate Set and consolidated by ownership, in the group's reporting currency, with the signs of the actuals (income positive, costs in brackets).
- **Variance to budget**: this period's actual minus the budget, on profit and loss lines only.
- **Favourability**: a word, not a number, on profit and loss headings and the net result (see below).
- **Budget year to date**: the budget from the start of the fiscal year to the period.

The budget is **before intercompany eliminations and without group-level adjustments**: no elimination, deal journal, top-side or equity-method entry reaches it. The legend repeats this.

## Balance sheet lines

A budget has period movements only, so the balance sheet budget column shows the **budgeted movements year to date**, not balances. There is **no variance to budget on the balance sheet**: a movement against a balance is not a variance.

A Cycle whose entries do not balance within an entity and period carries a balancing plug. The plug shows as its own balance sheet line, **Unbalanced budget (no balancing entry)**, and counts in the residual, so the budget balance sheet adds up and the plug is visible.

## Favourability

The Favourability column reads the variance to budget of a profit and loss heading:

| The column says | When |
|---|---|
| Favourable | The variance to budget is above zero. |
| Unfavourable | The variance to budget is below zero. |
| On budget | The variance to budget is exactly zero. |

The sign of the variance alone decides it. The statement shows income and costs both income-positive, so a cost above budget is already a negative variance and income above budget a positive one. The Main Account's normal balance plays no part.

The net result reads the same way: Favourable when there is more profit than budgeted. Balance sheet lines have no favourability, as they have no variance to budget. A heading that has actuals but no line in the approved budget is budgeted at zero: on the profit and loss its Budget and Budget year to date are 0.00 and its variance is the actual, so unbudgeted spend is Unfavourable and unbudgeted revenue Favourable. On the balance sheet it shows Budget 0.00 and no variance.

The legend under the statement states the rule, and the Excel export carries the same column.

## When the columns are not shown

| The legend says | What to do |
|---|---|
| No approved budget for the group and year | Declare one in **Approved Budget** (the Budget Cycle and the layer policy) and submit it. A draft declares nothing. |
| The declaration names a Cycle that is cancelled or not approved | Cancel the declaration and amend it to name the approved Cycle. |
| The inputs of the Cycle have changed since it was approved | The warehouse no longer holds the approved figures. Review the Cycle; to approve the changed inputs, cancel and amend the Cycle and the declaration. |
| The declaration reads a layer that is no Budget Layer | Define the layer in Budget Layer, or amend the declaration. |
| No budget rows in the warehouse for the Cycle up to the period | Build the budget. The columns return when the build has run. |

## The drill and the rate

Click the Budget cell of a heading to open **Statement Drill** on the Budget basis: one row per entity in your scope, with its accounts. Each account shows its **declared method and rate**: the translation method the chart declared for the account when the budget was built, and the rate it was translated at, with the Rate Set. An account with no declared method reads "closing (undeclared)": it is translated at the closing rate. The Source column opens the Budget Cycle, and the entity's **Budget Entries** are one click away. The Unbalanced line has its own drill, by entity.

## Export to Excel

**Export to Excel** adds the four budget columns and the legend line to the Statement sheet, and a **Budget drill** sheet with every heading's budget drill and its rates. The download is recorded as a close event that names the Cycle, layers and Rate Set.

## The worksheet on the Budget basis

The **Consolidation Worksheet** has a **Basis** filter: **Actual** (the default) or **Budget**. On Budget it lays out the approved budget of the group and year in the same columns as the actuals: one column per entity, its translation difference, then Consolidated. The budget's balancing plug is its own column, **Unbalanced budget**. Balance sheet accounts show the budgeted movements to the period end, not balances. The legend names the approved budget and repeats that it is before intercompany eliminations and without group-level adjustments.

Where there is no approved budget to show, the worksheet shows no columns, and the legend says why, in the same words as the statement.

## The entity basis

On the Consolidated Statement's **Entity** basis, the budget columns show that entity's own budget: the entity's rows of the Budget Cycle named by its root group's Approved Budget, in the entity's **functional currency**, before translation and before eliminations. A Cycle whose entries do not balance for the entity shows the plug as the **Unbalanced budget** line, as on the group statement, so the balance sheet still adds up.

The Entity basis has no group filter. The approved budget is the one declared on the group at the top of the consolidation tree the entity sits in, even when the entity sits under a sub-group: an Approved Budget is declared only on a root group.

| The legend says | What to do |
|---|---|
| The entity is a member of no consolidation group | Add it to a root Consolidation Group, and declare that group's Approved Budget. |

Any other reason (no approved budget, a cancelled Cycle, changed inputs) reads as on the group statement.

**Export to Excel** on the Entity basis carries the same budget columns and legend line. It has no **Budget drill** sheet, as the Entity basis has no budget drill; the legend says so.

## Cancelling a Cycle

A Budget Cycle named by a submitted Approved Budget cannot be cancelled. Cancel the Approved Budget first (a period of its year must be open), then the Cycle.

## An entity that leaves the group during the plan

Today the budget shows what the warehouse holds for every entity in the Cycle, whatever the entity's ownership window; how an exit should cut the budget is not decided yet.
