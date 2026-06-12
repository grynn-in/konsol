"""Tests for security hardening fixes.

Covers:
  - #8  table-name validation before SQL interpolation
  - #9  entity-level authorization on epm_value / epm_batch
  - #10 constant-time webhook secret comparison
  - #11 ClickHouse HTTPS / TLS support

These are pure-function and source-inspection tests — no live site, no
ClickHouse, and (deliberately) no monkeypatching. The security *policy* lives
in side-effect-free helpers that take plain arguments, so it can be asserted
directly; the thin Frappe wiring around it is checked by source inspection,
matching the style used elsewhere in this test suite.
"""
import os

from konsol import api
from konsol import clickhouse


APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _api_src():
    with open(os.path.join(APP_DIR, "api.py")) as f:
        return f.read()


def _clickhouse_src():
    with open(os.path.join(APP_DIR, "clickhouse.py")) as f:
        return f.read()


# ---------------------------------------------------------------------------
# #11 ClickHouse TLS  (pure connection_url + source guarantees)
# ---------------------------------------------------------------------------

def test_connection_url_defaults_to_http():
    conn = {"host": "localhost", "port": "8123", "secure": False}
    assert clickhouse.connection_url(conn) == "http://localhost:8123/"


def test_connection_url_uses_https_when_secure():
    conn = {"host": "ch.example.com", "port": "8443", "secure": True}
    assert clickhouse.connection_url(conn) == "https://ch.example.com:8443/"


def test_both_query_paths_pass_tls_verify():
    """Both ClickHouse callers must forward verify= to requests."""
    assert "verify=conn.get(\"verify\"" in _clickhouse_src()
    assert "verify=ch_settings.get(\"verify\"" in _api_src()


def test_query_paths_use_connection_url_not_hardcoded_http():
    # No raw f"http://... URL building left in the query paths.
    assert 'f"http://{ch_settings' not in _api_src()
    assert 'f"http://{conn' not in _clickhouse_src()


# ---------------------------------------------------------------------------
# #8 table-name validation  (pure regex)
# ---------------------------------------------------------------------------

def test_safe_table_name_accepts_qualified_name():
    assert api._SAFE_TABLE_NAME.match("epm_gold.budget_monthly_input")


def test_safe_table_name_blocks_injection():
    assert not api._SAFE_TABLE_NAME.match("gold.facts; DROP TABLE x --")
    assert not api._SAFE_TABLE_NAME.match("gold.facts WHERE 1=1")
    assert not api._SAFE_TABLE_NAME.match("facts")          # must be schema-qualified
    assert not api._SAFE_TABLE_NAME.match("gold.facts UNION SELECT 1")


def test_batch_query_validates_table_before_sql():
    """The FROM clause must be guarded by _SAFE_TABLE_NAME, before query exec."""
    src = _api_src()
    body = src.split("def _batch_query_clickhouse")[1].split("\ndef ")[0]
    # Table is validated and the bad-table branch sets an error + skips the row.
    assert "_SAFE_TABLE_NAME.match(table" in body
    assert "Invalid table identifier" in body
    # Validation must appear before the SQL string is assembled / executed.
    assert body.index("_SAFE_TABLE_NAME.match(table") < body.index("_clickhouse_query(")


# ---------------------------------------------------------------------------
# #9 entity-level authorization  (pure policy function)
# ---------------------------------------------------------------------------

def test_unrestricted_when_no_perm_doctype():
    assert api._resolve_allowed_entities("alice", [], "", {}) is None


def test_unrestricted_for_system_manager():
    assert api._resolve_allowed_entities(
        "alice", ["System Manager"], "Legal Entity",
        {"Legal Entity": [{"doc": "E1"}]}) is None


def test_unrestricted_for_administrator():
    assert api._resolve_allowed_entities("Administrator", [], "Legal Entity", {}) is None


def test_allow_list_from_user_permissions():
    allowed = api._resolve_allowed_entities(
        "alice", ["EPM User"], "Legal Entity",
        {"Legal Entity": [{"doc": "E1"}, {"doc": "E2"}]})
    assert allowed == {"E1", "E2"}


def test_empty_allow_list_when_perms_configured_but_none_granted():
    # Configured doctype but no grants → user sees no entities (deny-by-default).
    allowed = api._resolve_allowed_entities("alice", ["EPM User"], "Legal Entity", {})
    assert allowed == set()


def test_endpoints_enforce_entity_access():
    src = _api_src()
    # Single-value endpoint asserts access for its entity.
    assert "_assert_entity_access(entity)" in src
    # Batch endpoint resolves the allow-list once and denies per-row.
    batch = src.split("def epm_batch")[1].split("\ndef ")[0]
    assert "allowed_entities = _allowed_entities()" in batch
    assert "Not permitted to access entity" in batch


def test_assert_entity_access_raises_permission_error_source():
    body = _api_src().split("def _assert_entity_access")[1].split("\ndef ")[0]
    assert "raise frappe.PermissionError" in body
    assert "entity not in allowed" in body


# ---------------------------------------------------------------------------
# #10 constant-time webhook comparison  (source guarantee)
# ---------------------------------------------------------------------------

def test_webhook_uses_constant_time_compare():
    src = _api_src()
    assert "hmac.compare_digest" in src
    assert "secret != expected" not in src
