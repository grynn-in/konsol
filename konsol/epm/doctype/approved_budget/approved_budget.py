"""Approved Budget: the declaration that one Budget Cycle, read through one
layer policy, is a root group's approved budget for a fiscal year
(konsol#348 S5 K1; BUD-4-2, Deepak Pai, 7 Nov 2026).

The Consolidated Statement's Budget columns read the Cycle and the layers
this document names, for its group and year; without a submitted
declaration the report shows a visible "no approved budget" state, never a
blank or guessed column. The declaration adds no figures: the warehouse
holds every approved Cycle, and this says which one the group reports
against.

Submittable, and submit is the approval (#348 S5-Q1 a, taken under the
standing rule): review happens on the draft, EPM Admin submits, and there is
no installed workflow and no state field. The approver and the time of the
approval are stamped at submit (``approved_by``, ``approved_on``; PR #554
review m1: the owner may have drafted it, and ``modified`` moves with every
save). Cancel is gated on the year (S5 K2); a cancelled declaration stays as
the audit trail and may be amended.

validate refuses, by name, what the statement could not read correctly:
- a member row of a group (``data_area_id`` set): the statement reads the
  root group's consolidated budget, and a member's declaration would show
  the whole group's as if it were the member's (statement_api.group_code);
- a Cycle that is not approved, not of a ``budget`` scenario (BUD-4: the
  forecast, latest-estimate read is S6), or not of the declared year;
- a layer named under All layers, or none under One layer: never cleared or
  guessed for the user;
- a second live declaration (docstatus 0 or 1) for the (group, year), read
  FOR UPDATE: under REPEATABLE READ a plain read would miss one committed
  after this transaction's first read (Budget Cycle's
  _refuse_second_live_cycle). A cancelled declaration does not count, so a
  cancelled one can be amended.
"""
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from konsol.epm.budget_grain import digest_name
from konsol.workflows import set_cancelled_state

ALL_LAYERS = "All layers"
ONE_LAYER = "One layer"
BUDGET = "budget"

#: The index validate's FOR UPDATE read runs on (S5 K2; PR #522 review: an
#: unindexed locking read scans the table and next-key-locks every row).
LIVE_INDEX = ("consolidation_group", "fiscal_year", "docstatus")
LIVE_INDEX_NAME = "approved_budget_live"


