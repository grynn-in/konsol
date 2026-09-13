"""The one-time adoption of the rates each period was already translated at (konsol#103).

Group Exchange Rate is new, and the translation that reads only governed rates
(konsolidat#93) fails loudly on a period with no approved rate. This records,
as approved rows authored by the system and labelled source "Adoption", the
rate gold_consolidated_trial_balance translated each currency, group currency
and period at, so that translation gives the same figures on its first build.
See konsol.group_rates.adopt_erp_rates for what is and isn't adopted; it
prints every row it adopts or skips into the migrate log.

A patch, so the upgrade cannot forget it; patches run before model sync here
(patches.txt has no sections), so the doctype is loaded first. A fresh install
marks this done without running it, which is right: it has nothing translated.
The warehouse must be reachable: a migrate that cannot read it fails here, by
design, rather than leaving the next translation build to fail on every period.
"""
import frappe


def execute():
    frappe.reload_doc("consolidation", "doctype", "group_exchange_rate")
    from konsol.group_rates import adopt_erp_rates

    adopt_erp_rates()
