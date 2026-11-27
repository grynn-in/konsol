"""The one rule for a name that K.EPM reads and ClickHouse stores as an
identifier or a bound value: a lower-case letter, then lower-case letters,
digits or underscores (konsol#564).

No frappe import: the K.EPM readers (api.py, hierarchy_query.py) and the
Budget Layer doctype all import it from here, so a code the doctype accepts is
a code the readers accept.
"""
import re

SAFE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*\Z")

#: The rule in words, for refusal messages.
SAFE_IDENTIFIER_RULE = "lower-case: start with a letter a-z, then only a-z, 0-9 or underscore"
