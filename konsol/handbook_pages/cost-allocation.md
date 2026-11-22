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
- **Driver**: the basis of the split, for example Headcount or IT Tickets. A driver names its dimension and, optionally, a unit.
- **Statistic**: the driver's values for one entity, scenario and fiscal year, one value per period and department. Upload them from a CSV file (`fiscal_period,member,value`) with **Load from File**. A budget run needs a budget-scenario statistic, even when the values equal actuals. Approve the statistic before you use it.
- **Rule Set**: the method, the cost pool accounts, the steps (who sends, by which driver) and each step's receivers. Two choices on the rule set decide the treatment:
  - **Receiver Account**: *Sender's Own Accounts* keeps the cost on the account it was booked on (rent stays rent). *Declared Allocation Account* moves received cost onto one account you name, for example "Allocated Shared Cost".
  - **Amounts After Approval**: *Follow Latest Data* recomputes the amounts whenever the trial balance or budget changes. *Freeze at Approval* keeps the amounts of the first build after approval. If the data changes later, a warning reports the difference; cancel and re-approve the run to refresh it.

## Methods

- **Direct**: each sender gives its cost straight to the departments it serves. A sender may not receive, so service departments are never charged for each other.
- **Step-Down**: use it when one service department also uses another's services (HR uses IT). Senders go in step order: put the department that serves the others first. A sender passes on its own cost plus whatever earlier steps gave it, and receives nothing after its own step.
- **Reciprocal**: use it when service departments serve each other, for example IT supports HR and HR recruits for IT. Konsol solves the exchange exactly, with no iterations and no rounding plug. Step order does not matter. A department may not serve itself. A group of departments whose cost can never reach a department outside the group is refused, and the refusal names them.

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
- the rule set and the statistics have not changed since;
- no other approved run already relieves the same sender.

A closed period blocks approving or cancelling a run, and cancelling a statistic, only for actual scenarios: budget and forecast allocation stay open. A statistic that an approved run uses cannot be cancelled. Cancel the run first.
