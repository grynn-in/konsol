"""A new Build Approval moves through the workflow's Request transition (konsol#215 row W3).

With the Build Approval Workflow active, before_save no longer moves a new
Draft row on itself: after_insert loads the row fresh and takes "Request",
so the workflow (its roles and conditions, which a site can change) decides
where the row goes. A user with no Request transition leaves it in Draft and
is told. A site without an active workflow keeps the old auto-transition in
before_save. approved_by is set when a row becomes Approved.
"""
import contextlib
import copy
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BA_PY = os.path.join(APP_DIR, "pipeline", "doctype", "build_approval", "build_approval.py")
WORKFLOW_QUERY = ("Workflow", {"document_type": "Build Approval", "is_active": 1})


class _D(dict):
    """frappe._dict: attribute reads and writes are item reads and writes."""
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


def _transitions():
    """The shipped workflow's Request/Approve rows, as frappe returns them."""
    rows = []
    for role in ("EPM Analyst", "EPM Admin", "System Manager", "Administrator"):
        rows.append(_D(state="Draft", action="Request", next_state="Approved", allowed=role,
                       condition='doc.risk_level == "low"'))
        rows.append(_D(state="Draft", action="Request", next_state="Pending Review", allowed=role,
                       condition='doc.risk_level == "high"'))
    rows.append(_D(state="Pending Review", action="Approve", next_state="Approved", allowed="EPM Admin",
                   condition=None))
    for state in ("Completed", "Failed", "Cancelled"):
        for role in ("EPM Analyst", "EPM Admin", "System Manager"):
            rows.append(_D(state=state, action="Run Again", next_state="Draft", allowed=role, condition=None))
    return rows


class _Site:
    """A stub frappe site: its db, session, workflow module and what they saw."""

    def __init__(self, workflow=True, user="zz.analyst@example.com", roles=("EPM Analyst",)):
        self.rows = {}
        self.roles = set(roles)
        self.applied = []
        self.messages = []
        self.queries = []
        self.frappe = frappe = types.ModuleType("frappe")
        frappe.session = _D(user=user)
        frappe.flags = _D()

        class ValidationError(Exception):
            pass
        frappe.ValidationError = ValidationError

        def throw(msg, exc=Exception, **k):
            raise exc(msg)
        frappe.throw = throw

        def get_value(doctype, filters=None, *a, **k):
            self.queries.append((doctype, filters))
            if (doctype, filters) == WORKFLOW_QUERY and workflow:
                return "Build Approval Workflow"
            return None
        frappe.db = types.SimpleNamespace(get_value=get_value, sql=lambda *a, **k: None)

        def get_single(_):
            raise RuntimeError("no settings on the host")
        frappe.get_single = get_single
        frappe.msgprint = lambda msg, *a, **k: self.messages.append(msg)
        frappe.publish_realtime = lambda *a, **k: None
        frappe.enqueue = lambda *a, **k: None
        frappe.logger = lambda *a, **k: types.SimpleNamespace(info=lambda *a, **k: None)

        def get_doc(doctype, name):
            assert doctype == "Build Approval"
            return self.BuildApproval(before=None, new=False, **copy.deepcopy(self.rows[name]))
        frappe.get_doc = get_doc

        model = types.ModuleType("frappe.model")
        document = types.ModuleType("frappe.model.document")
        workflow_mod = types.ModuleType("frappe.model.workflow")
        utils = types.ModuleType("frappe.utils")
        utils.now_datetime = lambda: None

        class Document:
            def __init__(self, before=None, new=False, **fields):
                self._before = before
                self._new = new
                self.flags = _D()
                self.__dict__.update(fields)

            def get_doc_before_save(self):
                return self._before

            def is_new(self):
                return self._new

            def has_value_changed(self, field):
                if self._before is None:
                    return True
                return getattr(self._before, field, None) != getattr(self, field, None)

            def fields(self):
                return {k: v for k, v in self.__dict__.items() if not k.startswith("_") and k != "flags"}

            def save(self):
                """Document.save: before_save on the loaded row, then write it."""
                self._before = types.SimpleNamespace(**site.rows[self.name])
                self.before_save()
                site.rows[self.name] = copy.deepcopy(self.fields())

        site = self
        document.Document = Document

        def get_transitions(doc):
            """v15: the session user's transitions from the row's state whose condition holds."""
            assert not doc.is_new(), "get_transitions returns [] for a new row"
            found = []
            for t in _transitions():
                if t.state != doc.workflow_state or t.allowed not in self.roles:
                    continue
                if t.condition and not eval(t.condition, {}, {"doc": types.SimpleNamespace(**doc.fields())}):
                    continue
                found.append(t)
            return found

        def apply_workflow(doc, action):
            self.applied.append((doc, action))
            transition = next((t for t in get_transitions(doc) if t.action == action), None)
            if not transition:
                raise frappe.ValidationError("Not a valid Workflow Action")
            doc.workflow_state = transition.next_state
            doc.save()
            return doc

        workflow_mod.get_transitions = get_transitions
        workflow_mod.apply_workflow = apply_workflow
        model.workflow = workflow_mod
        model.document = document
        frappe.model = model
        self.mods = {"frappe": frappe, "frappe.model": model, "frappe.model.document": document,
                     "frappe.model.workflow": workflow_mod, "frappe.utils": utils}
        with self.installed():
            spec = importlib.util.spec_from_file_location("build_approval_w3", BA_PY)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        self.BuildApproval = module.BuildApproval

    @contextlib.contextmanager
    def installed(self):
        saved = {m: sys.modules.get(m) for m in self.mods}
        sys.modules.update(self.mods)
        try:
            yield
        finally:
            for m, old in saved.items():
                if old is None:
                    sys.modules.pop(m, None)
                else:
                    sys.modules[m] = old

    def insert(self, scope, name="ZZ-BA-0001"):
        """Document.insert: before_save, write the row, after_insert."""
        doc = self.BuildApproval(before=None, new=True, name=name, build_scope=scope,
                                 workflow_state="Draft", approved_by=None, requested_by=None,
                                 rebuild_requested=0, error_message=None, started_at=None)
        with self.installed():
            doc.before_save()
            self.state_after_before_save = doc.workflow_state
            self.rows[name] = copy.deepcopy(doc.fields())
            doc._new = False
            if hasattr(doc, "after_insert"):
                doc.after_insert()
        return self.rows[name]


