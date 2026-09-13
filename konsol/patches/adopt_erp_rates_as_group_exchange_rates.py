"""The one-time adoption of the rates each period was already translated at (konsol#103).

Group Exchange Rate is new, and the translation that reads only governed rates
(konsolidat#93) fails loudly on a period with no approved rate. This records,
as approved rows authored by the system and labelled source "Adoption", the
rate gold_consolidated_trial_balance translated each currency, group currency
and period at, so that translation gives the same figures on its first build.
See konsol.group_rates.adopt_erp_rates for what is and isn't adopted; it
prints every row it adopts or skips into the migrate log.

A patch, so the upgrade cannot forget it. patches.txt has no sections, so it
runs BEFORE model sync and before after_migrate. It therefore loads what it
needs itself (review of #174: on a site upgrading from main, ISO Currency had
no usd_log10 column yet, MariaDB error 1054 failed every migrate; with the
column but no values, every row was refused and the patch still looked done):

1. the ISO Currency doctype, so usd_log10 exists;
2. the Group Exchange Rate doctype;
3. the ISO currencies' magnitude references, filled only where unset
   (konsol.currency_references.seed_iso_currencies);
4. the adoption. It plans first, and only a currency it would ENTER needs a
   reference: one translated at the 1.0 parity fallback or at two rates is
   skipped, reference or not, and its skip says it has none. If a currency it
   would enter still has no reference, it writes nothing and raises, naming
   each one ("Create or edit ISO Currency X, set USD Reference (log10)"), so
   the migrate stops and the patch runs again once they are set.

A fresh install marks this done without running it, which is right: it has
nothing translated. The warehouse must be reachable: a migrate that cannot
read it fails here, by design, rather than leaving the next translation build
to fail on every period.
"""
import frappe


def execute():
    frappe.reload_doc("epm", "doctype", "iso_currency")
    frappe.reload_doc("consolidation", "doctype", "group_exchange_rate")
    from konsol.currency_references import seed_iso_currencies

    seeded = seed_iso_currencies()
    print(f"konsol#103: ISO Currency seeded: {len(seeded['inserted'])} inserted, "
          f"{len(seeded['filled'])} magnitude references filled")
    from konsol.group_rates import adopt_erp_rates

    return adopt_erp_rates()
