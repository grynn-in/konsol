"""Cost Allocation Driver: the statistic a cost pool is spread on (konsol#269).

The driver names a Dimension. That Dimension must be Published, so a rule set
can only spread on a dimension the rest of the model already recognises.
"""
import frappe
from frappe.model.document import Document


class CostAllocationDriver(Document):
    def validate(self):
        status = frappe.db.get_value("Dimension", self.dimension, "status")
        if status != "Published":
            frappe.throw(
                f"Dimension {self.dimension} is {status or 'not found'}. "
                "A cost allocation driver needs a Published dimension."
            )
