# demo_data

Shipped rows that are **data**, not configuration — seeded once into an empty
site and never touched again.

This directory exists because `konsol/fixtures/` cannot hold them.
`frappe.utils.fixtures.import_fixtures` imports **every `.json` file in the
`fixtures/` directory** on every `bench migrate`, regardless of what the
`fixtures` hook lists (the hook is only read when *exporting*), and each import
goes through `import_doc` → `delete_old_doc`, which force-deletes the existing
document — bypassing even the submitted-document guard — before reinserting the
shipped version.

For configuration that is fine: the shipped value is the truth. For ownership it
is not. Verified on a running site: an Ownership Period edited from 80% to 65%
was back at 80% after one `bench migrate`, and the figures
`lift_ownership_to_ownership_period` had just carried over from the tree would
have gone the same way. That is the "it re-ran and reverted the publish" failure
F2 removes from the dbt side, arriving through Frappe instead.

Loaded by `konsol.install._bootstrap_ownership_periods`, which inserts only when
the doctype is completely empty.
