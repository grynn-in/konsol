---
title: FX translation and CTA
audience: Everyone
section: Concepts
order: 110
---
Consolidation translates each entity from its functional currency into the reporting currency of each group that holds it (IAS 21).

## Which rate an account uses

Each Main Account declares a **Translation Method**:

| Translation Method | Rate |
|---|---|
| closing | the period's Closing rate |
| average | the period's Average rate |
| historical | the entity's Historical Equity Rate for the account |

If you leave it blank, the account type decides: historical for Equity, closing for other Balance Sheet accounts, average for Profit and Loss. A Profit and Loss account may be declared closing (hyperinflation, IAS 29). It can never be historical, and a Balance Sheet account can never be average.

If you change the method on a Published account, every period is translated again at the next full rebuild, closed periods included.

An entity whose functional currency is the reporting currency translates at 1, whatever rates exist.

## Group Exchange Rate

Each period has one **Closing** and one **Average** rate per currency pair, into the group's reporting currency. The rate is units of the reporting currency for one unit of the from-currency (Quote / Quoted Per). If a needed pair does not have exactly one approved Closing and one approved Average rate for the period, the build stops. It never guesses a rate.

A Balance Sheet balance on an account translated at closing (not Equity) is translated at the closing rate of each reporting date, not as the sum of each period's movement at that period's rate. Equity and historical accounts are never retranslated: each movement keeps the rate it was translated at.

## Historical Equity Rate

A rate for one entity and one equity account, from a **Rate Date**, into one **To Currency**. A period uses the latest rate whose date is on or before the period's date. Before the first rate date, the account translates at the closing rate. The rate is recorded under the group that owns the entity; a group above it applies the rate whose To Currency is its own reporting currency, so a sub-group presenting in EUR under a parent presenting in USD needs two rates for the same account. A group with no rate into its currency is named by the warehouse check and translates the account at the closing rate.

## CTA

After translation at different rates, an entity's translated balances no longer net to zero. The currency translation adjustment (CTA) is the amount that makes them net to zero again. It is one figure per entity and period, on the group's share. The minority share is carried with NCI.

The consolidated balance sheet shows the CTA under the **CTA account** declared in Close Settings. If none is declared, the CTA appears in the unmatched residual, and sign-off is blocked by a setup gap until the account is declared.

## Year-end close

For a trial balance of period-end balances, konsol closes the year itself in the year's Closing period: it reverses the Profit and Loss accounts and moves the result into the chart's retained-earnings account. That close moves amounts that are already translated, so it creates no CTA. A close that the ERP posts itself is translated as ordinary activity, and a warehouse test reports it.
