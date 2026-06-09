# Konsol API Reference

All endpoints are served at `https://<site>/api/method/konsol.api.<endpoint>`.
Authentication: Frappe session cookie or API key/secret header.

---

## API Map

```
konsol.api
├── Health
│   └── GET  health                    # Health check (guest OK)
│
├── EPM Data Retrieval (ClickHouse proxy for Excel)
│   ├── GET  epm_value                 # Single value lookup
│   └── POST epm_batch                 # Batch value retrieval
│
├── Budget Write-Back
│   ├── POST budget_save               # Save full budget line
│   ├── POST budget_cell_save          # Save single cell (EPMSAVE)
│   └── POST budget_save_batch         # Save multiple budget lines
│
├── Consolidation (PRD-8, PRD-16)
│   ├── GET  get_hierarchy_tree        # Hierarchy as nested JSON
│   ├── POST approve_adjustment        # Approve topside journal
│   └── POST reverse_adjustment        # Reverse approved journal
│
└── Allocation (PRD-21)
    ├── POST run_allocation            # Create & execute allocation run
    ├── POST reverse_allocation        # Reverse active allocation run
    └── GET  allocation_history        # List runs with filters
```

---

## 1. Health

### `GET /api/method/konsol.api.health`

Health check. No authentication required.

**Response:**
```json
{"status": "ok", "app": "konsol"}
```

---

## 2. EPM Data Retrieval

### `GET /api/method/konsol.api.epm_value`

Single value lookup from ClickHouse gold tables. Used by Excel `EPMVALUE()` function.

**Parameters:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `entity` | string | yes | Entity/data_area_id (e.g. `"GBMF"`) |
| `year` | int | yes | Fiscal year (e.g. `2025`) |
| `period` | string | yes | `1`-`12`, `Q1`-`Q4`, `H1`-`H2`, or `FY` |
| `account` | string | yes | Main account code (e.g. `"40000"`) |
| `measure` | string | no | Column to return. Default: `period_net_amount` |
| `scenario` | string | no | `actuals` (default), `budget`, or `variance` |
| `cost_center` | string | no | Filter by cost center |
| `department` | string | no | Filter by department |
| `scenario_id` | string | no | Filter by scenario ID (e.g. `"BUDGET_2025"`) |

**Measures by scenario:**

| Scenario | Allowed measures |
|----------|-----------------|
| `actuals` | `period_debit`, `period_credit`, `period_net_amount`, `transaction_count`, `ytd_debit`, `ytd_credit`, `ytd_net_amount` |
| `budget` | `period_amount`, `annual_amount` |
| `variance` | `actual_amount`, `budget_amount`, `variance_abs`, `variance_pct`, `variance_favorable` |

**Response:**
```json
{"value": 125000.50}
```

**Example:**
```
GET /api/method/konsol.api.epm_value?entity=GBMF&year=2025&period=Q1&account=40000&scenario=actuals
```

---

### `POST /api/method/konsol.api.epm_batch`

Batch value retrieval. Sends up to 2000 requests in a single call. Requests sharing the same scenario/measure/period are grouped into a single ClickHouse query for efficiency.

**Body:** JSON array of request objects (same params as `epm_value`).

```json
[
  {"entity": "GBMF", "year": 2025, "period": "Q1", "account": "40000", "scenario": "actuals"},
  {"entity": "USMF", "year": 2025, "period": 6, "account": "50000", "measure": "period_debit"}
]
```

**Response:**
```json
{
  "values": [125000.50, 87000.00],
  "errors": [null, null]
}
```

Invalid items get inline errors (non-blocking — other items still return):
```json
{
  "values": [125000.50, null],
  "errors": [null, "Invalid measure 'bad' for scenario 'actuals'. Allowed: ..."]
}
```

---

## 3. Budget Write-Back

### `POST /api/method/konsol.api.budget_save`

Create or update a Budget Input document with all 12 period rows. Upserts by unique key: `(scenario_id, data_area_id, fiscal_year, main_account)`.

**Body:**
```json
{
  "scenario_id": "BUDGET_2025",
  "data_area_id": "GBMF",
  "fiscal_year": 2025,
  "main_account": "40000",
  "dim_cost_center": "CC001",
  "dim_department": "SALES",
  "periods": [
    {"period": 1, "amount": 10000, "layer": "base"},
    {"period": 2, "amount": 12000, "layer": "base"}
  ]
}
```

**Response:**
```json
{"name": "BUD-BUDGET_2025-GBMF-2025-40000"}
```

---

### `POST /api/method/konsol.api.budget_cell_save`

Save a single budget cell. Designed for `EPMSAVE()` immediate writes from Excel. Upserts one period+layer row within a Budget Input document.

