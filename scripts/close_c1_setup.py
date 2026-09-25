"""konsol#305 C1 setup: the data the close walk-through needs, on a live site.

Run INSIDE the backend container, from the sites directory:

    docker cp scripts/close_c1_setup.py konsolidat_backend:/home/frappe/frappe-bench/
    docker exec -u frappe konsolidat_backend bash -lc \
        'cd /home/frappe/frappe-bench/sites && ../env/bin/python ../close_c1_setup.py'

It records every prior value it changes in ``zz_c1_state.json`` (next to the
script) BEFORE changing it; ``close_c1_cleanup.py`` reads that file back. It
refuses to run when anything it would create already exists (FY2099, ZZOP,
a zz-c1 user, or a state file): it never deletes or overwrites a
pre-existing record.

What it does (tasks.md C1 step 1; Problems 14 — site-wide changes):
- EPM Fiscal Year 2099, Monthly (12), no Opening or Closing period;
- Close Settings first close = 2099 / 1 (the prior tabSingles rows recorded);
- entity ZZOP (leaf, Active, USD, Monthly), its Consolidation Group node under
  the root group and a submitted Ownership Period from 2099-01-01;
- reporting_frequency "Monthly" on every other in-scope entity whose value
  is blank (the names recorded; set without touching ``modified``);
- a submitted TB Exception for every other in-scope entity for 2099 P1 and P2;
- Group Exchange Rates for every pair ``group_rates.required_pairs(2099, p)``
  names (P1, P2);
- users zz-c1-ea (Entity Accountant, User Permission Entity = ZZOP),
  zz-c1-analyst (EPM Analyst), zz-c1-lead (EPM Admin), zz-c1-viewer (EPM User).
"""
import datetime
import json
import os

import frappe

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "zz_c1_state.json")

FY = 2099
PERIODS = (1, 2)
ENTITY = "ZZOP"
GROUP = "ECL_GROUP"
CURRENCY = "USD"
PASSWORD = "Zz-c1-Pass!2026"
REASON = "C1 walk-through"
USERS = {
    "zz-c1-ea@example.com": ["Entity Accountant"],
    "zz-c1-analyst@example.com": ["EPM Analyst"],
    "zz-c1-lead@example.com": ["EPM Admin"],
    "zz-c1-viewer@example.com": ["EPM User"],
}

#: Every doctype setup or the walk-through touches; counted before and after.
COUNTED = ("EPM Fiscal Year", "Entity", "Consolidation Group", "Ownership Period", "TB Exception",
           "Trial Balance Submission", "Group Exchange Rate", "Assertion Run", "Pipeline Run",
           "User", "User Permission", "Build Approval", "File", "Trial Balance Upload")


def counts():
    return {dt: frappe.db.count(dt) for dt in COUNTED}


def refuse_if_present():
    problems = []
    if frappe.db.exists("EPM Fiscal Year", str(FY)) or frappe.db.exists("EPM Fiscal Year", {"fiscal_year": FY}):
        problems.append(f"EPM Fiscal Year {FY} already exists")
    if frappe.db.exists("Entity", ENTITY):
        problems.append(f"Entity {ENTITY} already exists")
    for dt in ("Trial Balance Submission", "TB Exception", "Group Exchange Rate", "Assertion Run"):
        n = frappe.db.count(dt, {"fiscal_year": FY})
        if n:
            problems.append(f"{n} {dt} row(s) already in FY{FY}")
    for u in USERS:
        if frappe.db.exists("User", u):
            problems.append(f"User {u} already exists")
    if os.path.exists(STATE):
        problems.append(f"{STATE} exists: run close_c1_cleanup.py first")
    if problems:
        raise SystemExit("REFUSED, nothing changed:\n- " + "\n- ".join(problems))


def save_state(state):
    with open(STATE, "w") as f:
        json.dump(state, f, indent=1, default=str)


