"""Dimension members for cost allocation (konsol#269 KF2).

Rule Set and Statistic both ask "is this value a member of this dimension?".
Dimension Value is a virtual doctype. In Frappe v16 ``frappe.get_all`` on a
virtual doctype pages at 20 rows by default and applies the user's read check
(qb_query._handle_virtual_doctype), so a 25-member dimension looked like 20
members, and a user without Dimension Value read could not save either
document. Membership is a system-level check, not a list the user is browsing,
so this reads the controller's own row source directly.
"""


def dimension_members(dimension):
    """The set of member values of ``dimension``; empty when it is not declared."""
    from konsol.epm.doctype.dimension_value import dimension_value

    return {r["value"] for r in dimension_value._rows() if r["dimension"] == dimension}
