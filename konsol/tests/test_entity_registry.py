"""The governed entity registry (konsol#110).

F8 shipped a trial-balance intake for subsidiaries with no ERP connector, and
those entities then vanished at consolidation: gold_consolidated_trial_balance
INNER JOINed silver_legal_entities — an ERP-sourced table — for each entity's
accounting currency, and a konsol-only entity had no row there. Entity now
writes through to epm_staging.entities and dbt resolves the currency from it
first, the ERP second.

These tests ENUMERATE rather than name the cases: every DDL column against
every mapped field, every lifecycle hook that changes the row set.
"""
import ast
import json
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTITY_DIR = os.path.join(APP_DIR, "epm", "doctype", "entity")


def _src():
    with open(os.path.join(ENTITY_DIR, "entity.py")) as f:
        return f.read()


def _meta():
    with open(os.path.join(ENTITY_DIR, "entity.json")) as f:
        return json.load(f)


def _class_attr(name):
    """A literal class attribute of Entity, read without importing frappe."""
    tree = ast.parse(_src())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Entity")
    for node in cls.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"Entity.{name} not declared")


def _method(name):
    tree = ast.parse(_src())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Entity")
    return next((n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name), None)


def _calls(fn):
    """Names of everything a method calls — `self._resync()` -> '_resync',
    `super().on_update()` -> 'super', 'on_update'."""
    out = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute):
                out.add(f.attr)
            elif isinstance(f, ast.Name):
                out.add(f.id)
    return out


def _ddl_columns():
    with open(os.path.join(APP_DIR, "clickhouse.py")) as f:
        ch = f.read()
    block = ch.split('"epm_staging.entities": (')[1].split("),")[0]
    body = "".join(re.findall(r'"([^"]*)"', block))
    cols = body.split("(", 1)[1].split(")", 1)[0]
    return [c.strip().split()[0] for c in cols.split(",")]


def test_writes_through_to_the_registry_table():
    assert _class_attr("CH_TABLE") == "epm_staging.entities"


def test_every_ddl_column_is_mapped_and_nothing_else_is():
    """A column the DDL has and the map does not is written as DEFAULT forever;
    a mapped column the DDL lacks makes every INSERT fail, and sync_rows only
    logs that. Either way the registry silently stops meaning anything."""
    mapped = _class_attr("CH_FIELD_MAP")
    assert list(mapped) == _ddl_columns(), (
        "CH_FIELD_MAP keys must match the DDL columns, in order")


def test_every_mapped_field_exists_on_the_doctype():
    fields = {f["fieldname"] for f in _meta()["fields"]} | {"name"}
    for column, field in _class_attr("CH_FIELD_MAP").items():
        assert field in fields, f"{column} maps to {field!r}, which Entity does not have"


def test_the_join_key_is_the_record_name():
    """The name IS the normalised code, and it is what a rename changes — the
    field could in principle lag it; the name cannot."""
    assert _class_attr("CH_FIELD_MAP")["data_area_id"] == "name"


def test_the_currency_is_sent_under_the_name_the_consolidation_joins_on():
    assert _class_attr("CH_FIELD_MAP")["accounting_currency"] == "functional_currency"


def test_functional_currency_is_a_link_to_the_iso_list():
    """As free text, 'usd' or 'EURO' would reach the warehouse, miss every rate
    and translate at the 1.0 parity fallback — caught only by a warehouse test
    after the build, instead of by the form."""
    f = next(x for x in _meta()["fields"] if x["fieldname"] == "functional_currency")
    assert f["fieldtype"] == "Link"
    assert f["options"] == "ISO Currency"


def test_every_lifecycle_that_changes_the_row_set_resyncs():
    """Insert and save (on_update), delete (after_delete), rename
    (after_rename — rename_doc never calls on_update). Each one."""
    for hook in ("on_update", "after_delete", "after_rename"):
        fn = _method(hook)
        assert fn is not None, f"Entity.{hook} is missing"
        assert "_resync" in _calls(fn), f"Entity.{hook} does not resync"