# --- with the workflow active -----------------------------------------------

def test_a_new_high_risk_row_takes_request_and_waits_for_review():
    site = _Site()
    row = site.insert("actuals")
    assert site.state_after_before_save == "Draft", "before_save must not move the row itself"
    assert [a for _, a in site.applied] == ["Request"]
    assert row["workflow_state"] == "Pending Review"
    assert row["approved_by"] is None


def test_a_new_low_risk_row_takes_request_and_is_approved():
    site = _Site()
    row = site.insert("staging")
    assert site.state_after_before_save == "Draft", "before_save must not move the row itself"
    assert [a for _, a in site.applied] == ["Request"]
    assert row["workflow_state"] == "Approved"
    assert row["approved_by"] == "Administrator"


def test_request_is_applied_to_a_fresh_load_not_the_inserted_object():
    site = _Site()
    inserted = []
    original = site.BuildApproval.after_insert if hasattr(site.BuildApproval, "after_insert") else None
    assert original, "BuildApproval has no after_insert"

    def spy(self):
        inserted.append(self)
        return original(self)
    site.BuildApproval.after_insert = spy
    site.insert("actuals")
    (doc, action), = site.applied
    assert action == "Request"
    assert doc is not inserted[0], "apply_workflow must get frappe.get_doc's fresh row"
    assert doc.name == "ZZ-BA-0001"


def test_the_workflow_lookup_is_the_active_build_approval_workflow():
    site = _Site()
    site.insert("staging")
    assert WORKFLOW_QUERY in site.queries


def test_a_user_without_a_request_transition_leaves_the_row_in_draft_and_is_told():
    site = _Site(roles=("Guest",))
    row = site.insert("actuals")
    assert row["workflow_state"] == "Draft"
    assert site.applied == [], "no Request transition: apply_workflow must not be called"
    assert site.messages == ["You may not request a actuals build."]


# --- without an active workflow: the old behaviour ----------------------------

def test_without_a_workflow_before_save_still_moves_a_new_row():
    for scope, state, approver in (("actuals", "Pending Review", None), ("staging", "Approved", "Administrator")):
        site = _Site(workflow=False)
        row = site.insert(scope)
        assert site.state_after_before_save == state, scope
        assert row["workflow_state"] == state, scope
        assert row["approved_by"] == approver, scope
        assert site.applied == [] and site.messages == [], scope


