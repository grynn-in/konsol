---
title: Upload and submit your trial balance
audience: Entity Accountant
section: Start here
order: 10
---
You send each entity's trial balance (TB) for each period, for the entities assigned to you.

## Upload and submit

1. Open **Trial Balances** (Trial Balance Submission) and add a new one.
2. Choose the **Entity**, **Fiscal Year** and **Fiscal Period**.
3. Choose the **Amount Basis**: what each row's debit and credit are. See *Trial balance amount basis* under Concepts. A wrong choice doubles or halves every balance.
4. Attach the CSV in **Trial Balance CSV** and save. Konsol checks the file when you save.
5. If the save succeeds, submit. Submitting sends the trial balance to the warehouse and requests a build. The Close Lead approves that build in Build Approval.

The CSV has a header row:

```
main_account,debit,credit,currency[,description][,partner_data_area_id][,amount_basis]
```

- `currency` is on every row and must be the entity's functional currency.
- `partner_data_area_id` is the other group entity an intercompany row is held with, for example `ZZ02`. Leave it blank for a third party.
- `amount_basis`, when you give it, must agree with the form.
- One row per account, partner and declared dimension value.

## When the file is refused

Nothing is saved, and the message lists every problem. Fix the CSV, attach it again and save. The usual causes:

- the period is not declared, or is not Open;
- you are not assigned to the entity;
- another trial balance is already submitted for the entity and period;
- the Close Lead declared a TB Exception for the entity and period;
- the `currency` column is missing, differs between rows, or is not the entity's functional currency;
- an amount has more decimal places than the currency allows, or total debits do not equal total credits;
- an account is not a Published account of the group chart, or is a heading;
- two rows have the same account, partner and dimension values;
- a negative amount: post it in the other column instead;
- a partner that is not an existing, non-group entity, or that is the entity itself.

Two things are warnings, not refusals. The trial balance is saved, and the warning is kept on it:

- an intercompany row without a partner: it loads, but it is never eliminated and is listed as unmatched;
- a partner on an account that does not carry intercompany.

## Correcting a submitted trial balance

While the period is Open, cancel the trial balance, then **Amend** it: amend makes a new draft from the cancelled one. Attach the corrected file, save and submit. After the period is closed, a submitted trial balance cannot be cancelled.

## TB Exceptions

If an entity has no trial balance for a period (for example, it is dormant), ask the Close Lead to declare a **TB Exception** with the reason. Sign-off accepts a submitted exception in place of the trial balance. While one is submitted, a trial balance for that entity and period is refused until the Close Lead cancels the exception.

**My Work** lists what is waiting for you. **Period Close** shows the state of each period.
