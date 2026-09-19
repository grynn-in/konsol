# Why it is shaped this way

Three things that are true about this product and are written down nowhere
else: why the boundaries fall where they do, what was tried and abandoned, and
the order things have to be built in.

They are the questions a second engineer asks in week one, and the questions a
rebuild would answer wrongly. Everything here is reconstructed from the code
and from the issues cited; where a claim is judgement rather than measurement,
it says so.

Companion documents: the decisions register at the top of `HANDOFF.md` (what
was decided, by whom, and what was rejected), and konsolidat's
`docs/design/rules.md` (the 126 rules the warehouse enforces, and why).

---

## 1 · Why the boundaries are where they are

### konsol declares. ClickHouse computes.

The single load-bearing sentence. konsol never calculates a consolidated
number — it states facts and policy, and the warehouse derives everything from
them.

The reason is not performance. It is that **a derived number can be rebuilt and
a stored one cannot be trusted.** Anything konsol computed and saved would
immediately be a second source of truth, and the first question about any
figure would become "is this stale?". Because nothing is stored, the question
does not exist: drop the warehouse, rebuild it, and the same inputs give the
same answer.

The cost is that every fix is a model change plus a rebuild, and that a wrong
input produces a wrong answer everywhere at once rather than in one place. That
trade was taken deliberately.

### Three kinds of doctype, and how to tell which you need

| kind | example | what it means | how it is enforced |
|---|---|---|---|
| **plain** | `Entity`, `Main Account` | reference data; latest value wins | validation only |
| **governed** | `Consolidation Group`, `Dimension`, `Reporting Hierarchy` | Draft → Published → Inactive, write-through to ClickHouse on publish | `GovernedReferenceDocument` |
| **submittable** | `Ownership Period`, `Trial Balance Submission`, `Business Combination`, `Group Exchange Rate` | **submit is the approval**; the row reaches the warehouse only at `docstatus = 1` | `before_submit` / `on_submit`, and a Frappe Workflow where a second pair of eyes is needed |

The test is one question: **does this change a number that someone has already
reported?**

- No → plain or governed. A chart of accounts gains a row; nothing restates.
- Yes → submittable. An ownership percentage, an exchange rate, a trial
  balance: each one moves a figure that may already be in a board pack, so it
  needs a moment where somebody accepted responsibility. That moment is submit.

This is why **`Consolidation Group` is not submittable and `Ownership Period`
is.** The group tree is shape — which entities exist and how they nest.
Ownership is arithmetic — the percentage that multiplies every line of a
subsidiary's trial balance. Editing shape does not restate anything by itself;
editing a percentage restates everything below it.

### Why time lives in two different places

This looks inconsistent and is not.

    Reporting Hierarchy Member
      member_code · parent_member · effective_from · effective_to
      the dated row IS the fact. a rename or a move is one more tranche.

    Consolidation Group                         NO DATES ON THE NODE
      parent_consolidation_group · is_group · data_area_id
      reporting_currency · <6 policy fields> · <9 declared accounts>

    Ownership Period                            submittable
      data_area_id · effective_date · end_date
      ownership_pct · consolidation_method
      acquisition_date · acquisition_price · fair_value_adjustment
      is_disposal · disposal_date · disposal_price

**A change of ownership is an event, not an edit.** It has a date, a price and
a counterparty, and somebody has to approve it. You can approve a *document*;
you cannot approve a tree-node edit. So the tree holds the shape and a
submittable document holds the history.

A division moving between reporting groups has no price and nobody to approve.
A dated row is enough.

### Why there are two trees

`Consolidation Group` is over legal entities and **changes the numbers**:
moving an entity changes its ownership percentage, its consolidation method,
its reporting currency and its elimination scope. `Reporting Hierarchy` is over
a `Dimension` and **changes nothing**: the same amount appears under a
different subtotal.

    does the grouping change a number?
      YES → Consolidation Group.  time in Ownership Period.  submit = approval.
      NO  → Reporting Hierarchy.  time on the member row.    publish is enough.