def test_nested_set_hooks_are_chained_not_replaced():
    """NestedSet.on_update maintains lft/rgt; NestedSet.after_rename re-points
    the children. Overriding either without super() corrupts the tree."""
    for hook in ("on_update", "after_rename"):
        calls = _calls(_method(hook))
        assert "super" in calls and hook in calls, f"Entity.{hook} does not call super().{hook}"


def test_delete_never_syncs_from_on_trash():
    """on_trash runs before the row is gone, so a sync there re-publishes it
    (konsol#120). Entity must leave on_trash to NestedSet."""
    assert _method("on_trash") is None


def _module_function(name):
    tree = ast.parse(_src())
    return next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name), None)


def test_one_sync_call():
    """Every hook goes through _resync, and the only place that names
    sync_doctype is the module-level function _resync queues."""
    assert _src().count("sync_doctype(") == 1
    assert "sync_doctype" in _calls(_module_function("_sync_entity_registry"))


def test_the_sync_waits_for_the_commit():
    """ClickHouse has no transaction: a sync inside on_update publishes a save
    that may still roll back, and the warehouse then consolidates on a row
    MariaDB never committed. Reproduced live in the #110 E2E (konsol#124)."""
    calls = _calls(_method("_resync"))
    assert "sync_doctype" not in calls and "_sync_entity_registry" not in calls, (
        "_resync must queue the sync, not run it")
    body = ast.dump(_method("_resync"))
    assert "after_commit" in body and "_sync_entity_registry" in body


def test_the_sync_is_queued_once_per_transaction_by_asking_the_queue():
    """CallbackManager.add appends without deduping. The guard checks the
    queue itself for the sync function. A marker flag was tried first; Frappe
    drops the rollback callbacks at the start of commit(), so a commit that
    failed left the flag set and silently skipped every later sync in the
    process (#110 re-review)."""
    resync = _method("_resync")
    src = ast.unparse(resync)
    assert "_functions" in src and "not in" in src and "_sync_entity_registry" in src
    assert "entity_registry_sync_queued" not in _src(), "no marker flag: it can stick"
    # and behaviourally: run the real method three times against a stub queue
    import collections, types
    queue = collections.deque()
    after_commit = types.SimpleNamespace(_functions=queue, add=queue.append)
    ns = {"frappe": types.SimpleNamespace(db=types.SimpleNamespace(after_commit=after_commit)),
          "_sync_entity_registry": lambda: None}
    exec(compile(ast.Module(body=[resync], type_ignores=[]), "entity.py", "exec"), ns)
    for _ in range(3):
        ns["_resync"](None)
    assert list(queue) == [ns["_sync_entity_registry"]], "three saves must queue one sync"


def _module_function_source(name):
    fn = _module_function(name)
    assert fn is not None, f"{name} is missing"
    return ast.Module(body=[fn], type_ignores=[])


def test_normalised_comparison_ignores_representation_not_value():
    """The previous doc comes from the database (ints, NULL as None); incoming
    values come as sent. Run the real function with a cint stand-in, not a
    re-statement of it."""
    ns = {"cint": lambda v: int(float(v or 0))}
    exec(compile(_module_function_source("_normalised"), "entity.py", "exec"), ns)
    norm = ns["_normalised"]
    assert norm("is_group", "0") == norm("is_group", 0) == norm("is_group", None)
    assert norm("is_group", "1") == norm("is_group", 1)
    assert norm("functional_currency", "") == norm("functional_currency", None)
    assert norm("functional_currency", "EUR") != norm("functional_currency", "USD")
    assert "_normalised" in _calls(_method("_changes_what_consolidation_sees"))


# ---- rebuilds ----------------------------------------------------------------

def _build_map_scope():
    with open(os.path.join(APP_DIR, "tasks.py")) as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "DOCTYPE_BUILD_MAP" for t in node.targets):
            return ast.literal_eval(node.value)["Entity"]["scope"]
    raise AssertionError("DOCTYPE_BUILD_MAP not found")


