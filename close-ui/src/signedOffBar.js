// konsol#305 L01b: signedOffBar.js
//
// Whether the viewed period counts as "signed off" for AppShell's Viewer
// banner ("You are viewing a signed-off period."). L01 found the banner on
// FY2025 P07 and P12 for a Viewer, both Open and unsigned:
// showProvisionalBar (AppShell.vue) tested only `period != provisional`, not
// the period's own sign-off state. A period counts here only when its
// sign-off is actually signed (A04 period_model.period_states' `is_signed`,
// assertion_run.SIGNED_STATES) or its effective status has moved past Open
// (Closed/Locked, konsol.fiscal_status_model) — never merely because it
// differs from the provisional period.
//
// Pure: takes the period state object period_api.get_context returns as
// `selected` (or an entry of `periods`); no frappe/vue import.

export function isSignedOffPeriod(selected) {
  if (!selected) return false;
  if (selected.is_signed) return true;
  return selected.status != null && selected.status !== "Open";
}