**Body:**
```json
{
  "scenario_id": "BUDGET_2025",
  "data_area_id": "GBMF",
  "fiscal_year": 2025,
  "main_account": "40000",
  "fiscal_period": 3,
  "amount": 15000,
  "layer": "base"
}
```

**Valid layers:** `base`, `challenge`, `management`, `board`

**Response:**
```json
{"status": "ok", "name": "BUD-BUDGET_2025-GBMF-2025-40000", "value": 15000}
```

---

### `POST /api/method/konsol.api.budget_save_batch`

Save multiple budget lines at once. Each item follows the same schema as `budget_save`.

**Body:** JSON array of budget line objects.

**Response:**
```json
{
  "results": [{"name": "BUD-...", "index": 0}, null],
  "errors": [null, {"index": 1, "error": "Missing required fields: main_account"}]
}
```

---

## 4. Consolidation APIs

### `GET /api/method/konsol.api.get_hierarchy_tree`

Returns the consolidation hierarchy as a nested JSON tree (PRD-8).

**Parameters:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `consolidation_group` | string | no | Filter to a subtree rooted at this group |

**Response:**
```json
{
  "tree": [
    {
      "name": "CG-GROUP_CORP-GBMF",
      "consolidation_group": "GROUP_CORP",
      "data_area_id": "GBMF",
      "entity_name": "GB Manufacturing",
      "parent_group": null,
      "hierarchy_level": 1,
      "ownership_pct": 100,
      "consolidation_method": "full",
      "children": [
        {
          "consolidation_group": "GROUP_EMEA",
          "data_area_id": "DEMF",
          "entity_name": "DE Manufacturing",
          "parent_group": "GROUP_CORP",
          "hierarchy_level": 2,
          "ownership_pct": 80,
          "consolidation_method": "full",
          "children": []
        }
      ]
    }
  ]
}
```

---

### `POST /api/method/konsol.api.approve_adjustment`

Approve a Consolidation Adjustment that is in "Pending Approval" status (PRD-16).

**Parameters:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Document name (e.g. `"CADJ-IC001-0001"`) |

**Response:**
```json
{
  "status": "Approved",
  "approved_by": "admin@example.com",
  "approved_at": "2025-06-09 14:30:00"
}
```

**Errors:**
- `ValidationError` if current status is not "Pending Approval"

---

### `POST /api/method/konsol.api.reverse_adjustment`

Reverse an Approved Consolidation Adjustment (PRD-16). Creates a new reversal document with negated debit/credit amounts and links both documents via `reversal_journal_id`.

**Parameters:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Document name of the adjustment to reverse |

**What happens:**
1. New reversal doc created with swapped debit/credit amounts
2. Reversal doc auto-approved and submitted
3. Original doc marked as "Reversed"
4. Both linked via `reversal_journal_id`

**Response:**
```json
{
  "original": "CADJ-IC001-0001",
  "reversal": "CADJ-REV-IC001-0002",
  "status": "Reversed"
}
```

**Errors:**
- `ValidationError` if current status is not "Approved"

---

## 5. Allocation APIs

### `POST /api/method/konsol.api.run_allocation`

Create and execute an Allocation Run for a given fiscal period (PRD-21). The run is created, submitted, and synced to ClickHouse in one call.

**Parameters:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `fiscal_year` | int | yes | Fiscal year (e.g. `2025`) |
| `fiscal_period` | int | yes | Fiscal period (`1`-`12`) |

**What happens:**
1. New Allocation Run doc created
2. Submitted (triggers `before_submit`: sets `allocation_run_id`, `run_by`, `run_at`, `status=Active`)
3. Synced to `epm_staging.allocation_runs` via ClickHouse

**Response:**
```json
{
  "name": "ARUN-2025-P6-0001",
  "allocation_run_id": "ARUN-2025-P6-0001",
  "status": "Active",
  "run_by": "admin@example.com",
  "run_at": "2025-06-09 14:30:00"
}
```

---

### `POST /api/method/konsol.api.reverse_allocation`

Reverse an Active Allocation Run (PRD-21). Creates a new reversal run and cancels the original.

**Parameters:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Document name of the Allocation Run to reverse |

**What happens:**
1. New reversal run created with `reversal_of` linking to original
2. Reversal submitted (becomes Active)
3. Original cancelled (status set to "Reversed")
4. Both synced to ClickHouse

**Response:**
```json
{
  "original": "ARUN-2025-P6-0001",
  "reversal": "ARUN-2025-P6-0002",
  "status": "Reversed"
}
```

**Errors:**
- `ValidationError` if current status is not "Active"

---

### `GET /api/method/konsol.api.allocation_history`

List allocation runs with optional filters. Returns all runs (Active + Reversed) for audit trail.

