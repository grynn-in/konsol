---
title: Intercompany elimination
audience: Everyone
section: Concepts
order: 120
---
Balances between two group entities are removed from the consolidated numbers. Konsol pairs the two sides from what each entity's trial balance says. It never guesses a partner.

## What makes a row intercompany

- **Allow Intercompany** on a Main Account says the account may carry intercompany partner rows.
- An **Intercompany Account** (Published by the Close Lead) pairs an account with its **Counterpart Account**: the account the partner books the other side on, for example an intercompany receivable against the intercompany payable, or revenue against cost. Leave the counterpart blank when both sides use the same account. Each account belongs to one pair. Both accounts must declare Allow Intercompany before the pairing can be published.
- On the trial balance, **partner_data_area_id** names the other group entity on each intercompany row, for example `ZZ02`. A row without a partner loads, but it is never eliminated: it is listed as unmatched.

With no Published Intercompany Account, intercompany shows "not configured" and nothing is checked. A group with no intercompany at all declares **None in this group** in Close Settings.

## Matched, difference and NCI

A pair is the two sides (E1 with partner E2 on one account, and E2 with partner E1 on the counterpart). They are compared at 100% in the reporting currency. A balance-sheet pair compares the balance to date; a profit-and-loss pair compares the period's movement.

- **Matched**: the smaller side. It is eliminated from both sides at the share the group holds of both.
- **NCI**: when the group holds more of one side than the other (100% against 80%, say), the extra is eliminated against the **Non-controlling Interest Account** declared on the group's root (a placeholder account until one is declared).
- **Difference**: what is left of each side. It goes to the group's **Intercompany Difference Account**, labelled with its cause (booking or fx). With no difference account, only the matched amount is eliminated and the difference stays on the intercompany accounts.

## Tolerance

Each consolidation group declares an **Intercompany Difference Tolerance** on its own row: the largest difference, at 100% in reporting currency, that is within tolerance. 0 is undeclared and shows as a setup gap. For an exact match, declare a small positive amount such as 0.01.

A booking difference is judged against the tolerance, and so is a balance-sheet pair across currencies (both sides at the closing rate). A profit-and-loss pair across currencies is an fx difference and is never judged. Any pair over tolerance turns a Green check run Amber: the Close Lead must type an acknowledgement to sign off.

## Unrealised profit

Profit on goods one group entity sold to another and still held in the buyer's inventory is eliminated from two records:

- an **IC Elimination Rule** of **Rule Type** `unrealized_profit` (maintained by the Close Lead), with a **Margin %** above 0, the **Debit Account** (the revenue side), the **Asset Account** (the inventory credited), and the seller in **Debit Entity Pattern** and the buyer in **Credit Entity Pattern** (an entity code, or `*` for any; a blank pattern matches nothing);
- an **IC Balance** for the seller, buyer and period, with **Ending Inventory from IC**.

The elimination is ending inventory × margin %. Limits:

- It is a flat margin per rule, and the inventory figure is the one entered on the IC Balance; it is not read from the trial balance.
- Only an approved IC Balance with inventory above 0 is eliminated. A draft one blocks sign-off until it is approved or deleted.
- An IC Balance that no rule matches, or that more than one rule matches, blocks sign-off.
- The elimination is in the group view only; it is not split with the minority owners.
- Each period's elimination is computed from that period's IC Balance on its own; it is not the change from the previous period.
- A buyer held by the equity method is not eliminated.
