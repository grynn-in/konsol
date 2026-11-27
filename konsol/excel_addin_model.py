"""Pure rules for the Excel add-in's site manifest and cross-origin access.

konsol#605, decided by Deepak Pai on 27 Nov 2026: build both A and B.

A. Each site serves its own manifest, rendered from its ``host_name``. Every
   URL in it is that site's, ``<Id>`` is derived from the site's address, and
   ``DisplayName`` names the site. A site without a usable ``host_name`` is
   refused. There is no fallback address.
B. One add-in install may call another site. That site allows the add-in's
   origin only when Close Settings → Excel Add-in Origins lists it exactly.
   The CORS headers go on the add-in endpoints only, never as ``*``, and never
   with ``Access-Control-Allow-Credentials``: the add-in sends its token in
   ``X-Konsolidat-Token`` and sends no cookies.

No frappe here: ``konsol/excel_addin.py`` is the glue.
"""
import re
import uuid
from xml.sax.saxutils import escape

#: The konsol.api methods the add-in calls (functions.js, index.html).
#: test_excel_addin_cors.py checks this list against the add-in's source.
ADDIN_ENDPOINTS = (
    "epm_batch",
    "budget_cell_save",
    "excel_addin_auth",
    "excel_addin_whoami",
    "excel_addin_logout",
    "build_cell_map",
    "build_snapshot",
)
#: Frappe v16 routes one RPC under three prefixes (frappe/api/__init__.py:
#: Submount "/api", "/api/v1" and "/api/v2", each with "/method/<method>").
#: An add-in method is matched by its name under any of them, so Frappe's own
#: credentialed CORS is off on every route to it (konsol#618 review).
METHOD_PREFIXES = ("/api/method/", "/api/v1/method/", "/api/v2/method/")
_ADDIN_METHODS = frozenset("konsol.api." + name for name in ADDIN_ENDPOINTS)

ALLOW_METHODS = "GET, POST, OPTIONS"
#: The headers the add-in sends: its JSON body, Accept, and its token.
ALLOW_HEADERS = "Accept, Content-Type, X-Konsolidat-Token"
#: How long a browser may cache a preflight answer: ten minutes, so a removed
#: origin stops working soon after the list changes.
PREFLIGHT_MAX_AGE = "600"

ORIGINS_SETTING = "Close Settings → Excel Add-in Origins"

PLACEHOLDERS = ("{{SITE_URL}}", "{{ADDIN_ID}}", "{{DISPLAY_NAME}}")

# A host is DNS labels: letters, digits, hyphens and dots. No wildcard, no
# userinfo, no IPv6 literal (Office needs a certificate for a name anyway).
_HOST = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$")
_ORIGIN = re.compile(r"^(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*)://(?P<rest>.*)$")


def parse_origin(text):
    """``text`` as an https origin, ``https://host[:port]`` in lower case.

    A single trailing ``/`` is dropped, and so is ``:443``. Anything else that
    is not the origin itself is refused with a ``ValueError`` that says why:
    a wildcard, a scheme other than https, a path, query, fragment or user,
    a malformed host or port.
    """
    value = (text or "").strip()
    if not value:
        raise ValueError("The origin is empty.")
    if "*" in value:
        raise ValueError(f"{value}: no wildcard. List each origin exactly, e.g. https://site.example.")
    m = _ORIGIN.match(value)
    if not m or m.group("scheme").lower() != "https":
        raise ValueError(f"{value}: an origin starts with https:// (Office add-ins need HTTPS).")
    rest = m.group("rest")
    if rest.endswith("/"):
        rest = rest[:-1]
    if any(c in rest for c in "/?#@\\"):
        raise ValueError(f"{value}: give the site's origin only, with no path, query or user: "
                         "https://host or https://host:port.")
    host, _, port = rest.partition(":")
    host = host.lower()
    if not host or not _HOST.match(host):
        raise ValueError(f"{value}: the host is not a valid name.")
    if port or rest.endswith(":"):
        if not port.isdigit() or not 0 < int(port) < 65536:
            raise ValueError(f"{value}: the port must be a number from 1 to 65535.")
        if int(port) == 443:
            port = ""
    return "https://" + host + (":" + str(int(port)) if port else "")


