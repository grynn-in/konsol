---
title: Cost allocation
audience: Group Accountant
section: Concepts
order: 160
---
Cost allocation moves the cost of service departments (IT, HR, facilities) to the departments they serve. It works inside one entity. It is management reporting only: it never changes the trial balance, never posts to your ERP, and never touches the consolidated numbers. It runs as its own build, so a problem in allocation cannot stop a close.

Everything lives in **Planning → Allocation**.

## What you declare

- **Allocation Dimension** on the rule set: the dimension that holds your departments, for example Cost Center. It must be Published and In Budget.
- **Driver**: the basis of the split, for example Headcount or IT Tickets. A driver names its dimension, optionally a unit, and its **Source**: where its values come from (see the next section).
- **Statistic**: the driver's values for one entity, scenario and fiscal year, one value per period and department. Upload them from a CSV file (`fiscal_period,member,value`) with **Load from File**. A budget run needs a budget-scenario statistic, even when the values equal actuals. Approve the statistic before you use it. Only a driver whose Source is Uploaded Statistic takes statistics.
- **Rule Set**: the method, the cost pool accounts, the steps (who sends, by which driver, on which **Basis**: a share of the pool or a declared rate) and each step's receivers. Two choices on the rule set decide the treatment:
  - **Receiver Account**: *Sender's Own Accounts* keeps the cost on the account it was booked on (rent stays rent). *Declared Allocation Account* moves received cost onto one account you name, for example "Allocated Shared Cost".
  - **Amounts After Approval**: *Follow Latest Data* recomputes the amounts whenever the trial balance or budget changes. *Freeze at Approval* keeps the amounts of the first build after approval. If the data changes later, a warning reports the difference; cancel and re-approve the run to refresh it.

## Where a driver's values come from

Every driver has a **Source**. It decides where **Compute Weights** finds the values it turns into shares.

- **Uploaded Statistic**: a statistic per entity, scenario and fiscal year, loaded from a CSV file, as described above. Use it for values that live outside the ledger and change with the scenario, for example tickets or square metres.
- **Fixed Weights**: shares you declare on the driver, one row per department with **Effective From** and **Effective To** dates. They apply to every scenario: a budget run and an actual run read the same rows. A window must cover the whole period. If a department's share changes inside a period, Compute refuses and names the department, so start a new window on the first day of a period. Leave Effective To blank for a share that still applies.
- **Account Set**: values read from the trial balance when you compute: the run's entity, scenario, fiscal year and period, on the driver's accounts and their descendants. **Value Sign** says how to read them: *As Booked*, or *Reversed* to turn a credit balance positive, which you need for revenue. The driver's dimension must be In Budget, because the scenario trial balance carries only budget dimensions. A department with no trial balance rows is missing and refuses the run. A zero is a real zero: the department receives nothing. A negative value after the sign refuses the run; pick the other sign or a different account set.

Whatever the source, the weights are fixed when the run is computed and stored on the run, with the window or the accounts they came from. Approval checks them again: if the fixed weights or the trial balance changed in between, approval refuses and asks you to compute again. After approval the run keeps its weights. If the trial balance moves later, the warehouse reports a warning that names the run; it does not change the approved run. **Amounts After Approval** still governs the pool amounts, not the weights.

Switching a driver's Source asks before it removes the rows entered for the old source. A driver whose Source is not Uploaded Statistic refuses a statistic.

In the worked example below, Headcount could be a Fixed Weights driver: HR 2, Sales 5, Ops 3, each Effective From the first day of the fiscal year. Compute gives the same shares in every period and scenario until you add a new window. If IT's cost should instead follow each division's revenue, make a driver with Source Account Set, the revenue accounts, and Value Sign Reversed: a department that booked 500 of revenue counts 500, not −500.

## Methods

