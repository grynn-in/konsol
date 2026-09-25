from frappe.model.document import Document


class EPMSettings(Document):
    # konsolidat#93 (decided 13 Sep 2026): the presentation currency lives on
    # each Consolidation Group node, which is what the translation reads. The
    # group consolidation currency setting that used to be here was read by
    # nothing, so it is gone (see the drop_epm_settings_… patch).
    pass
