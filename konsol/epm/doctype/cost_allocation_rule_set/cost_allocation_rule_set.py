"""Cost Allocation Rule Set: who allocates what to whom (konsol#269, K4).

validate builds the pure model's RuleSet from the document and lists every
error in one refusal. The frappe reads are small helpers so tests can stub them.

Phase 2b-2 (Q5 = 5B, Deepak Pai 13 Nov 2026; Q1, Q4 and Q6 = A, 27 Nov 2026):
each rule has a basis. Share of Pool splits the sender's whole pool; Declared
Rate charges a rate per driver unit, held on the Rates table by step in dated
windows that may not overlap. A Declared Rate step needs Declared Allocation
Account mode, because the charge is credited to the sender on that account.
"""
from fractions import Fraction

import frappe
from frappe.model.document import Document

from konsol import cost_allocation_model as model


def _dimension_state(dimension):
    """(status, in_budget) of a Dimension, (None, 0) when it does not exist."""
    row = frappe.db.get_value("Dimension", dimension, ["status", "in_budget"], as_dict=True)
    if not row:
        return (None, 0)
    return (row.get("status"), row.get("in_budget") or 0)


def _dimension_members(dimension):
    """Member values of a dimension (shared helper: no page cap, no user check)."""
    from konsol.cost_allocation_members import dimension_members

    return dimension_members(dimension)


def _driver_dimensions(drivers):
    """{driver: dimension} for the named Cost Allocation Drivers that exist."""
    if not drivers:
        return {}
    rows = frappe.get_all("Cost Allocation Driver", filters={"name": ["in", list(drivers)]},
                          fields=["name", "dimension"])
    return {r["name"]: r["dimension"] for r in rows}


def _rows(table):
    return [r.as_dict() if hasattr(r, "as_dict") else dict(r.__dict__) for r in table or []]


class CostAllocationRuleSet(Document):
    def validate(self):
        errors = self._dimension_errors()
        if not errors:
            errors = self._model_errors()
        if errors:
            frappe.throw("<br>".join(errors))

    def _dimension_errors(self):
        status, in_budget = _dimension_state(self.allocation_dimension)
        errors = []
        if status != "Published":
            errors.append(f"Dimension {self.allocation_dimension} is {status or 'not found'}; "
                          "a cost allocation rule set needs a Published dimension.")
        elif not in_budget:
            errors.append(f"Dimension {self.allocation_dimension} is not In Budget; "
                          "a cost allocation rule set needs a dimension marked Include in Budget (In Budget).")
        return errors

    def _model_errors(self):
        """Structure (the model's validate), the rules' bases and rate rows
        (rate_errors) and, when those pass, a Reciprocal closed loop, in one
        list. A Declared Rate step under Reciprocal is allowed here: the run
        computes it (a rate sender leaves the linear system and acts as an exit)."""
        rules = [{"step": r["step"], "sender_member": r["sender_member"], "driver": r["driver"],
                  "basis": r.get("basis")}
                 for r in _rows(self.rules)]
        receivers = [{"step": r["step"], "receiver_member": r["receiver_member"]}
                     for r in _rows(self.receivers)]
        rates = [{"step": r["step"], "rate": r.get("rate"), "effective_from": r.get("effective_from"),
                  "effective_to": r.get("effective_to")}
                 for r in _rows(getattr(self, "rates", None))]
        from konsol.cost_allocation_members import MemberReadError

        try:
            members = _dimension_members(self.allocation_dimension)
        except MemberReadError as exc:
            return [f"Cost Allocation Rule Set: {exc}"]
        rs = model.RuleSet(
            entity=self.entity,
            dimension=self.allocation_dimension,
            method=self.method,
            receiver_account_mode=self.receiver_account_mode,
            allocation_account=self.allocation_account or "",
            rules=[{k: r[k] for k in ("step", "sender_member", "driver")} for r in rules],
            receivers=receivers,
            dimension_members=members,
            driver_dimensions=_driver_dimensions(sorted({r["driver"] for r in rules})),
        )
        errors = model.validate(rs)
        errors += model.rate_errors(rules, rates, self.receiver_account_mode)
        if not errors and rs.method == model.RECIPROCAL:
            errors += self._closed_loop_errors(rules, receivers)
        return errors

    def _closed_loop_errors(self, rules, receivers):
        """Refuse at save a Reciprocal loop whose cost can never leave. Whether
        a receiver is reachable depends on weights only through "positive", so
        equal weights decide it with no statistics: if even those cannot exit,
        no statistic can. A loop that only zero statistics close is refused at
        Compute (compute_weights_on builds the coefficient rows) and again at
        submit.

        ``rules`` carry their basis: a Declared Rate sender is an exit for the
        model (its outflow is fixed by its rate, so it is outside the linear
        system), as at Compute. The check runs only after rate_errors has
        validated the real rates, so each rate step gets a placeholder rate of
        1 and one unit per receiver: enough for reachability, which does not
        depend on their values. A rule without a basis is Share of Pool, the
        field's default, as in rate_errors."""
        steps = model.build_steps(
            [dict(r, basis=r.get("basis") or model.SHARE_OF_POOL,
                  rate=Fraction(1) if r.get("basis") == model.DECLARED_RATE else None)
             for r in rules],
            receivers)
        weights = {(st.step, r): Fraction(1, len(st.receivers)) for st in steps for r in st.receivers}
        units = {(st.step, r): Fraction(1) for st in steps if st.basis == model.DECLARED_RATE
                 for r in st.receivers}
        try:
            model.reciprocal_coefficients(steps, weights, units)
        except model.CostAllocationError as exc:
            return exc.errors
        return []