- **Direct**: each sender gives its cost straight to the departments it serves. A sender may not receive, so service departments are never charged for each other.
- **Step-Down**: use it when one service department also uses another's services (HR uses IT). Senders go in step order: put the department that serves the others first. A sender passes on its own cost plus whatever earlier steps gave it, and receives nothing after its own step.
- **Reciprocal**: use it when service departments serve each other, for example IT supports HR and HR recruits for IT. Konsol solves the exchange exactly, with no iterations and no rounding plug. Step order does not matter. A department may not serve itself. A group of departments whose cost can never reach a department outside the group is refused, and the refusal names them.

## Charging a declared rate

Each rule has a **Basis**. It decides how much the sender charges.

- **Share of Pool**: the sender's whole pool is split over its receivers by the driver. This is the treatment in the examples below. The sender ends at zero.
- **Declared Rate**: the sender charges a rate per driver unit, for example 50 per IT ticket. A receiver with 8 tickets is charged 400, whatever the pool. Charges are exact: rate × units, with no rounding.

Rates live on the rule set, in the **Rates** table, one row per step and validity window (**Effective From** and **Effective To**). A window must cover the whole period. If a rate changes inside a period, Compute refuses and names the step, so start a new window on the first day of a period. Leave Effective To blank for a rate that still applies.

A rule set with a Declared Rate rule must use **Declared Allocation Account**. The receivers are charged on that account, the sender is credited there for what it charged, and the sender's own costs stay on its own accounts. *Sender's Own Accounts* is refused for a rate rule, because a fixed charge cannot be matched to the accounts the pool was booked on.

A rate sender rarely charges exactly its pool. The difference is its **absorption variance**: pool + cost received − charged. Positive means under-absorbed (it charged less than it cost), negative means over-absorbed. The variance is reported per rate sender, on the run. It is never spread to receivers.

The methods treat a rate sender like this:

- **Step-Down**: a rate sender's charge is fixed, so cost it receives from earlier steps does not flow on. It stays with the rate sender and shows in its variance. A share sender in a later step passes on a rate charge like any other cost it received.
- **Reciprocal**: rate senders are exits from the solve. Their charges are fixed, so they take no part in the exchange; the solve covers the share senders only.

Take the worked example below with IT as a rate sender instead. IT's pool is 1,000 and its rate is 50 per ticket. Tickets: HR 5, Sales 8, Ops 6. HR stays a share sender by headcount, 5/8 to Sales and 3/8 to Ops.

| step | from → to | basis | units | charged |
|---|---|---|---|---|
| 1 | IT → HR | 50 per ticket | 5 | 250 |
| 1 | IT → Sales | 50 per ticket | 8 | 400 |
| 1 | IT → Ops | 50 per ticket | 6 | 300 |
| 2 | HR → Sales | share 5/8 of 650 | | 406.25 |
| 2 | HR → Ops | share 3/8 of 650 | | 243.75 |

IT charges 950 against a pool of 1,000, so its absorption variance is +50, under-absorbed. HR holds its own 400 plus the 250 charged by IT, 650, and splits it.

| department | before | after |
|---|---|---|
| IT | 1,000 | 50 (variance) |
| HR | 400 | 0 |
| Sales | 0 | 806.25 |
| Ops | 0 | 543.75 |
| Total | 1,400 | 1,400 |

Sales receives 400 from IT and 406.25 from HR. Ops receives 300 from IT and 243.75 from HR. The 50 that IT did not charge stays with IT as its variance, so the total is still 1,400. Had the rate been 60, IT would have charged 1,140 and its variance would be −140, over-absorbed.

The rate is fixed when the run is computed and stored on the run's weights, with the window it came from. Approval checks it again: if the rate changed in between, approval refuses and asks you to compute again.

## A worked example

One entity. IT supports everyone with a laptop, including HR's own staff. HR serves the two divisions, Sales and Ops.

```
Company
├─ Shared Services
│  ├─ IT     cost 1,000   (account 6000)   serves HR, Sales, Ops
│  └─ HR     cost   400   (account 6000)   serves Sales, Ops
└─ Divisions
   ├─ Sales
   └─ Ops
```

