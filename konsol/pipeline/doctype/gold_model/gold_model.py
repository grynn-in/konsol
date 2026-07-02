"""Gold Model — assigns each gold dbt model to a Build Governance domain.

Frappe is the source of truth for the model -> domain mapping. On save, the
mapping is written into dbt_project.yml's models block as
`+tags: ['gold', 'domain:<build_domain>']`, which the governed build selects
with `tag:domain:<build_domain>` (see konsol.tasks.SCOPE_SELECTOR). Previously
these tags were hand-maintained in dbt_project.yml — a wrong edit changed which
models a governed build rebuilds.
"""
from frappe.model.document import Document

from konsol.dbt_config import regenerate_model_domains


class GoldModel(Document):
    # build_domain is a Link to Build Scope, so Frappe enforces that the domain
    # exists — no manual allow-list needed here.

    def on_update(self):
        # Re-emit the gold models' domain tags into dbt_project.yml.
        regenerate_model_domains()

    def on_trash(self):
        regenerate_model_domains()
