"""Allocation Tier — child table for tiered allocation rate bands.

PRD-20: Each tier defines a rate band with lower/upper bounds, rate, cap, and floor.
Synced to epm_staging.allocation_tiers via parent Allocation Rule's on_update hook.
"""
from frappe.model.document import Document


class AllocationTier(Document):
    pass
