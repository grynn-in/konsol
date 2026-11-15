"""K.EPMSAVE states its currency and amount basis (konsol#348 S2-X3).

budget_cell_save requires both and defaults neither (18 Sep 2026: every
amount declares its currency; Q2-c: the basis is never defaulted). The add-in
therefore takes them as two arguments of its own, appended after `node` so
that no existing argument moves, and refuses a call that leaves either blank
before anything is sent: the cell names the argument to fill.

Excel requires optional parameters to come last, and `dimensions`,
`hierarchy` and `node` are optional, so the two are declared optional in
functions.json; the function itself refuses them blank.
"""
import json
import os

from konsol.tests.test_excel_addin_dimensions import _call, _metadata

_ARGS = (100, "ZZE", 2026, 1, "ZZ6100", "ZZ_PLAN", "base")
_OK = {"message": {"status": "ok", "value": 100}}


def test_epmsave_declares_currency_and_amount_basis_after_node():
    params = _metadata()["EPMSAVE"]["parameters"]
    names = [p["name"] for p in params]
    assert names[10:12] == ["currency", "amountBasis"], names
    for p in params[10:12]:
        assert p["type"] == "string", p
        assert "required" in p["description"].lower(), p
    assert "Movement" in params[11]["description"]
    assert "Year-to-date" in params[11]["description"]


def test_epmsave_sends_currency_and_amount_basis():
    out = _call("EPMSAVE", *_ARGS, None, None, None, "EUR", "Year-to-date", reply=_OK)
    assert out["error"] is None, out
    (req,) = out["requests"]
    assert req["body"]["currency"] == "EUR"
    assert req["body"]["amount_basis"] == "Year-to-date"


def test_epmsave_sends_the_values_as_given():
    # Judging the code or the basis is the server's: nothing is mapped here.
    out = _call("EPMSAVE", *_ARGS, None, None, None, " eur ", "movement", reply=_OK)
    assert out["error"] is None, out
    body = out["requests"][0]["body"]
    assert body["currency"] == " eur "
    assert body["amount_basis"] == "movement"


def test_epmsave_without_a_currency_is_refused_before_sending():
    for blank in (None, "", [[""]]):
        out = _call("EPMSAVE", *_ARGS, None, None, None, blank, "Movement")
        assert out["error"] is not None, (blank, out)
        assert out["error"]["code"] == "#VALUE!", out
        assert "currency" in out["error"]["message"], out
        assert "11" in out["error"]["message"], out
        assert out["requests"] == [], blank


def test_epmsave_without_an_amount_basis_is_refused_before_sending():
    out = _call("EPMSAVE", *_ARGS, None, None, None, "EUR", None)
    assert out["error"] is not None, out
    assert "amountBasis" in out["error"]["message"], out
    assert "12" in out["error"]["message"], out
    assert out["requests"] == []


def test_an_old_ten_argument_epmsave_is_refused_naming_both():
    # A formula written before S2-X3 stops at `node`: it is refused in the
    # cell, never sent without a currency.
    out = _call("EPMSAVE", *_ARGS, [["dim_region=EU"]], "ZZ_H", "ZZ_N")
    assert out["error"] is not None, out
    assert "currency" in out["error"]["message"]
    assert "amountBasis" in out["error"]["message"]
    assert out["requests"] == []


def test_handbook_documents_the_new_arguments():
    page = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "handbook_pages", "excel-addin.md")
    text = open(page).read()
    assert ("=K.EPMSAVE(amount, entity, year, period, account, scenarioId, layer, "
            "[dimensions], [hierarchy], [node], currency, amountBasis, [asOfDate])") in text


def test_a_403_denial_shows_the_servers_reason_in_the_cell():
    # S2-X3: budget_cell_save now refuses an access denial with HTTP 403. The
    # cell names the denial, and still says how to sign in, since a 403 can
    # also mean the session is gone.
    reply = {"exc_type": "PermissionError",
             "_server_messages": json.dumps([json.dumps(
                 {"message": "Not permitted to write entity ZZE"})])}
    out = _call("EPMSAVE", *_ARGS, None, None, None, "EUR", "Movement", reply=reply, status=403)
    assert out["error"] is not None, out
    assert "Not permitted to write entity ZZE" in out["error"]["message"], out
    assert "sign in" in out["error"]["message"], out


def test_a_bare_403_still_asks_to_sign_in():
    out = _call("EPMSAVE", *_ARGS, None, None, None, "EUR", "Movement", reply={}, status=403)
    assert out["error"]["message"].startswith("Not logged in"), out


# ---- S3 K5 (QB5 a): a forecast write names its round by asOfDate -----------------
# budget_cell_save requires as_of_date for a forecast scenario and refuses it
# for any other, so the add-in sends it as given and never fills it in. It is
# the 13th argument, after amountBasis, so no existing argument moves.

def test_epmsave_declares_as_of_date_last_and_optional():
    params = _metadata()["EPMSAVE"]["parameters"]
    assert params[-1]["name"] == "asOfDate", [p["name"] for p in params]
    assert params[-1].get("optional") is True
    assert "forecast" in params[-1]["description"] and "YYYY-MM-DD" in params[-1]["description"]
    assert "round" in params[-1]["description"]


def test_epmsave_sends_the_as_of_date_as_given():
    out = _call("EPMSAVE", *_ARGS, None, None, None, "EUR", "Movement", "2027-06-30", reply=_OK)
    assert out["error"] is None, out
    (req,) = out["requests"]
    assert req["body"]["as_of_date"] == "2027-06-30"


def test_epmsave_without_an_as_of_date_sends_none_the_server_judges_the_scenario():
    for blank in (None, "", [[""]]):
        out = _call("EPMSAVE", *_ARGS, None, None, None, "EUR", "Movement", blank, reply=_OK)
        assert out["error"] is None, (blank, out)
        assert "as_of_date" not in out["requests"][0]["body"], blank


def test_epmsave_turns_an_excel_date_serial_into_the_iso_date():
    # A date cell reaches a string parameter as its serial number; the server
    # would otherwise be asked for a round "as of 46568".
    import datetime
    serial = (datetime.date(2027, 6, 30) - datetime.date(1899, 12, 30)).days
    out = _call("EPMSAVE", *_ARGS, None, None, None, "EUR", "Movement", serial, reply=_OK)
    assert out["error"] is None, out
    assert out["requests"][0]["body"]["as_of_date"] == "2027-06-30"
    out = _call("EPMSAVE", *_ARGS, None, None, None, "EUR", "Movement", str(serial), reply=_OK)
    assert out["requests"][0]["body"]["as_of_date"] == "2027-06-30"


def test_a_different_as_of_date_is_a_different_save_not_a_cache_hit():
    # EPMSAVE skips a write whose arguments are unchanged; the round is an argument.
    out = _call("EPMSAVE", *_ARGS, None, None, None, "EUR", "Movement", "2027-03-31", reply=_OK)
    assert out["error"] is None and len(out["requests"]) == 1, out
    js = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "public", "excel-addin", "functions.js")).read()
    body = js.split("function epmSave(")[1].split("\nfunction ")[0]
    key_expr = body.split("var key = ")[1].split(";")[0]
    assert "asOfDate" in key_expr or "asOf" in key_expr, key_expr


def test_the_handbook_explains_the_as_of_date_argument():
    page = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "handbook_pages", "excel-addin.md")
    text = open(page).read()
    assert "asOfDate" in text and "forecast" in text and "2.0.4.0" in text
