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
BUILD_LOCK = os.path.join(APP_DIR, "build_lock.py")
WORKFLOW_QUERY = ("Workflow", {"document_type": "Build Approval", "is_active": 1})
# frappe.get_roles("Administrator") is every role
ALL_ROLES = {"EPM Analyst", "EPM Admin", "System Manager", "Administrator"}


class _D(dict):
    """frappe._dict: attribute reads and writes are item reads and writes."""
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


def _transitions(request=True):
    """The shipped workflow's Request/Approve rows, as frappe returns them.
    ``request=False``: a site that removed every Request row."""
    rows = []
    for role in ("EPM Analyst", "EPM Admin", "System Manager", "Administrator") if request else ():
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

    def __init__(self, workflow=True, user="zz.analyst@example.com", roles=("EPM Analyst",),
                 can_read=True, request=True):
        self.rows = {}
        self.roles = set(roles)
        self.applied = []
        self.applied_as = []
        self.enqueued = []
        self.messages = []
        self.queries = []
        self.published = []
        # the state each save wrote, as before_save left it
        self.saved_states = []
        self.frappe = frappe = types.ModuleType("frappe")
        # v15: frappe.session is frappe.local.session; set_user rewrites it in place
        session = _D(user=user, sid="zz-sid", data=_D(csrf_token="zz"))
        frappe.local = types.SimpleNamespace(session=session, form_dict=_D(cmd="zz.method"))
        frappe.session = session
        frappe.flags = _D()

        def set_user(name):
            session.user = name
            session.sid = name
            session.data = _D()
            frappe.local.form_dict = _D()
        frappe.set_user = set_user

        def session_roles():
            return ALL_ROLES if session.user == "Administrator" else self.roles

        class ValidationError(Exception):
            pass
        frappe.ValidationError = ValidationError

        class PermissionError(Exception):
            pass
        frappe.PermissionError = PermissionError

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
        frappe.publish_realtime = lambda event, data=None, *a, **k: self.published.append((event, data))
        frappe.enqueue = lambda *a, **k: self.enqueued.append(k.get("build_request"))
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
                """Document.save: before_save on the loaded row, write it, on_update."""
                self._before = types.SimpleNamespace(**site.rows[self.name])
                self.before_save()
                site.saved_states.append(self.workflow_state)
                site.rows[self.name] = copy.deepcopy(self.fields())
                self.on_update()

            def load_from_db(self):
                """v15: re-reads the fields; self.flags survive it."""
                self.__dict__.update(copy.deepcopy(site.rows[self.name]))

        site = self
        document.Document = Document

        def get_transitions(doc):
            """v15: the session user's transitions from the row's state whose condition holds.
            It checks READ permission first (workflow.py ~52)."""
            assert not doc.is_new(), "get_transitions returns [] for a new row"
            if not can_read and session.user != "Administrator":
                raise PermissionError(f"{session.user} may not read Build Approval")
            found = []
            for t in _transitions(request):
                if t.state != doc.workflow_state or t.allowed not in session_roles():
                    continue
                if t.condition and not eval(t.condition, {}, {"doc": types.SimpleNamespace(**doc.fields())}):
                    continue
                found.append(t)
            return found

        def apply_workflow(doc, action):
            self.applied.append((doc, action))
            self.applied_as.append(session.user)
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
            # the real build_lock, bound to this stub frappe
            spec = importlib.util.spec_from_file_location("build_lock_w6", BUILD_LOCK)
            build_lock = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(build_lock)
        self.mods["konsol.build_lock"] = build_lock
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
        """Document.insert: before_save, write the row, after_insert, then
        run_post_save_methods (on_update) on the same instance."""
        doc = self.BuildApproval(before=None, new=True, name=name, build_scope=scope,
                                 workflow_state="Draft", approved_by=None, requested_by=None,
                                 rebuild_requested=0, error_message=None, started_at=None)
        self.inserted = doc
        with self.installed():
            doc.before_save()
            self.state_after_before_save = doc.workflow_state
            self.rows[name] = copy.deepcopy(doc.fields())
            doc._new = False
            if hasattr(doc, "after_insert"):
                doc.after_insert()
            doc.on_update()
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


def test_a_workflow_without_a_request_transition_leaves_the_row_in_draft_and_is_told():
    """Request is konsol's step, taken as Administrator (row W6): only a site
    whose workflow offers no Request at all leaves the row in Draft."""
    site = _Site(request=False)
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

