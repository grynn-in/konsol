---
title: Trial balance amount basis
audience: Everyone
section: Concepts
order: 130
---
ERPs export a trial balance in one of three shapes. Each submission declares its shape in **Amount Basis**, and the warehouse turns all three into period movements, the one shape the rest of konsol reads.

| Amount Basis | What each debit and credit is | Period movement |
|---|---|---|
| Period movement | this period's movement | the row as it stands |
| Year-to-date movement | the movement from the start of the fiscal year | this period's figure less the previous submitted period's in the same year; the year's first period as it stands |
| Period-end balance | the closing balance at period end | this period's balance less the previous submitted period's, in any year; the first submitted period as it stands |

An account missing from a later year-to-date or period-end file counts as 0 there. Its movement is minus the previous figure. For example, a loan that was repaid simply drops out of the file.

## Choose it once per entity

An entity's trial balances must all have the same basis. If an entity's submissions mix bases, a warehouse check fails the build and names the entity. A wrong basis doubles or halves every balance.

The CSV may carry an `amount_basis` column. It can only confirm the form: a row that names a different basis refuses the file.

The Close Lead can correct the basis of submitted trial balances with **Set Amount Basis**. A change of basis counts as a data change for the period, so the checks must run again before sign-off.

## Period-end balances and the year end

A period-end-balance file carries the year's profit and loss until the year ends, and the next year's file starts those accounts from zero. Konsol closes the year itself in the year's **Closing** period: it reverses the profit and loss accounts and moves the result into the chart's **Retained Earnings Account**. Exactly one Published account carries that flag. Without a Closing period after the year's last submitted period, or without exactly one retained-earnings account, no close is made, and a warehouse check fails the build and names the year.