Driver: Headcount (HR 2, Sales 5, Ops 3). Rule set: Step-Down, Sender's Own Accounts.

- Step 1: IT sends to HR, Sales and Ops, by headcount: 2/10, 5/10 and 3/10.
- Step 2: HR sends to Sales and Ops, by headcount: 5/8 and 3/8.

**Compute Weights** on the run turns the statistic into those shares. It refuses if any department is missing a value and lists every gap. **Preview Sender Pools** shows the cost to be allocated: IT 1,000, HR 400.

After approval, the allocation lines are:

| step | from → to | sent | received |
|---|---|---|---|
| 1 | IT → HR | IT −200 | HR +200 |
| 1 | IT → Sales | IT −500 | Sales +500 |
| 1 | IT → Ops | IT −300 | Ops +300 |
| 2 | HR → Sales | HR −375 | Sales +375 |
| 2 | HR → Ops | HR −225 | Ops +225 |

HR receives 200 of IT's cost because IT supports HR's 2 people too. Step-Down charges HR for that first, and HR then passes on 600: its own 400 plus the 200 from IT. If IT served only Sales and Ops, you would leave HR off IT's receivers, which is the Direct method.

| department | before | after |
|---|---|---|
| IT | 1,000 | 0 |
| HR | 400 | 0 |
| Sales | 0 | 875 |
| Ops | 0 | 525 |
| Total | 1,400 | 1,400 |

The total is conserved to the cent. Allocation never creates or loses cost.

- With **Declared Allocation Account** 6999: IT and HR are relieved on 6000, and Sales and Ops receive the cost on 6999.
- With **Freeze at Approval**: if IT's cost later becomes 1,200, a Follow Latest Data run moves to Sales 975 and Ops 585. A frozen run stays at 875 and 525, and the drift warning names the run.

## A reciprocal example

Same entity. IT and HR serve each other, and both serve Sales and Ops. Rule set: Reciprocal. IT sends its cost by **IT Tickets**. HR sends its cost by **Headcount**.

- IT: own cost 1,000, sent to HR 5, Sales 3, Ops 2 (50%, 30%, 20%).
- HR: own cost 400, sent to IT 4, Sales 4, Ops 2 (40%, 40%, 20%).

Each department's total is its own cost plus its share of the other's total. IT's total is 1,000 plus 40% of HR's total (450), so 1,450. HR's total is 400 plus 50% of IT's total (725), so 1,125.

The six gross lines are:

| from → to | amount | own | pass |
|---|---|---|---|
| IT → HR | 725 | 500 | 225 |
| IT → Sales | 435 | 300 | 135 |
| IT → Ops | 290 | 200 | 90 |
| HR → IT | 450 | 160 | 290 |
| HR → Sales | 450 | 160 | 290 |
| HR → Ops | 225 | 80 | 145 |

Each line is split into `own` (the sender's own cost) and `pass` (cost it received and passes on). The `own` column adds to 1,400, the cost the two departments started with. In **Declared Allocation Account** mode, only `own` cost is relieved on the pool account.

The lines include IT and HR charging each other. Each service department still ends at zero, because what it receives it passes on.

| department | before | after |
|---|---|---|
| IT | 1,000 | 0 |
| HR | 400 | 0 |
| Sales | 0 | 885 |
| Ops | 0 | 515 |
| Total | 1,400 | 1,400 |

Sales receives 435 from IT and 450 from HR, so 885. Ops receives 290 from IT and 225 from HR, so 515.

## Approval and the period

A run covers one rule set, scenario and period. **Compute Weights** works only in Draft. Approving the run checks that:

- the weights were computed for this scenario and period;
- the rule set, its rates and the driver values (statistics, fixed weights, or the trial balance an Account Set driver reads) have not changed since;
- no other approved run already relieves the same sender.

A closed period blocks approving or cancelling a run, and cancelling a statistic, only for actual scenarios: budget and forecast allocation stay open. A statistic that an approved run uses cannot be cancelled. Cancel the run first.