def _run_again(state, started_at, rebuild_requested=1, scope="actuals", name="ZZ-BA-0004", **site_args):
    """A finished row, loaded fresh, taken back to Draft by the Run Again transition.

    Since row W7 the Run Again save's on_update then takes Request, so the
    row does not rest in Draft; before_save itself still leaves it Draft
    (site.saved_states[0])."""
    site = _Site(**site_args)
    site.rows[name] = dict(name=name, build_scope=scope, risk_level=None, workflow_state=state,
                           approved_by="zz.old@example.com", requested_by="zz.analyst@example.com",
                           rebuild_requested=rebuild_requested, error_message="ZZ old error",
                           started_at=started_at, completed_at="2026-09-01 10:05:00" if started_at else None,
                           duration_seconds=300 if started_at else 0)
    with site.installed():
        site.instance = site.frappe.get_doc("Build Approval", name)
        site.frappe.model.workflow.apply_workflow(site.instance, "Run Again")
    return site.rows[name], site


def test_run_again_from_completed_resets_the_run_and_spends_the_flag():
    row, site = _run_again("Completed", "2026-09-01 10:00:00")
    assert site.saved_states[0] == "Draft", "before_save must not move a Run Again row on"
    assert row["started_at"] is None and row["completed_at"] is None
    assert row["duration_seconds"] == 0
    assert row["error_message"] is None
    assert row["rebuild_requested"] == 0
    assert [a for _, a in site.applied] == ["Run Again", "Request"]


def test_run_again_from_failed_resets_the_run_and_spends_the_flag():
    row, site = _run_again("Failed", "2026-09-01 10:00:00")
    assert site.saved_states[0] == "Draft", "before_save must not move a Run Again row on"
    assert row["started_at"] is None and row["completed_at"] is None
    assert row["duration_seconds"] == 0
    assert row["error_message"] is None
    assert row["rebuild_requested"] == 0
    assert [a for _, a in site.applied] == ["Run Again", "Request"]


def test_run_again_from_cancelled_clears_the_error_and_keeps_the_flag():
    row, site = _run_again("Cancelled", None)
    assert site.saved_states[0] == "Draft", "before_save must not move a Run Again row on"
    assert row["started_at"] is None and row["completed_at"] is None
    assert row["error_message"] is None
    assert row["rebuild_requested"] == 1, "a Cancelled row's flag was never acted on"
    assert [a for _, a in site.applied] == ["Run Again", "Request"]


def test_run_again_of_a_low_risk_row_is_not_moved_by_before_save_either():
    row, site = _run_again("Completed", "2026-09-01 10:00:00", scope="staging")
    assert site.saved_states[0] == "Draft"
    assert [a for _, a in site.applied] == ["Run Again", "Request"]


# --- a row run again gets a fresh approver (konsol#215 row W3b) -------------------

def test_a_row_run_again_clears_its_old_approver_and_records_the_next_one():
    site = _Site(user="zz.admin@example.com", roles=("EPM Admin",))
    name = "ZZ-BA-0005"
    site.rows[name] = dict(name=name, build_scope="actuals", risk_level="high", workflow_state="Completed",
                           approved_by="zz.old@example.com", requested_by="zz.analyst@example.com",
                           rebuild_requested=0, error_message=None, started_at="2026-09-01 10:00:00",
                           completed_at="2026-09-01 10:05:00", duration_seconds=300)
    wf = site.frappe.model.workflow
    with site.installed():
        # Run Again takes Request itself since row W7
        wf.apply_workflow(site.frappe.get_doc("Build Approval", name), "Run Again")
        assert site.rows[name]["workflow_state"] == "Pending Review"
        assert site.rows[name]["approved_by"] is None, "the reset must clear the old run's approver"
        wf.apply_workflow(site.frappe.get_doc("Build Approval", name), "Approve")
    assert site.rows[name]["workflow_state"] == "Approved"
    assert site.rows[name]["approved_by"] == "zz.admin@example.com"


# --- Request is konsol's step, and the inserting instance is fresh (row W6) -------

def _assert_caller_session(site, user):
    assert site.frappe.session.user == user
    assert site.frappe.session.sid == "zz-sid"
    assert site.frappe.session.data == {"csrf_token": "zz"}
    assert site.frappe.local.form_dict == {"cmd": "zz.method"}


def test_request_is_taken_as_administrator_and_the_caller_is_restored():
    site = _Site()
    site.insert("actuals")
    assert site.applied_as == ["Administrator"], "Request is konsol's step, not the caller's"
    _assert_caller_session(site, "zz.analyst@example.com")


def test_a_user_who_may_not_read_build_approval_still_requests_the_build():
    """An Entity Accountant's trial-balance submission queues the request as
    that user, who has no permission on Build Approval (insert ignores it)."""
    for scope, state in (("actuals", "Pending Review"), ("staging", "Approved")):
        site = _Site(user="zz.accountant@example.com", roles=("Entity Accountant",), can_read=False)
        row = site.insert(scope)
        assert row["workflow_state"] == state, scope
        assert site.messages == [], scope
        _assert_caller_session(site, "zz.accountant@example.com")