def test_the_rebuild_scope_reaches_the_models_that_read_the_registry():
    """`staging` selects tag:domain:staging — five models, none of which read
    the registry — so a currency change used to rebuild nothing that mattered.
    `consolidation` is ancestor-inclusive (+tag:domain:consolidation), which on
    the live stack selects silver_entity_currencies AND
    gold_consolidated_trial_balance (dbt ls, #110 review)."""
    scope = _build_map_scope()
    assert scope == "consolidation"
    # The selector lives in tasks.SCOPE_SELECTOR, not the Build Scope fixture.
    with open(os.path.join(APP_DIR, "tasks.py")) as f:
        tree = ast.parse(f.read())
    selectors = next(ast.literal_eval(n.value) for n in ast.walk(tree)
                     if isinstance(n, ast.Assign) and any(
                         isinstance(t, ast.Name) and t.id == "SCOPE_SELECTOR" for t in n.targets))
    assert selectors[scope] == "+tag:domain:consolidation", (
        "the scope must select ancestors — the registry model is upstream of "
        "every consolidation-domain model, and gold_consolidated_trial_balance "
        "itself is domain:actuals")


def test_only_a_change_the_warehouse_can_see_requests_a_rebuild():
    """The consolidation scope is high-risk, so each request waits for an EPM
    Admin. The watched fields are exactly the ones behind the columns dbt
    reads — accounting_currency and is_group — and nothing else."""
    watched = set(_class_attr("_REBUILD_FIELDS"))
    mapped = _class_attr("CH_FIELD_MAP")
    assert watched == {mapped["accounting_currency"], mapped["is_group"]}


def test_every_lifecycle_that_changes_what_consolidation_sees_requests_a_rebuild():
    """Save (when a watched field changed), delete, rename. The last two are
    not doc_events at all, so the generic trigger could never have seen them."""
    for hook in ("on_update", "after_delete", "after_rename"):
        assert "_request_rebuild" in _calls(_method(hook)), f"Entity.{hook} requests no rebuild"
    with open(os.path.join(APP_DIR, "tasks.py")) as f:
        tasks = ast.parse(f.read())
    job = next((n for n in tasks.body if isinstance(n, ast.FunctionDef)
                and n.name == "request_consolidation_build"), None)
    assert job is not None, "the job target is missing from tasks.py"
    assert "on_consolidation_doc_update" in _calls(job), (
        "reuse the trigger path: it carries the scope map and the debounce")


def test_the_rebuild_request_goes_through_the_shared_queue():
    """Entity decides when; konsol.tasks.queue_consolidation_build decides how
    (a job after the commit, guarded, best-effort). Its behaviour is tested in
    test_build_trigger.py. Entity must not enqueue, or call the committing
    trigger, itself (konsol#126)."""
    calls = _calls(_method("_request_rebuild"))
    assert "queue_consolidation_build" in calls
    assert not {"enqueue", "on_consolidation_doc_update"} & calls


def test_entity_is_not_a_generic_trigger_doctype():
    """In _dbt_trigger_doctypes every Entity save — a new country, a typo in a
    name — would ask an EPM Admin to approve a consolidation rebuild."""
    with open(os.path.join(APP_DIR, "hooks.py")) as f:
        hooks = f.read()
    triggers = hooks.split("_dbt_trigger_doctypes = [")[1].split("]")[0]
    entries = [ln.strip() for ln in triggers.splitlines()
               if ln.strip() and not ln.strip().startswith("#")]
    assert '"Entity",' not in entries


def test_doctype_json_is_marked_modified_after_the_link_change():
    """Frappe re-syncs a DocType only when the file's `modified` is newer than
    the database's, so a fieldtype change under the old stamp never applies."""
    assert _meta()["modified"] >= "2026-09-12"


def test_the_build_request_debounce_is_serialised_per_scope():
    """Check-then-insert with no lock let two concurrent requests both insert
    a Build Approval. Measured live before the fix: two simultaneous requests
    made two approvals. The Build Scope row lock must come before the pending
    check, or it serialises nothing."""
    with open(os.path.join(APP_DIR, "tasks.py")) as f:
        src = f.read()
    body = src.split("def on_consolidation_doc_update")[1].split("\ndef ")[0]
    scope_lock = body.index("tabBuild Scope")
    check = body.index("FROM `tabBuild Approval`")
    assert scope_lock < check and "FOR UPDATE" in body[check:check + 300]
