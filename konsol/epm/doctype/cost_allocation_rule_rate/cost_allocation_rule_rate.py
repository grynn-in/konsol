"""Cost Allocation Rule Rate: child row of a Rule Set, keyed by step (konsol#269 2b-2).

A Declared Rate step charges ``rate`` per driver unit inside the dated window
``effective_from``..``effective_to`` (blank = still applies). The Rule Set
controller validates the rows together with the rules.
"""
from frappe.model.document import Document


class CostAllocationRuleRate(Document):
    pass