class ApprovedBudget(Document):
    def validate(self):
        group = self._root_group_row()
        cycle = self._cycle_row()
        self._refuse_bad_cycle(cycle)
        self._refuse_bad_layer()
        # Read-only copies of the Cycle's identity, set here so an API insert
        # that skips fetch_from carries them too; the Cycle's own are the truth.
        self.scenario_id = cycle.scenario_id
        # konsol#592 D2: the Cycle has no Rate Set; every budget translates at
        # its year's Constant sheet, published under CONSTANT.
        from konsol.group_rates import CONSTANT_RATE_SET

        self.rate_set = CONSTANT_RATE_SET
        self.title = f"{group.consolidation_group} FY{self.fiscal_year}"
        self._refuse_second_live_declaration()

    def _root_group_row(self):
        """The group's code and member marker. A member row of a group carries
        the root's code with its own ``data_area_id``; declaring one would
        show the root group's statement as if it were the member's."""
        row = frappe.db.get_value("Consolidation Group", self.consolidation_group,
                                  ["consolidation_group", "data_area_id"], as_dict=True)
        if row is None:
            frappe.throw(_("Consolidation Group {0} does not exist.").format(self.consolidation_group),
                         frappe.ValidationError)
        if row.data_area_id:
            frappe.throw(
                _("{0} is a member of {1}, not a root consolidation group. An Approved Budget declares the "
                  "root group: choose it.").format(self.consolidation_group, row.consolidation_group),
                frappe.ValidationError)
        return row

    def _cycle_row(self, for_update=False):
        """The named Cycle's row. ``for_update`` (before_submit, PR #554 review
        m7a) locks it: Budget Cycle's cancel gate (K16) reads the submitted
        declarations that name the Cycle, and this submit reads the Cycle's
        docstatus; without a lock the two could each read before the other
        wrote (write skew under REPEATABLE READ) and leave a submitted
        declaration naming a cancelled Cycle. If the cancel commits first,
        this locking read sees its docstatus 2 and refuses. If this submit
        locks first, the cancel waits for its commit and then reads the
        declarations FOR UPDATE itself (BudgetCycle
        ._refuse_cancel_under_an_approved_budget), so it sees this one. Both
        reads must lock; either alone leaves one ordering open."""
        row = frappe.db.get_value(
            "Budget Cycle", self.budget_cycle,
            ["docstatus", "scenario_id", "scenario_type", "fiscal_year", "input_fingerprint"],
            as_dict=True, for_update=for_update)
        if row is None:
            frappe.throw(_("Budget Cycle {0} does not exist.").format(self.budget_cycle), frappe.ValidationError)
        return row

    def _refuse_bad_cycle(self, cycle):
        """The declaration names an approved Cycle of a budget scenario for its
        own year. A forecast Cycle is refused (BUD-4: the latest-estimate read
        is S6); a draft or cancelled one has nothing approved to declare."""
        states = {0: _("a draft"), 2: _("cancelled")}
        if int(cycle.docstatus or 0) != 1:
            frappe.throw(
                _("Budget Cycle {0} is {1}, not approved. An Approved Budget names an approved Cycle: approve "
                  "it first, or choose another.").format(self.budget_cycle, states.get(int(cycle.docstatus or 0),
                                                                                         _("not approved"))),
                frappe.ValidationError)
        if cycle.scenario_type != BUDGET:
            frappe.throw(
                _("Budget Cycle {0} is a {1} Cycle. An Approved Budget names a Cycle of a budget scenario; the "
                  "latest-estimate (forecast) read is a later step.").format(self.budget_cycle, cycle.scenario_type),
                frappe.ValidationError)
        if int(cycle.fiscal_year or 0) != int(self.fiscal_year or 0):
            frappe.throw(
                _("Budget Cycle {0} is for FY{1}; this declaration is for FY{2}. Choose a Cycle of the same "
                  "year.").format(self.budget_cycle, cycle.fiscal_year, self.fiscal_year),
                frappe.ValidationError)

    def _refuse_bad_layer(self):
        """One layer needs the layer; All layers refuses one. The form hides
        and requires the field by policy (mandatory_depends_on); this is the
        rule an API insert meets. Never cleared for the user: a layer named
        under All layers says the user meant one of the two, and only they
        know which."""
        policy = self.get("layer_policy")
        layer = self.get("layer")
        if policy == ONE_LAYER and not layer:
            frappe.throw(_("Layer Policy is One layer: name the Budget Layer the approved budget reads."),
                         frappe.ValidationError)
        if policy == ALL_LAYERS and layer:
            frappe.throw(
                _("Layer Policy is All layers, but Layer {0} is named. Clear the layer to read every layer, or "
                  "choose One layer to read {0} alone.").format(layer),
                frappe.ValidationError)

    def _refuse_second_live_declaration(self):
        """One live declaration (docstatus 0 or 1) per (group, year): the
        statement reads THE approved budget, and two would make the choice
        for it. Read FOR UPDATE on (consolidation_group, fiscal_year,
        docstatus), the index on_doctype_update adds (S5 K2), so two inserts
        for one pair serialise and the second sees the first (the REPEATABLE
        READ trap: a plain read serves the transaction's snapshot). The
        document's own row does not count on a re-save; a cancelled one never
        does, so a cancelled declaration can be amended. Skipped in install
        and migrate, as Budget Cycle's lock is."""
        if frappe.flags.in_install or frappe.flags.in_migrate:
            return
        states = {0: _("draft"), 1: _("approved")}
        rows = frappe.db.get_values(
            "Approved Budget",
            {"consolidation_group": self.consolidation_group, "fiscal_year": self.fiscal_year,
             "docstatus": ["in", [0, 1]]},
            ["name", "docstatus"], as_dict=True, order_by="name asc", for_update=True)
        for row in rows:
            if row.name == self.name:
                continue
            frappe.throw(
                _("Approved Budget {0} is already the {1} declaration for {2} FY{3}. A group and year hold one "
                  "live declaration: work in it, or cancel it and amend it.").format(
                    row.name, states[int(row.docstatus)], self.consolidation_group, self.fiscal_year),
                frappe.ValidationError)

    def before_submit(self):
        """Submit is the approval (S5-Q1 a). The declaration takes a copy of
        the Cycle's ``input_fingerprint`` (S5 K2; BUD-11-3): the hash of the
        inputs approved HERE, frozen by the submit, which the statement
        compares with the Cycle's recomputed hash at every read (S5-Q2 a).
        Set in before_submit so it lands with the approval and nothing saves
        in on_submit. A Cycle with no stamp (approved before S4 K8) is refused
        by name: a blank copy would never match anything. The Cycle row is
        read FOR UPDATE (``_cycle_row``, review m7a). The approver and the
        time are stamped here too (``approved_by``, ``approved_on``; review
        m1): the submitter approves, whoever drafted the document."""
        cycle = self._cycle_row(for_update=True)
        self._refuse_bad_cycle(cycle)
        if not cycle.input_fingerprint:
            frappe.throw(
                _("Budget Cycle {0} has no input fingerprint stamped (it was approved before fingerprints "
                  "were). Cancel and amend the Cycle so Approve stamps one, then declare it.").format(
                    self.budget_cycle),
                frappe.ValidationError)
        self.input_fingerprint = cycle.input_fingerprint
        self.approved_by = frappe.session.user
        self.approved_on = now_datetime()

    def before_cancel(self):
        """Cancel only while the year has an open period (the Budget Cycle
        rule, BUD-9-3): a fully closed year's budget is history. Nothing
        changes before the gate. No workflow ships for this doctype (S5-Q1
        a); the shared helper sets a docstatus-2 state only when a site has
        installed one of its own. A cancelled declaration leaves the
        statement in its "no approved budget" state and stays as the audit
        trail; a correction is an amendment."""
        from konsol.period_status import assert_year_not_closed

        assert_year_not_closed(self.fiscal_year, _("cancel Approved Budget {0}").format(self.name))
        set_cancelled_state(self)

    def autoname(self):
        """``APB-<group>-<year>-<sha8>`` (budget_grain.digest_name: collision-
        safe, length-capped; never ``format:{#####}``, #535), or the first
        free ``<digest>-N``: a cancelled declaration keeps its name as the
        audit trail, and its amendment must not collide with it (the Budget
        Cycle pattern, #524)."""
        base = digest_name("APB", [self.consolidation_group, self.fiscal_year])
        name, n = base, 0
        while frappe.db.exists("Approved Budget", name):
            n += 1
            name = f"{base}-{n}"
        self.name = name

    def set_new_name(self, *args, **kwargs):
        """The declaration owns its name under any site naming setting: Frappe
        v16 names an amendment ``<base>-<n+1>`` before the controller runs,
        without checking that the name is free (budget_cycle.py's
        set_new_name has the trace). Once Frappe has named it, autoname names
        it again as the first free suffix; a name the caller passes, or a
        document already named, is left alone."""
        force = kwargs.get("force", args[0] if args else False)
        set_name = kwargs.get("set_name", args[1] if len(args) > 1 else None)
        named = self.flags.name_set and not force
        super().set_new_name(*args, **kwargs)
        if not named and not set_name:
            self.autoname()


def on_doctype_update():
    """The index validate's locking read runs on (S5 K2): (group, year,
    docstatus), equality on the first two and IN on the last. Its own index,
    not budget_intake.LOCK_INDEXES: that map is the intake's reads, and
    test_budget_intake pins it to them. add_index checks first, so this is
    idempotent."""
    frappe.db.add_index("Approved Budget", list(LIVE_INDEX), index_name=LIVE_INDEX_NAME)
