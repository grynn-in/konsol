"""The site's declared analysis dimensions, read in one place (konsol#255).

Every konsol check that asks "may a trial-balance file carry this dim_*
column?" asks ``declared_dimensions`` here: the single CSV intake (Trial
Balance Submission) and the bulk one (``konsol.tb_bulk``). The judgement
itself belongs to ``konsol.tb_dimension_model``, which is pure and takes the
rows; this module is the one place that goes and gets them — the shape
``konsol.group_chart`` has for the chart of accounts (konsol#182), and for the
same reason: a rule that each caller looks up for itself is a rule each caller
can get differently wrong.

Until this module existed, both intakes took ``declared_dimensions=()`` and
**no** production caller passed anything. So the accepted set was empty on
every real upload, and every ``dim_*`` header was refused with "create the
Dimension dim_cost_center before a file may carry it" while the administrator
was looking at that exact Dimension, Published and ticked. The parsers were
right about everything they were told; nobody ever told them what the site
declared.

Why every row, not only the Published and ticked ones
-----------------------------------------------------
``accepted_dimension_columns`` does the filtering, and it is the only thing
that may: ``dimension_problems`` needs the rejected rows to say WHICH of
declare / publish / tick is missing, because the fix differs in each case.
Filter at the query and a Draft dimension comes back as "not declared at all",
sending the administrator to create a record that is open on their screen —
the same unactionable refusal this module exists to end, one step further on.
Two of the model's four refusals would be unreachable in production, and its
``status`` and ``in_trial_balance`` checks would become dead code, since every
row it was handed would satisfy them by construction.

``konsol.schema_apply._sync_tb_dimension_columns`` does filter, correctly: it
only ever needs the accepted names, because those are the only columns it may
create. It could read them as ``accepted_dimension_columns(declared_dimensions())``
and share this one query; that is left alone deliberately (konsol#255 row 13
does not own that module).
"""
import frappe

from konsol import tb_dimension_model as M

DOCTYPE = "Dimension"

#: What a declared dimension is, as ``konsol.tb_dimension_model`` reads it.
FIELDS = ("dimension_name", "status", M.FLAG)


def declared_dimensions():
    """The site's Dimension records, as the trial-balance intakes judge them.

    Empty on a site the doctype has not reached yet (a hot-copied release
    before migrate): no dim_* column is accepted then, so a file carrying one
    is **refused** rather than accepted with its values silently dropped —
    the position ``group_chart.chart_accounts`` takes for an unreached chart.

    ``get_all``, not ``get_list``: which dimensions the site declares is
    validation, not a list somebody is being shown, and a submitter whose
    permissions hide a Dimension record must not thereby have their file
    refused for carrying it.

    Not cached. The set changes the moment an administrator publishes a
    Dimension or ticks ``in_trial_balance``, and the very next upload has to
    see it; a TTL here would refuse a file for a dimension that is on screen,
    Published and ticked, which is the bug this module exists to fix. The
    query is one small unfiltered table read, once per parsed file.
    """
    if not frappe.db.table_exists(DOCTYPE):
        return []
    return frappe.get_all(DOCTYPE, fields=list(FIELDS), limit_page_length=0)
