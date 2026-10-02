"""konsol#305 L01 setup: the users the Delivery 2 wave 2 walk-through needs.

Run INSIDE the backend container, from the sites directory:

    docker cp scripts/close_w2_setup.py konsolidat_backend:/home/frappe/frappe-bench/
    docker exec -u frappe konsolidat_backend bash -lc \
        'cd /home/frappe/frappe-bench/sites && ../env/bin/python ../close_w2_setup.py'

The walk is read-only (L01 goal, decided by Deepak Pai 2 Oct 2026): this
setup creates users and their User Permissions, nothing else. It writes
every created name to ``zz_w2_state.json`` (next to the script) as it creates
it; ``close_w2_cleanup.py`` deletes exactly those names, never by pattern
(prompt rule 9).

It refuses to run when a user it would create, the state file or the
password file already exists: it never overwrites a pre-existing record.

Users (one password, generated here, written to ``zz_w2_pw`` with mode 600
and never printed; the cleanup deletes the file):
- zz-l01-lead       EPM Admin (Close Lead)
- zz-l01-analyst    EPM Analyst (Group Accountant)
- zz-l01-viewer     EPM User (Viewer), unscoped
- zz-l01-viewer-scoped  EPM User, User Permission Entity = one P07 entity
- zz-l01-ea         Entity Accountant, User Permission Entity = CA_OVIVO
  (a #289 entity: TBs for FY2025 P07-P12, ownership only from 2025-12-16).

It also records the highest Build Approval name, so the cleanup can list any
build the walk caused (lesson BAPR-00068/69).
"""
import json
import os
import secrets

import frappe

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "zz_w2_state.json")
PW_FILE = os.path.join(HERE, "zz_w2_pw")

FY, FP = 2025, 7
EA_ENTITY = "CA_OVIVO"
USERS = {
    "zz-l01-lead@example.com": "EPM Admin",
    "zz-l01-analyst@example.com": "EPM Analyst",
    "zz-l01-viewer@example.com": "EPM User",
    "zz-l01-viewer-scoped@example.com": "EPM User",
    "zz-l01-ea@example.com": "Entity Accountant",
}
COUNTED = ("User", "User Permission", "Build Approval", "Close Event", "Group Exchange Rate",
           "Business Combination", "Consolidation Journal")


def counts():
    return {dt: frappe.db.count(dt) for dt in COUNTED}


def name_number(name):
    tail = str(name).rsplit("-", 1)[-1]
    return int(tail) if "-" in str(name) and tail.isdigit() else None


def highest(doctype):
    names = [n for n in frappe.get_all(doctype, pluck="name", limit_page_length=0)
             if name_number(n) is not None]
    if not names:
        return {"name": None, "number": 0}
    top = max(names, key=name_number)
    return {"name": top, "number": name_number(top)}


def scoped_entity():
    """The first entity (by code) with a submitted FY2025 P07 TB and an
    ownership covering 2025-07-01, other than the EA's entity: the scoped
    Viewer sees it and nothing else."""
    start = f"{FY}-{FP:02d}-01"
    rows = frappe.db.sql(
        """select distinct t.data_area_id from `tabTrial Balance Submission` t
           join tabEntity e on e.name = t.data_area_id and e.is_group = 0 and e.status = 'Active'
           where t.docstatus = 1 and t.fiscal_year = %s and t.fiscal_period = %s
             and t.data_area_id <> %s
             and exists (select 1 from `tabOwnership Period` o where o.docstatus = 1
                         and o.data_area_id = t.data_area_id and o.effective_date <= %s
                         and (o.end_date is null or o.end_date >= %s))
           order by t.data_area_id limit 1""",
        (FY, FP, EA_ENTITY, start, start))
    if not rows:
        raise SystemExit("REFUSED, nothing changed: no entity with a FY2025 P07 TB and ownership")
    return rows[0][0]


def refuse_if_present():
    problems = [f"User {u} already exists" for u in USERS if frappe.db.exists("User", u)]
    if not frappe.db.exists("Entity", EA_ENTITY):
        problems.append(f"Entity {EA_ENTITY} does not exist")
    for path in (STATE, PW_FILE):
        if os.path.exists(path):
            problems.append(f"{path} exists: run close_w2_cleanup.py first")
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
    scoped = scoped_entity()

    password = "Zz-" + secrets.token_urlsafe(18)
    fd = os.open(PW_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(password)

    state = {
        "started_at": frappe.utils.now(),
        "counts_before": counts(),
        "build_approval_mark": highest("Build Approval"),
        "scoped_entity": scoped,
        "ea_entity": EA_ENTITY,
        "created": {"users": [], "user_permissions": []},
    }
    save_state(state)  # on disk before the first change

    for email, role in USERS.items():
        u = frappe.get_doc({"doctype": "User", "email": email,
                            "first_name": "ZZ L01 " + email.split("@")[0][len("zz-l01-"):],
                            "send_welcome_email": 0, "user_type": "System User",
                            "new_password": password, "roles": [{"role": role}]})
        u.insert(ignore_permissions=True)
        state["created"]["users"].append(u.name)
        save_state(state)
    frappe.db.commit()

    for email, entity in (("zz-l01-viewer-scoped@example.com", scoped),
                          ("zz-l01-ea@example.com", EA_ENTITY)):
        up = frappe.get_doc({"doctype": "User Permission", "user": email, "allow": "Entity",
                             "for_value": entity, "apply_to_all_doctypes": 1}).insert(
            ignore_permissions=True)
        state["created"]["user_permissions"].append(up.name)
        save_state(state)
    frappe.db.commit()
    frappe.clear_cache()

    state["counts_after_setup"] = counts()
    save_state(state)
    print("setup done")
    print("  users:", state["created"]["users"])
    print("  user permissions:", state["created"]["user_permissions"],
          "scoped viewer ->", scoped, "EA ->", EA_ENTITY)
    print("  build approval mark:", state["build_approval_mark"])
    for dt in COUNTED:
        print(f"  count {dt}: {state['counts_before'][dt]} -> {state['counts_after_setup'][dt]}")


if __name__ == "__main__":
    main()