A consolidation node has exactly one parent, which is why a 50/50 joint venture
is currently inexpressible (konsol#117). Reporting hierarchies have no such
limit — another sign they solve a different problem.

### Why the finest grain goes on the fact row

Put a rollup on a fact row and you freeze an opinion into history. A fact row
is history and cannot change after close; a hierarchy is an opinion and can be
versioned and dated. If a sub-division moves to another division in 2024, a
dated hierarchy reports both sides correctly and a `dim_division` column on
every 2019 row does not — you would have to restate the trial balance to fix an
org chart.

Corollary: two dimensions are right only when a row can genuinely be any
combination of both. If one determines the other, it is one dimension and a
tree.

### Why undeclared input is refused rather than absorbed

    undeclared period     → PeriodNotDeclared
    undeclared account    → refused at upload
    undeclared entity     → refused at upload
    debits ≠ credits      → that entity-period refused
    closed period         → submit refused; for a trial balance, even the save
    missing rate          → the period cannot leave Open

The principle is konsol#247: **refuse what makes a number wrong, report what
makes it unexplained, and never let data create configuration.** A wrong
currency makes every figure wrong, so it is refused. A missing cost centre
leaves a figure unexplained but not wrong, so it is reported.

The third clause is the one people push against. A loader that creates a
missing account "to be helpful" has let a typo become a permanent part of the
chart, and nobody will ever know which accounts were declared and which were
invented.

---

## 2 · What was tried and abandoned

A rebuild would re-propose every one of these. They are recorded so the
argument does not have to be had twice.

### Cube, as the read path — rejected on measurement

**konsol#232.** Cube is deployed and idle: 8 hand-written schema files, **zero
pre-aggregations** (the feature it would be adopted for), and **zero calls from
konsol** — nothing references port 4000. Every Excel read goes straight to
ClickHouse from `api.py`.

Measured on the loaded group (45,928 rows in `gold_consolidated_trial_balance`)
through the real batched path: **500 cells in 52 ms**. There is no latency
problem for Cube to solve, and adding it would put a second semantic layer
beside the Dataset registry.

Rejected on evidence, not taste. Re-propose it only with a measurement that
shows the direct path failing.

### Airbyte as an in-product ELT layer — moved outside the boundary

**konsolidat#90, then konsol#218.** The local setup used `abctl`, which
provisions Airbyte into a throwaway kind cluster on its own Docker network. The
bridge to the app network **drops on every host reboot**, API credentials are
random per install and hand-pasted into EPM Settings, and workspace IDs are
found by hand in the UI.

Those are `abctl` artefacts rather than design faults — but the conclusion held
for a different reason: under a multi-tenant direction, ELT does not belong
inside the product. The seam moved:

> **Airbyte normalises structure. konsol applies meaning.**

Airbyte (or anything else) delivers a thin fixed-shape CSV; konsol maps nothing
and refuses what it has not declared.

### A configurable Source Profile — rejected 16 Sep 2026

The natural next step from the above is a per-customer mapping layer: your
column `CCTR` means cost centre, your account `1000` means `10010`. It was
rejected in favour of **a fixed canonical contract** (konsol#218 Decision 1).

The reasoning: a mapping layer is a second place where meaning lives, it is
per-customer by construction, and it makes every support question start with
"what does this site's profile say?". The chart of accounts is the precedent —
every site's is different, and nobody calls the contract configurable because
of it.

`Dimension.source_column` survives as the ghost of this idea, and a patch
(`fix_dimension_source_column_drift.py`) actively drives it back to equal
`dimension_name`. It should be deleted, not left to invite a future site to put
a source name in it.

### The ERP staging tree — being removed

**konsolidat#221, 17 Sep 2026.** The D365 and ERPNext staging tree and the
bronze layer that depends on it are going. Standing rule: **do not repair a
defect whose only site is inside that tree — close it citing #221.**

It is the last step of a direction already taken piece by piece: the chart
moved to konsol (konsol#182), then group rates, then intercompany, then the
calendar. Each move left the ERP path supplying less, until it supplied
nothing.

What it taught, and what a rebuild should not re-learn: a staging layer shaped
like the *source* rather than the *destination* has to be rewritten for every
new source, and it is where per-customer logic accumulates because that is the
only place it fits.

### The VBA Excel client — retired

**konsol#153, konsolidat#163.** The Office add-in
(`konsol/public/excel-addin`) is the only Excel client. Thirteen VBA tests read
`OpenEPM.bas` from a path that no longer existed and **skipped on every run**
while appearing to pass — an early instance of the failure konsol#248 describes
at scale.

### dbt seeds as configuration — replaced by doctypes

**konsolidat#149.** Reference data used to live in `seeds/*.csv` and arrive via
`dbt seed`. It now lives in konsol doctypes and reaches the warehouse by
write-through. Ten documentation files still describe the old way.

The reason is the one that runs through everything here: a CSV in a repository
cannot be approved, cannot be permissioned, and cannot be audited. A doctype
can.

### Allocation — removed and to be redesigned, 19 Sep 2026

**konsol#264.** A management-accounting allocation engine existed in konsolidat,
driven by `Allocation Rule`, `Allocation Driver` and `Allocation Run` doctypes.
It is being removed.

The reasoning is worth keeping because it generalises: what was built was close
to a duplicate of what an ERP cost accounting module already does, and the
capabilities that would justify owning one were not there. Measured before
deciding — `allocation_rules`, `allocation_drivers`, `allocation_runs` and
`allocation_tiers` were all **empty on the live site**, so removal was as cheap
as it would ever be.

The test this sets: **if a feature duplicates what the source system already
does, and nothing distinguishes our version, it is a liability rather than an
asset** — it has to be maintained, it appears in every build, and it competes
for attention with the consolidation path that is the actual product.

### A per-row ownership marker on the semantic model — rejected 18 Sep 2026

When splitting `fixtures/` by mutability (konsol#230), the obvious design was a
per-row `owner = product | site` flag. Rejected: **"structural" is a
dependency, not an ownership property.** A static marker encodes today's
dependency graph into data and drifts from it at the next model change. A
publish-time check that refuses a state where a model references a missing
measure derives the same answer and cannot drift.

---

## 3 · The order things must be built in

Most work here is independent. These are the pairs where order is not a
preference.

### The general shape

> **A deletion must be preceded by the fix that makes the empty case work.**

Every instance below is that sentence. The product ships defaults; removing a
default exposes a path nothing has ever taken.

### The dependencies that exist today

    konsolidat#220  →  konsol#230
      40 of 48 dim_select() call sites carry a trailing comma, so a site with
      ZERO dimensions cannot compile any model. konsol#230 deletes the shipped
      dimensions. In the wrong order, every build breaks and nobody can tell
      whether the deletion or the macros caused it.

    konsol#253  →  konsolidat#209
      the currency-scaled materiality floor derives from ISO minor units, which
      needs a currency on the fact row. #253 adds the column.

    konsolidat#171  →  partial_period_treatment  →  konsolidat#169, #170
      all three partial-period treatments need ownership judged at the period's
      end_date from the declared calendar, not the first of the month. The
      boundary fix is required whichever treatment a group elects.

    konsol#258  →  any orchestrator work
      definition=None silently falls back to plan.DEFAULT_DEFINITION, and the
      `silver` and `gold` steps are the same bare `dbt run`. Wiring
      pipeline_definition first ships a double full build on a schedule.

    konsol#255  →  Reporting Hierarchy being useful at all
      every dim_* column downstream of the trial balance is structurally
      unfillable until the intake can carry a declared dimension, so a
      hierarchy rolls up zero.

### The two that gate everything else

    konsol#248        the host test runner reports N/N passed when a whole file
                      stops importing — it printed 2328/2328 while 15 hierarchy
                      tests had silently gone.

    konsolidat#181    no CI dbt build against a throwaway ClickHouse. `dbt parse`
                      does not validate SQL.

Until both land there is **no reliable signal that anything works**, which is
why every other list keeps growing. They are also what makes the 126 assertions
worth what they appear to be worth: an assertion that has not run in three
months is documentation, not protection.

### What is deliberately not ordered

Everything else. The triage on konsol#216 groups the open work by what it
stops — cannot install for a second customer, makes a number wrong, a group
that cannot be modelled, cannot be operated safely — and within a tier the
order is a matter of appetite, not correctness.
