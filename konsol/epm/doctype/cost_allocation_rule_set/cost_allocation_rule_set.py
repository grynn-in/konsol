"""Cost Allocation Rule Set: who allocates what to whom (konsol#269, K4).

validate builds the pure model's RuleSet from the document and lists every
error in one refusal. The frappe reads are small helpers so tests can stub them.
"""
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
        rules = [{"step": r["step"], "sender_member": r["sender_member"], "driver": r["driver"]}
                 for r in _rows(self.rules)]
        receivers = [{"step": r["step"], "receiver_member": r["receiver_member"]}
                     for r in _rows(self.receivers)]
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
            rules=rules,
            receivers=receivers,
            dimension_members=members,
            driver_dimensions=_driver_dimensions(sorted({r["driver"] for r in rules})),
        )
        try:
            model.assert_valid(rs)
        except model.CostAllocationError as exc:
            return exc.errors
        return []