def parse_origin_list(text):
    """One origin per line, blank lines skipped, as a tuple of normalised
    origins. A bad line is refused by its number; a repeated origin too."""
    origins = []
    for number, line in enumerate((text or "").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            origin = parse_origin(line)
        except ValueError as e:
            raise ValueError(f"Line {number}: {e}") from None
        if origin in origins:
            raise ValueError(f"Line {number}: {origin} is listed twice.")
        origins.append(origin)
    return tuple(origins)


def is_addin_path(path):
    """True when ``path`` calls one of the add-in methods under any of the
    three API prefixes, exactly: no suffix, no doctype segment."""
    for prefix in METHOD_PREFIXES:
        if (path or "").startswith(prefix):
            return path[len(prefix):] in _ADDIN_METHODS
    return False


def cors_headers(path, method, origin, allowed):
    """The CORS headers for one request, as a dict.

    Only the add-in endpoints get any. On those, a request with an Origin gets
    ``Vary: Origin``; it gets ``Access-Control-Allow-Origin`` only when that
    Origin, exactly as the browser sent it, is in ``allowed``. A preflight
    also gets the methods, the headers and a max age. Never ``*`` and never
    ``Access-Control-Allow-Credentials``.
    """
    if not is_addin_path(path) or not origin:
        return {}
    headers = {"Vary": "Origin"}
    if origin not in tuple(allowed or ()):
        return headers
    headers["Access-Control-Allow-Origin"] = origin
    if (method or "").upper() == "OPTIONS":
        headers["Access-Control-Allow-Methods"] = ALLOW_METHODS
        headers["Access-Control-Allow-Headers"] = ALLOW_HEADERS
        headers["Access-Control-Max-Age"] = PREFLIGHT_MAX_AGE
    return headers


# -- the site manifest ----------------------------------------------------------

def _no_host_name(why):
    return ValueError(
        f"This site's host_name {why}, so konsol cannot build an Excel add-in "
        "manifest for it. A System Manager sets the site's public https address with "
        "`bench --site <site> set-config host_name https://<public address>`. "
        "konsol never falls back to another server's address.")


def site_base_url(host_name):
    """The site's https origin from its ``host_name``. Refused (``ValueError``)
    when there is no host_name, no scheme, a scheme other than https, or a
    path: the add-in is served from the site's root."""
    value = (host_name or "").strip()
    if not value:
        raise _no_host_name("is not set (there is no host_name)")
    if "://" not in value:
        raise _no_host_name(f"is {value}, with no scheme")
    if not value.lower().startswith("https://"):
        raise _no_host_name(f"is {value}, not https (Office add-ins need HTTPS)")
    try:
        return parse_origin(value)
    except ValueError:
        raise _no_host_name(f"is {value}, which has a path or is not a plain address") from None


def manifest_id(base):
    """A UUID stable for the site's address and different between sites."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, base))


def display_name(base):
    return "Konsolidat (%s)" % base.split("://", 1)[1]


def manifest_filename(site):
    """``konsolidat-<site>.xml``; anything but letters, digits, dot, dash and
    underscore in the site name becomes a dash."""
    return "konsolidat-%s.xml" % re.sub(r"[^A-Za-z0-9._-]+", "-", site or "site")


def render_manifest(template, base):
    """The template with the site's address, Id and display name filled in.
    A template that lacks a placeholder, or keeps one, is refused."""
    missing = [p for p in PLACEHOLDERS if p not in template]
    if missing:
        raise ValueError("The manifest template has no " + ", ".join(missing) + ".")
    xml = (template
           .replace("{{SITE_URL}}", escape(base, {'"': "&quot;"}))
           .replace("{{ADDIN_ID}}", manifest_id(base))
           .replace("{{DISPLAY_NAME}}", escape(display_name(base), {'"': "&quot;"})))
    if "{{" in xml or "}}" in xml:
        raise ValueError("The manifest template has a placeholder konsol does not fill.")
    return xml
