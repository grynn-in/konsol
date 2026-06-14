"""Gold Model — assigns each gold dbt model to a Build Governance domain.

Frappe is the source of truth for the model -> domain mapping. On save, the
mapping is written into dbt_project.yml's models block as
`+tags: ['gold', 'domain:<build_domain>']`, which the governed build selects
with `tag:domain:<build_domain>` (see konsol.tasks.SCOPE_SELECTOR). Previously
these tags were hand-maintained in dbt_project.yml — a wrong edit changed which
models a governed build rebuilds.
"""
import frappe
from frappe.model.document import Document

from konsol.dbt_config import regenerate_model_domains

# Must stay in sync with konsol.tasks.SCOPE_SELECTOR (the build scopes that map
# to tag:domain:<x>). "full" is not a per-model domain.
VALID_DOMAINS = {"staging", "actuals", "scenarios", "consolidation"}


class GoldModel(Document):

    def validate(self):
        if self.build_domain not in VALID_DOMAINS:
            frappe.throw(
                f"Build Domain '{self.build_domain}' is invalid. "
                f"Allowed: {', '.join(sorted(VALID_DOMAINS))}.",
                frappe.ValidationError,
            )

    def on_update(self):
        # Re-emit the gold models' domain tags into dbt_project.yml.
        regenerate_model_domains()

    def on_trash(self):
        regenerate_model_domains()
