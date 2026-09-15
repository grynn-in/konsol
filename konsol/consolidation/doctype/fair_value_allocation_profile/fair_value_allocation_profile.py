"""Fair Value Allocation Profile (konsol#208): reusable weights that spread a
Business Combination's Fair Value Adjustment Total over accounts when Get
Balances from Trial Balance builds the Acquired Balance Sheet.

Reference data, not an approval: not submittable and not written to the
warehouse (the deal's lines, which carry the spread amounts, are). The
weights are declared, never defaulted: ``validate`` refuses a profile that
could not place the whole total on real accounts.
"""
from decimal import Decimal

import frappe
from frappe.model.document import Document

_PREFIX = "Fair Value Allocation Profile: "
_HUNDRED = Decimal(100)
_TOLERANCE = Decimal("0.0001")


def _number(value):
    """A weight as Decimal, printed without trailing zeros (99, 100.5)."""
    return Decimal(str(value or 0))


def _show(value):
    return format(value.normalize(), "f")


class FairValueAllocationProfile(Document):
    def validate(self):
        found = self._problems()
        if found:
            frappe.throw("<br>".join(found))

    def _problems(self):
        lines = list(self.get("lines") or [])
        if not lines:
            return [f"{_PREFIX}add at least one line: an account and its weight."]
        found, seen, total = [], set(), Decimal(0)
        for line in lines:
            account = line.get("main_account")
            weight = _number(line.get("weight"))
            total += weight
            if weight <= 0:
                found.append(f"{_PREFIX}the weight on {account or 'a line'} is {_show(weight)}; "
                             "every weight must be greater than 0.")
            if not account:
                found.append(f"{_PREFIX}a line has no Main Account.")
                continue
            if account in seen:
                found.append(f"{_PREFIX}{account} appears more than once; list each account once.")
                continue
            seen.add(account)
            if not self._is_published_leaf(account):
                found.append(f"{_PREFIX}{account} is not a Published, non-group Main Account.")
        if abs(total - _HUNDRED) > _TOLERANCE:
            found.append(f"{_PREFIX}Weights add up to {_show(total)}; they must add up to 100.")
        return found

    @staticmethod
    def _is_published_leaf(account):
        row = frappe.db.get_value("Main Account", account, ["status", "is_group"])
        return bool(row) and row[0] == "Published" and not row[1]
