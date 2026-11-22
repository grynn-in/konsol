---
title: Rates, ownership, intercompany and journals
audience: Group Accountant
section: Start here
order: 20
---
You prepare what the consolidation needs beyond the trial balances. You draft each record; the Close Lead approves it (Approve in the record's **Actions** menu). Approving it puts it into the warehouse and requests a consolidation build.

## Exchange rates (Rate Sheet)

- A **Statutory** Rate Sheet holds the rates for one fiscal period: an Average (profit and loss) and a Closing (balance sheet) quote per currency pair. It translates the actuals and locks when the period closes.
- A **Constant** Rate Sheet holds one rate per pair for a fiscal year. It translates budget and forecast and locks when the year closes. There is one per year for the site.
- **Group Reporting Currency** is the currency the group presents in; **From Currency** is an entity's functional currency.
- **Quote** and **Quoted Per**: the rate is Quote / Quoted Per. For example, 0.6607 USD per 100 JPY. Quote a weak currency per 100, 1,000 or 10,000.
- Each period needs one approved Statutory sheet that covers every functional currency that differs from the reporting currency. Without it the build stops.
- Use **Pre-fill from ERP** or **Duplicate previous** to start a draft. Then send it for approval.
- To correct an approved sheet, make a new sheet that supersedes it. Approving the new sheet cancels the old one.
- **Reason for Change** is required for a correction, and for a rate that moves more than Close Settings allows.

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
- If a build of the same scope is already waiting, no new build is made: the change joins the waiting build, which still needs the Close Lead's approval, even when an approval requested it.
- If a build of the same scope is running, the change gets one more build when the running one finishes.

To request a build yourself, add a Build Approval with its **Build Scope**. After a build is Completed, Failed or Cancelled, **Run Again** requests it again.
