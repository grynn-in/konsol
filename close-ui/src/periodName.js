// konsol#305 review-w5: periodName.js — the one "FY2025 P07" format.
//
// The live `period_code` is "P07" alone (measured 6 Oct), ambiguous across a
// year boundary (P12 reverses into next year's P01). Every screen names a
// period from its fiscal year and period through this function; none keeps
// its own copy. The server's twin is konsol/close/period_name.py.

export function periodName(fiscalYear, fiscalPeriod) {
	if (!Number.isInteger(fiscalYear)) {
		throw new Error(`periodName: no fiscal year (${fiscalYear})`);
	}
	if (!Number.isInteger(fiscalPeriod)) {
		throw new Error(`periodName: no fiscal period (${fiscalPeriod})`);
	}
	return `FY${fiscalYear} P${String(fiscalPeriod).padStart(2, "0")}`;
}