**Parameters:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `fiscal_year` | int | no | Filter by fiscal year |
| `fiscal_period` | int | no | Filter by fiscal period |

**Response:**
```json
{
  "runs": [
    {
      "name": "ARUN-2025-P6-0002",
      "allocation_run_id": "ARUN-2025-P6-0002",
      "fiscal_year": 2025,
      "fiscal_period": 6,
      "status": "Active",
      "run_by": "admin@example.com",
      "run_at": "2025-06-09 15:00:00",
      "reversal_of": "ARUN-2025-P6-0001"
    },
    {
      "name": "ARUN-2025-P6-0001",
      "allocation_run_id": "ARUN-2025-P6-0001",
      "fiscal_year": 2025,
      "fiscal_period": 6,
      "status": "Reversed",
      "run_by": "admin@example.com",
      "run_at": "2025-06-09 14:30:00",
      "reversal_of": null
    }
  ]
}
```

---

## ClickHouse Sync Architecture

All DocTypes sync to ClickHouse staging tables via lifecycle hooks. This is the bridge between Frappe (source of truth) and dbt (analytics engine).

```
Frappe DocType                    ClickHouse Table                    Sync Trigger
─────────────────────────────────────────────────────────────────────────────────
Consolidation Group         →  gold.consolidation_groups              on_update, on_trash
                            →  epm_staging.consolidation_hierarchy    on_update, on_trash
Consolidation Adjustment    →  gold.consolidation_adjustments         on_update, on_trash
                            →  epm_staging.consolidation_adjustments  on_update, on_trash
IC Elimination Rule         →  gold.ic_elimination_rules              on_update, on_trash
                            →  epm_staging.ic_elimination_rules       on_update, on_trash
Allocation Rule             →  gold.allocation_rules                  on_update, on_trash
                            →  epm_staging.allocation_rules           on_update, on_trash
Allocation Driver           →  gold.allocation_drivers_*              on_update, on_trash
                            →  epm_staging.allocation_drivers         on_update, on_trash
Ownership Period            →  epm_staging.ownership_periods          on_submit, on_cancel, on_trash
Historical Equity Rate      →  epm_staging.historical_equity_rates    on_submit, on_cancel, on_trash
IC Balance                  →  epm_staging.ic_balances                on_submit, on_cancel, on_trash
Allocation Tier             →  epm_staging.allocation_tiers           via parent Allocation Rule
Allocation Run              →  epm_staging.allocation_runs            on_submit, on_cancel, on_trash
```

**Sync pattern:** Each DocType defines `CH_TABLE` + `CH_FIELD_MAP` constants. On trigger, `sync_doctype()` fetches ALL docs of that type and does TRUNCATE + INSERT (full refresh). Submittable DocTypes use `on_submit`/`on_cancel` instead of `on_update`.

**Dual sync:** Extended DocTypes (Consolidation Group, Adjustment, IC Rule, Allocation Rule/Driver) sync to BOTH legacy `gold.*` tables and new `epm_staging.*` tables for backward compatibility.

---

## Workflow States

### Consolidation Adjustment (PRD-16)

```
Draft ──[Submit for Approval]──→ Pending Approval ──[Approve]──→ Approved ──[Reverse]──→ Reversed
                                        │
                                        └──[Reject]──→ Draft
```

- **Draft**: Editable. Not synced to consolidated results.
- **Pending Approval**: Submitted. Awaiting controller review.
- **Approved**: Included in `gold_consolidation_adjustments`. `approved_by`/`approved_at` set.
- **Reversed**: Cancelled. Linked to reversal doc via `reversal_journal_id`.

### Allocation Run (PRD-21)

```
Draft ──[Run Allocation]──→ Running ──[Complete]──→ Active ──[Reverse]──→ Reversed
```

- **Draft**: Created but not executed.
- **Running**: Execution in progress.
- **Active**: Results included in `gold_allocation_results` (filtered by `status='Active'`).
- **Reversed**: Cancelled. Linked to reversal run via `reversal_of`.

### Budget Input (existing)

```
Draft ──[Submit for Review]──→ Submitted ──[Approve]──→ Approved
                                    │
                                    └──[Reject]──→ Rejected ──[Resubmit]──→ Submitted
```

---

## Error Handling

All endpoints follow Frappe conventions:
- **400 ValidationError**: Missing required fields, invalid parameters, wrong workflow state
- **403 PermissionError**: Insufficient role permissions
- **404**: Document not found
- **500**: ClickHouse connection timeout/failure (reported in `errors` array for batch endpoints)

Batch endpoints (`epm_batch`, `budget_save_batch`) use inline error reporting: invalid items get error messages in their position, valid items still return results.