def main():
    frappe.init(site="konsolidat.local", sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()
    frappe.set_user("Administrator")
    refuse_if_present()

    state = {
        "started_at": frappe.utils.now(),
        "counts_before": counts(),
        "close_settings_singles": [list(r) for r in frappe.db.sql(
            "select field, value from tabSingles where doctype='Close Settings' order by field")],
        "build_approvals_before": frappe.get_all("Build Approval", pluck="name", limit_page_length=0),
        "pipeline_runs_before": frappe.get_all("Pipeline Run", pluck="name", limit_page_length=0),
        "files_before": frappe.get_all("File", pluck="name", limit_page_length=0),
        "created": {"fiscal_year": None, "entity": None, "cg_node": None, "ownership_period": None,
                    "tb_exceptions": [], "rates": [], "users": [], "user_permissions": []},
        "frequency_set": {},
    }
    save_state(state)  # prior values are on disk before the first change

    # 1. FY2099: generate_periods is the only way to create a year.
    fy = frappe.get_doc({
        "doctype": "EPM Fiscal Year", "fiscal_year": FY,
        "start_date": f"{FY}-01-01", "end_date": f"{FY}-12-31",
        "period_pattern": "Monthly (12)", "include_opening_period": 0, "include_closing_period": 0,
    })
    state["created"]["fiscal_year"] = fy.generate_periods()
    frappe.db.commit()
    save_state(state)

    # 2. First close period.
    cs = frappe.get_single("Close Settings")
    cs.first_close_fiscal_year = FY
    cs.first_close_fiscal_period = 1
    cs.save()
    frappe.db.commit()

    # 3. ZZOP, its group node and its ownership.
    ent = frappe.get_doc({
        "doctype": "Entity", "entity_code": ENTITY, "entity_name": "ZZ C1 Operating",
        "is_group": 0, "status": "Active", "functional_currency": CURRENCY,
        "reporting_frequency": "Monthly", "country": "US",
    }).insert()
    state["created"]["entity"] = ent.name
    frappe.db.commit()
    save_state(state)

    root = frappe.db.get_value("Consolidation Group",
                               {"consolidation_group": GROUP, "parent_consolidation_group": ["is", "not set"]},
                               "name")
    node = frappe.get_doc({
        "doctype": "Consolidation Group", "consolidation_group": GROUP, "data_area_id": ENTITY,
        "entity_name": "ZZ C1 Operating", "parent_consolidation_group": root, "is_group": 0,
        "reporting_currency": CURRENCY,
    }).insert()
    state["created"]["cg_node"] = node.name
    frappe.db.commit()
    save_state(state)

    op = frappe.get_doc({
        "doctype": "Ownership Period", "consolidation_group": GROUP, "data_area_id": ENTITY,
        "effective_date": f"{FY}-01-01", "ownership_pct": 100, "consolidation_method": "full",
    }).insert()
    op.submit()
    state["created"]["ownership_period"] = op.name
    frappe.db.commit()
    save_state(state)

    # 4. Every other in-scope entity: a frequency where blank, and exceptions.
    from konsol.close import signoff_gate
    others = [e for e in signoff_gate.in_scope_entities(FY, 1) if e != ENTITY]
    state["in_scope_others"] = others
    for name in others:
        prior = frappe.db.get_value("Entity", name, ["reporting_frequency", "modified"], as_dict=True)
        if not prior.reporting_frequency:
            state["frequency_set"][name] = {"reporting_frequency": prior.reporting_frequency,
                                            "modified": str(prior.modified)}
    save_state(state)  # every prior value recorded before any is changed
    for name in state["frequency_set"]:
        frappe.db.set_value("Entity", name, "reporting_frequency", "Monthly", update_modified=False)
    frappe.db.commit()

    for p in PERIODS:
        for name in others:
            exc = frappe.get_doc({"doctype": "TB Exception", "data_area_id": name,
                                  "fiscal_year": FY, "fiscal_period": p, "reason": REASON}).insert()
            exc.submit()
            state["created"]["tb_exceptions"].append(exc.name)
        frappe.db.commit()
        save_state(state)

    # 5. Rates the close's rate gate asks for.
    from konsol import group_rates
    state["required_pairs"] = {}
    for p in PERIODS:
        pairs = sorted(group_rates.required_pairs(FY, p))
        state["required_pairs"][p] = pairs
        for frm, to in pairs:
            for rate_type in ("Average", "Closing"):
                r = frappe.get_doc({"doctype": "Group Exchange Rate", "from_currency": frm, "to_currency": to,
                                    "fiscal_year": FY, "fiscal_period": p, "rate_type": rate_type,
                                    "quote": 1, "quoted_per": "1", "source": "Manual",
                                    "source_note": REASON}).insert()
                r.submit()
                state["created"]["rates"].append(r.name)
    frappe.db.commit()
    save_state(state)

    # 6. Users.
    for email, roles in USERS.items():
        u = frappe.get_doc({"doctype": "User", "email": email,
                            "first_name": "ZZ C1 " + email.split("-")[2].split("@")[0],
                            "send_welcome_email": 0, "user_type": "System User",
                            "new_password": PASSWORD})
        for role in roles:
            u.append("roles", {"role": role})
        u.insert(ignore_permissions=True)
        state["created"]["users"].append(email)
    up = frappe.get_doc({"doctype": "User Permission", "user": "zz-c1-ea@example.com",
                         "allow": "Entity", "for_value": ENTITY, "apply_to_all_doctypes": 1}).insert(
        ignore_permissions=True)
    state["created"]["user_permissions"].append(up.name)
    frappe.db.commit()
    frappe.clear_cache()

    state["counts_after_setup"] = counts()
    state["finished_at"] = frappe.utils.now()
    save_state(state)
    print("setup done")
    print("  fiscal year:", state["created"]["fiscal_year"])
    print("  close settings:", [list(r) for r in frappe.db.sql(
        "select field, value from tabSingles where doctype='Close Settings' order by field")])
    print("  entity/node/ownership:", ent.name, node.name, op.name)
    print("  in-scope others:", len(others), "frequency set on:", len(state["frequency_set"]))
    print("  TB exceptions:", len(state["created"]["tb_exceptions"]))
    print("  required pairs:", state["required_pairs"], "rates:", len(state["created"]["rates"]))
    print("  users:", state["created"]["users"], "user permissions:", state["created"]["user_permissions"])
    for dt in COUNTED:
        print(f"  count {dt}: {state['counts_before'][dt]} -> {state['counts_after_setup'][dt]}")
    print("  sign-off problems 2099/1:", signoff_gate.sign_off_problems(FY, 1))


if __name__ == "__main__":
    main()
