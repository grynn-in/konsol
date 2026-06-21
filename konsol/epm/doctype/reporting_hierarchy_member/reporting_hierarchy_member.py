"""Reporting Hierarchy Member — nodes in a management reporting tree."""
import frappe
from frappe.model.document import Document


class ReportingHierarchyMember(Document):

    def validate(self):
        self._validate_parent_scope()
        self._validate_member_code()
        self._validate_no_cycles()

    def _validate_parent_scope(self):
        if not self.parent_member:
            return
        parent_h = frappe.db.get_value(
            "Reporting Hierarchy Member",
            self.parent_member,
            "reporting_hierarchy",
        )
        if parent_h != self.reporting_hierarchy:
            frappe.throw(
                "Parent member must belong to the same Reporting Hierarchy."
            )

    def _validate_member_code(self):
        if self.is_group:
            if not self.member_code:
                self.member_code = frappe.scrub(self.member_label).upper()[:140]
            return
        if not self.member_code:
            frappe.throw("Member Code is required for leaf nodes (Is Group = unchecked).")
        dupe = frappe.db.exists(
            "Reporting Hierarchy Member",
            {
                "reporting_hierarchy": self.reporting_hierarchy,
                "member_code": self.member_code,
                "name": ["!=", self.name],
            },
        )
        if dupe:
            frappe.throw(
                f"Member code '{self.member_code}' already exists in this hierarchy "
                f"({dupe})."
            )

    def _validate_no_cycles(self):
        if not self.parent_member:
            return
        seen = {self.name}
        current = self.parent_member
        while current:
            if current in seen:
                frappe.throw("Parent chain forms a cycle — choose a different parent.")
            seen.add(current)
            current = frappe.db.get_value(
                "Reporting Hierarchy Member",
                current,
                "parent_member",
            )