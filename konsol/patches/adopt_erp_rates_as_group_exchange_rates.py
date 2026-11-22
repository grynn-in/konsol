"""Retired: the one-time adoption of the rates each period was already translated at (konsol#103).

It recorded, as approved Group Exchange Rate rows labelled source "Adoption",
the rate gold_consolidated_trial_balance translated each currency at, so the
governed translation (konsolidat#93) gave the same figures on its first build.

Group Exchange Rate is retired (konsol#592 T26, patches/retire_group_exchange_rate.py):
rates are held on Rate Sheets, and the doctype and its adoption code left the
app. A no-op, kept in patches.txt so a site that ran it keeps its record and a
site that has not run it skips it. Such a site has nothing to adopt into a
retired doctype; its first approved Statutory sheet gives the translation its
rates, and the close gate names every period that has none.

The ISO Currency magnitude references this patch once seeded are seeded by
konsol.currency_references.seed_iso_currencies after every migrate.
"""


def execute():
    print("konsol#103 adoption: retired with Group Exchange Rate (konsol#592); nothing to do")
