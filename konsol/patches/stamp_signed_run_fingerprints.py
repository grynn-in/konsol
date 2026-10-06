"""Stamp every signed Assertion Run that has no numbers fingerprint (konsol#338).

#338-1 (Deepak Pai, 6 Oct 2026): a completed build voids a signature only
where the signed numbers changed, so every signed run needs the fingerprint
of the numbers it signed. The coordinator's call on the decision comment:
a run signed before fingerprints existed is stamped with the fingerprint of
the current warehouse. Any input change since it was signed has already
voided it (A63, #305-W4-4), so the current numbers are the signed numbers.

Signed means Signed Off, Acknowledged or Overridden. A Re-sign Needed or
unsigned run is left alone: ``sign_off_close`` refuses a run with no
fingerprint, so its checks run again. One warehouse read covers every
period. A second run finds nothing to stamp.

A warehouse that cannot be read fails the patch (and so the migrate), and
the next migrate runs it again: passing would leave signed runs that a
build check can only void. patches.txt has no sections, so this runs
pre_model_sync: it reloads Assertion Run first, for the new columns.
"""
import frappe


def execute():
    frappe.reload_doc("consolidation", "doctype", "assertion_run")

    from konsol.close import fingerprint
    from konsol.consolidation.doctype.assertion_run.assertion_run import SIGNED_STATES

    runs = frappe.get_all(
        "Assertion Run",
        filters={"signoff_status": ["in", list(SIGNED_STATES)],
                 "numbers_fingerprint": ["is", "not set"],
                 "fiscal_period": [">", 0]},
        fields=["name", "fiscal_year", "fiscal_period"], limit_page_length=0)
    if not runs:
        print("stamp_signed_run_fingerprints: no signed run without a fingerprint, nothing to do")
        return

    keys = {(int(r.fiscal_year), int(r.fiscal_period)) for r in runs}
    current = fingerprint.fingerprints(keys)
    as_of = fingerprint.latest_build_at()
    for r in runs:
        frappe.db.set_value(
            "Assertion Run", r.name,
            {"numbers_fingerprint": current[(int(r.fiscal_year), int(r.fiscal_period))],
             "fingerprint_as_of": as_of},
            update_modified=False)
    print("stamp_signed_run_fingerprints: stamped %d signed run(s) across %d period(s)"
          % (len(runs), len(keys)))
