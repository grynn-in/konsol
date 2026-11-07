---
title: Rates, ownership, intercompany and journals
audience: Group Accountant
section: Start here
order: 20
---
You prepare what the consolidation needs beyond the trial balances. You draft each record; the Close Lead approves it (Approve in the record's **Actions** menu). Approving it puts it into the warehouse and requests a consolidation build.

## Exchange rates (Group Exchange Rate)

- One record per currency pair, rate type and period. **Group Reporting Currency** is the currency the group presents in; **From Currency** is an entity's functional currency.
- **Rate Type**: Closing (balance sheet) or Average (profit and loss).
- **Quote** and **Quoted Per**: the rate is Quote / Quoted Per. For example, 0.6607 USD per 100 JPY. Quote a weak currency per 100, 1,000 or 10,000.
- For each functional currency that differs from the reporting currency, each period needs exactly one approved Closing and one approved Average rate. Without them the build stops.
- To correct an approved rate, make a new rate with **Supersedes** set to the old one. Approving the new rate cancels the old one.
- **Reason for Change** is required for a correction, for an amendment, and for a rate that moves more than Close Settings allows.

## Historical equity rates (Historical Equity Rate)

One rate per entity, equity account and **Rate Date**, under the group that owns the entity. It applies to every period from its rate date until a later rate replaces it. See *FX translation* under Concepts.

## Ownership (Ownership Period)

How much of a node its parent owns, from an **Effective Date**: **Ownership %** (0 to 100) and **Consolidation Method** (full, proportional, equity or none). A blank **End Date** means the period is still open. A change is a new Ownership Period that supersedes the old one when it is approved.

## Intercompany

- **Intercompany Account**: pairs an account with the account the partner books the other side on. You draft it; the Close Lead publishes it. See *Intercompany elimination* under Concepts.
- **IC Balance**: intercompany sales and the buyer's ending inventory from intercompany purchases, for unrealised profit. You draft it; the Close Lead approves it.

## Journals (Consolidation Journal)

A topside or reclassification entry for one consolidation group and period, in the group's reporting currency. Each line names an entity, an account, and a debit or a credit. To reverse the journal in a later period, give **Reverse In Fiscal Year** and **Reverse In Fiscal Period**. Then use **Send for Approval**. The Close Lead approves or rejects it.

## Builds (Build Approval)

Submitting or cancelling a trial balance, and approving a rate, ownership period, journal or IC balance, requests a build automatically:

- A build that an approval requested is approved at once.
- Any other build of a high-risk scope (actuals, scenarios, consolidation, chart, full) waits in **Pending Review** for the Close Lead.
- Staging and reporting builds are low risk and are approved at once.
- A change made while a build of the same scope is pending is added to that build.

To request a build yourself, add a Build Approval with its **Build Scope**. After a build is Completed, Failed or Cancelled, **Run Again** requests it again.
