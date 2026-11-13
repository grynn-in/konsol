from frappe.model.document import Document


class CostAllocationRun(Document):
    """Cost Allocation Run (konsol#269). Shape only: Compute Weights (K7) and
    the submit/cancel gates (K8) are added later."""