def test_the_inserting_instance_holds_the_new_state_after_insert():
    for scope, state in (("actuals", "Pending Review"), ("staging", "Approved")):
        site = _Site()
        site.insert(scope)
        assert site.inserted.workflow_state == state, "after_insert must reload the inserting instance"


def test_the_build_is_enqueued_once_by_the_request_not_again_by_the_outer_save():
    site = _Site()
    site.insert("staging")
    assert site.enqueued == ["ZZ-BA-0001"], "the inner Request save enqueues; the outer on_update must not"


def test_the_outer_on_update_returns_early_once_and_the_flag_is_spent():
    """frappe's load_from_db keeps flags, and control_api saves the same
    instance again (Approve): that later save must still enqueue."""
    site = _Site(user="zz.admin@example.com", roles=("EPM Admin",))
    site.insert("actuals")
    assert site.enqueued == []
    doc = site.inserted
    assert not doc.flags.get("request_applied"), "the outer on_update spends the flag"
    with site.installed():
        doc.workflow_state = "Approved"
        doc.save()
    assert site.enqueued == ["ZZ-BA-0001"]


def test_as_administrator_switches_the_user_and_restores_the_session():
    site = _Site()
    build_lock = site.mods["konsol.build_lock"]
    try:
        with build_lock.as_administrator():
            assert site.frappe.session.user == "Administrator"
            assert not site.frappe.flags.get("konsol_build_writer"), "only build_writer sets the flag"
            raise RuntimeError("the save failed")
    except RuntimeError:
        pass
    _assert_caller_session(site, "zz.analyst@example.com")
    with build_lock.build_writer():
        assert site.frappe.session.user == "Administrator"
        assert site.frappe.flags.konsol_build_writer is True
    _assert_caller_session(site, "zz.analyst@example.com")
    assert not site.frappe.flags.get("konsol_build_writer")


# --- no Build Approval rests in Draft under the workflow (row W7) -----------------

def test_run_again_on_a_failed_high_risk_row_ends_pending_review():
    row, site = _run_again("Failed", "2026-09-01 10:00:00")
    assert row["workflow_state"] == "Pending Review", "a Draft row would absorb every later request"
    assert site.enqueued == []
    assert site.published == [("build_request_pending", {"name": "ZZ-BA-0004", "scope": "actuals"})]
    assert row["approved_by"] is None


def test_run_again_on_a_failed_low_risk_row_is_approved_and_enqueued_once():
    row, site = _run_again("Failed", "2026-09-01 10:00:00", scope="staging")
    assert row["workflow_state"] == "Approved"
    assert row["approved_by"] == "Administrator"
    assert site.enqueued == ["ZZ-BA-0004"], "the Request's save enqueues; the Run Again save must not again"


def test_run_again_from_every_finished_state_takes_request():
    for state, started_at in (("Completed", "2026-09-01 10:00:00"), ("Failed", None), ("Cancelled", None)):
        row, _ = _run_again(state, started_at)
        assert row["workflow_state"] == "Pending Review", state


def test_the_request_after_run_again_is_taken_as_administrator_and_the_caller_is_restored():
    row, site = _run_again("Failed", "2026-09-01 10:00:00")
    assert site.applied_as == ["zz.analyst@example.com", "Administrator"]
    _assert_caller_session(site, "zz.analyst@example.com")


def test_the_run_again_instance_holds_the_new_state_and_no_spent_flag():
    """The instance saved by Run Again is reloaded; no request_applied is left
    on it (its on_update is already past), so a later save still acts."""
    row, site = _run_again("Completed", "2026-09-01 10:00:00")
    assert site.instance.workflow_state == "Pending Review"
    assert not site.instance.flags.get("request_applied")


def test_run_again_on_a_site_without_request_stays_draft_and_is_told():
    row, site = _run_again("Failed", "2026-09-01 10:00:00", request=False)
    assert row["workflow_state"] == "Draft"
    assert site.messages == ["You may not request a actuals build."]


def test_a_save_of_a_draft_row_that_was_already_draft_takes_no_request():
    """Only the move to Draft from a finished state takes Request."""
    site = _Site()
    name = "ZZ-BA-0006"
    site.rows[name] = dict(name=name, build_scope="actuals", risk_level="high", workflow_state="Draft",
                           approved_by=None, requested_by="zz.analyst@example.com", rebuild_requested=0,
                           error_message=None, started_at=None, completed_at=None, duration_seconds=0)
    with site.installed():
        site.frappe.get_doc("Build Approval", name).save()
    assert site.rows[name]["workflow_state"] == "Draft"
    assert site.applied == []