# --- approved_by ----------------------------------------------------------------

def _approve(scope, approved_by=None, workflow=True):
    site = _Site(workflow=workflow, user="zz.admin@example.com", roles=("EPM Admin",))
    before = types.SimpleNamespace(workflow_state="Pending Review", rebuild_requested=0, started_at=None,
                                   approved_by=approved_by)
    doc = site.BuildApproval(before=before, new=False, name="ZZ-BA-0002", build_scope=scope,
                             workflow_state="Approved", approved_by=approved_by,
                             requested_by="zz.analyst@example.com", rebuild_requested=0,
                             error_message=None, started_at=None)
    with site.installed():
        doc.before_save()
    return doc


def test_a_high_risk_row_approved_by_a_user_records_that_user():
    assert _approve("actuals").approved_by == "zz.admin@example.com"
    assert _approve("actuals", workflow=False).approved_by == "zz.admin@example.com"


def test_a_low_risk_row_that_becomes_approved_records_administrator():
    assert _approve("staging").approved_by == "Administrator"


def test_an_existing_approver_is_kept():
    assert _approve("actuals", approved_by="zz.other@example.com").approved_by == "zz.other@example.com"


def test_a_save_that_does_not_change_the_state_does_not_set_an_approver():
    site = _Site(user="zz.admin@example.com", roles=("EPM Admin",))
    before = types.SimpleNamespace(workflow_state="Approved", rebuild_requested=0, started_at=None,
                                   approved_by=None)
    doc = site.BuildApproval(before=before, new=False, name="ZZ-BA-0003", build_scope="actuals",
                             workflow_state="Approved", approved_by=None, requested_by="zz.analyst@example.com",
                             rebuild_requested=0, error_message=None, started_at=None)
    with site.installed():
        doc.before_save()
    assert doc.approved_by is None


# --- Run Again under the workflow (konsol#215 row W4) ----------------------------

def _run_again(state, started_at, rebuild_requested=1, scope="actuals", name="ZZ-BA-0004"):
    """A finished row, loaded fresh, taken back to Draft by the Run Again transition."""
    site = _Site()
    site.rows[name] = dict(name=name, build_scope=scope, risk_level="high", workflow_state=state,
                           approved_by="zz.old@example.com", requested_by="zz.analyst@example.com",
                           rebuild_requested=rebuild_requested, error_message="ZZ old error",
                           started_at=started_at, completed_at="2026-09-01 10:05:00" if started_at else None,
                           duration_seconds=300 if started_at else 0)
    with site.installed():
        fresh = site.frappe.get_doc("Build Approval", name)
        site.frappe.model.workflow.apply_workflow(fresh, "Run Again")
        offered = [t.action for t in site.frappe.model.workflow.get_transitions(
            site.frappe.get_doc("Build Approval", name))]
    return site.rows[name], offered


def test_run_again_from_completed_resets_the_run_and_spends_the_flag():
    row, offered = _run_again("Completed", "2026-09-01 10:00:00")
    assert row["workflow_state"] == "Draft", "before_save must not move a Run Again row on"
    assert row["started_at"] is None and row["completed_at"] is None
    assert row["duration_seconds"] == 0
    assert row["error_message"] is None
    assert row["rebuild_requested"] == 0
    assert "Request" in offered, "the user then takes Request"


def test_run_again_from_failed_resets_the_run_and_spends_the_flag():
    row, offered = _run_again("Failed", "2026-09-01 10:00:00")
    assert row["workflow_state"] == "Draft", "before_save must not move a Run Again row on"
    assert row["started_at"] is None and row["completed_at"] is None
    assert row["duration_seconds"] == 0
    assert row["error_message"] is None
    assert row["rebuild_requested"] == 0
    assert "Request" in offered


def test_run_again_from_cancelled_clears_the_error_and_keeps_the_flag():
    row, offered = _run_again("Cancelled", None)
    assert row["workflow_state"] == "Draft", "before_save must not move a Run Again row on"
    assert row["started_at"] is None and row["completed_at"] is None
    assert row["error_message"] is None
    assert row["rebuild_requested"] == 1, "a Cancelled row's flag was never acted on"
    assert "Request" in offered


def test_run_again_of_a_low_risk_row_stays_draft_too():
    row, offered = _run_again("Completed", "2026-09-01 10:00:00", scope="staging")
    assert row["workflow_state"] == "Draft"
    assert "Request" in offered
