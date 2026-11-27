"""Report templates: load and validate the template files (konsol#548 v1).

A report template is a JSON file ``konsol/report_templates/<id>.json``
(Deepak Pai, 27 Nov 2026, Q2). This module loads the files and refuses every
broken rule by name: each problem reads ``<file>: <key path>: <rule>``, and
``load_templates`` reports every problem in every file, not only the first.
Pure: no frappe, stdlib only, so the host tests run it directly. The rules
below are the schema; ``validate`` enforces all of them.

Top level
---------
Keys: ``id``, ``version``, ``order``, ``title``, ``description``, ``mode``,
``sheet_name``, ``params``, ``sign``, ``header``, ``series``, ``columns``,
``rows``, ``number_format``, ``errors``, ``source``, ``max_rows``, ``fields``.
Any other key is refused.

- Required: ``id``, ``version``, ``order``, ``title``, ``mode``,
  ``sheet_name``, ``params``, ``rows``.
- ``id``: lower-case identifier, equal to the file stem, unique across files.
- ``version``, ``order``: integers. Templates sort by ``order``, then ``id``.
- ``title``, ``description``, ``sheet_name``: strings.
- ``mode``: ``"formulas"`` (the sheet holds K.EPM formulas) or ``"snapshot"``
  (the sheet holds values from a registered source).
- Formulas only: ``series`` and ``columns`` (both required), ``sign``.
- Snapshot only: ``source``, ``max_rows``, ``fields`` (all required).
- ``number_format`` (optional): ``{"default": fmt, <series name>: fmt}``.
- ``errors`` (optional): ``{code: message}``, messages may carry tokens.

params (list, required; may be empty)
-------------------------------------
Every param has ``name`` (identifier, unique), ``type``, optional
``required`` (bool) and ``label`` (string). Per type, the extra keys allowed:

=================  =========================================================
type               extra keys
=================  =========================================================
entity, group      none
scope              ``kinds``: non-empty subset of entity, group, node
fiscal_year        none
period             ``depends_on``: [a fiscal_year param]
scenario           ``scenario_type``: budget | forecast; ``depends_on``;
                   ``default``: ``"rule"`` (the site's scenario rule picks it)
hierarchy          none
hierarchy_node     ``depends_on``
dimension_value    ``depends_on``
account            none
account_list       none
account_heading    none
choice             ``choices``: non-empty list of distinct strings;
                   ``default``: one of the choices (required)
text               ``default`` (string), ``echo_only`` (bool)
int                ``default`` (integer)
=================  =========================================================

``depends_on`` names params declared in the same template, never the param
itself.

sign (formulas, optional)
-------------------------
Either ``{"convention": "debit_positive" | "credit_positive", "sentence": str}``
or ``{"param": <choice param>, "sentences": {choice: str}}``, whose sentence
keys equal the param's choices exactly.

header (optional list)
----------------------
Cells ``{"at": "A1", "text": str}`` or ``{"at": "C1", "param": <param>}``.
``at`` is an A1-style cell reference (``A1`` … ``XFD1048576``).

series (formulas)
-----------------
A non-empty map ``name -> series``; ``name`` is an identifier. Each series has
a ``caption`` and exactly one of:

- ``read``: ``{"entity"?: word, "group"?: word, "node"?: word}``, the dataset
  word K.EPM reads for each scope kind. Every kind the template can be run for
  needs a word: the ``kinds`` of each scope param, ``entity`` for an entity
  param, ``group`` for a group param. Optional ``scenario_id``: ``"{<scenario
  param>}"``; optional ``measure``: string.
- ``formula``: an expression over other series names, numbers and
  ``+ - * / ( )``, e.g. ``"act - bud"``. It may not name an unknown series or
  itself, and formulas may not form a cycle.

columns (formulas)
------------------
``{"start_col": "B", "header_rows": [2] | [2, 3], "groups": [...]}``. Each group
is ``{"over": regular_periods | quarters | fy | ytd, "label": token string,
"series": [series names], "through"?: <period param>}``. ``through`` is
allowed on a ``ytd`` group only. Every series of a ``ytd`` group must also be
carried by a ``regular_periods`` group (YTD sums those cells).

rows
----
Formulas: ``{"start_row": int, "blocks": [...]}``; snapshot:
``{"start_row": int, "header_row": bool}``. Block ``type`` and keys:

=================  =========================================================
type               keys
=================  =========================================================
accounts           id?, filter {statement_section?, account_type? [str],
                   sub_section?}, one_chart? bool, caption
account_tree       id?, root? (an account_heading param, or a section name),
                   depth? positive int, one_chart? bool, caption
account_list       id?, param (account_list param), caption
hierarchy_nodes    id?, hierarchy (hierarchy param), node? (hierarchy_node
                   param), account (account param), caption
total              id?, of [block ids], caption, bold? bool
heading            caption
blank              none
=================  =========================================================

Block ids are unique. A total's ``of`` names blocks of this template that are
not totals.

snapshot
--------
``source``: a name in ``SNAPSHOT_SOURCES``. ``max_rows``: positive integer.
``fields``: non-empty list of ``{"h": header, "field": identifier,
"number"?: bool, "default"?: token string}``.

Tokens
------
Captions, labels, header text, sign sentences, error messages and field
defaults may carry ``{token}``. Allowed: ``{account.name}``,
``{account.code}``, ``{node.label}``, ``{scope.code}``, ``{scope.name}``,
``{scope.kind}``, ``{sign.sentence}``, ``{title}``, ``{month_abbrev}``,
``{label}``, ``{code}``, ``{number}``, ``{charts}`` (the several-charts error), ``{param.<name>}`` and ``{<name>}`` for
a declared param, and ``{<name>.code}`` for a period param. Any other token is
refused.
"""
import json
import os
import re

TOP_KEYS = (
    "id", "version", "order", "title", "description", "mode", "sheet_name",
    "params", "sign", "header", "series", "columns", "rows", "number_format",
    "errors", "source", "max_rows", "fields",
)
REQUIRED_TOP = ("id", "version", "order", "title", "mode", "sheet_name", "params", "rows")
MODES = ("formulas", "snapshot")
FORMULAS_ONLY = ("series", "columns", "sign")
FORMULAS_REQUIRED = ("series", "columns")
SNAPSHOT_ONLY = ("source", "max_rows", "fields")
SNAPSHOT_REQUIRED = ("source", "max_rows", "fields")

#: Snapshot sources the server can fill. T6 registers the reader for each.
SNAPSHOT_SOURCES = ("entity_trial_balance",)

SCOPE_KINDS = ("entity", "group", "node")
SCENARIO_TYPES = ("budget", "forecast")
SIGN_CONVENTIONS = ("debit_positive", "credit_positive")
COLUMN_GROUPS = ("regular_periods", "quarters", "fy", "ytd")

_COMMON_PARAM_KEYS = ("name", "type", "required", "label")
PARAM_TYPES = {
    "entity": (),
    "group": (),
    "scope": ("kinds",),
    "fiscal_year": (),
    "period": ("depends_on",),
    "scenario": ("scenario_type", "depends_on", "default"),
    "hierarchy": (),
    "hierarchy_node": ("depends_on",),
    "dimension_value": ("depends_on",),
    "account": (),
    "account_list": (),
    "account_heading": (),
    "choice": ("choices", "default"),
    "text": ("default", "echo_only"),
    "int": ("default",),
}

BLOCK_TYPES = {
    "accounts": ("id", "filter", "one_chart", "caption"),
    "account_tree": ("id", "root", "depth", "one_chart", "caption"),
    "account_list": ("id", "param", "caption"),
    "hierarchy_nodes": ("id", "hierarchy", "node", "account", "caption"),
    "total": ("id", "of", "caption", "bold"),
    "heading": ("caption",),
    "blank": (),
}
ACCOUNTS_FILTER_KEYS = ("statement_section", "account_type", "sub_section")

FIXED_TOKENS = (
    "account.name", "account.code", "node.label", "scope.code", "scope.name",
    "scope.kind", "sign.sentence", "title", "month_abbrev", "label", "code", "number", "charts",
)

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TEMPLATE_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_A1 = re.compile(r"^([A-Z]{1,3})([1-9][0-9]{0,6})$")
_COLUMN = re.compile(r"^[A-Z]{1,3}$")
_WORD = re.compile(r"^[A-Za-z][A-Za-z0-9_.]*$")
_TOKEN = re.compile(r"\{([^{}]*)\}")
_PARAM_REF = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*)\}$")
_FORMULA_PART = re.compile(r"\s*(?:([A-Za-z_][A-Za-z0-9_]*)|(\d+(?:\.\d+)?)|([-+*/()]))")

_MAX_COLUMN = 16384  # XFD
_MAX_ROW = 1048576


class TemplateError(Exception):
    """One or more template files are invalid; ``problems`` lists every one."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__(
            f"{len(self.problems)} report template problem(s):\n" + "\n".join(self.problems)
        )


def load_templates(directory):
    """Every ``*.json`` template in ``directory``, validated: ``{id: template}``.

    Sorted by ``order``, then ``id``. Raises TemplateError listing every
    problem in every file, including an id declared by two files.
    """
    if not os.path.isdir(directory):
        raise TemplateError([f"{directory}: the report template directory does not exist"])
    problems = []
    files = []  # (file name, template, valid)
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        stem = name[: -len(".json")]
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as f:
                template = json.load(f)
        except (OSError, ValueError) as e:
            problems.append(f"{name}: (file): not valid JSON ({e})")
            continue
        found = validate(template, stem)
        problems.extend(found)
        files.append((name, template, not found))
    # An id belongs to the file named after it; every other file declaring it
    # is refused, naming the owner.
    owners = {}
    for name, template, _ in files:
        tid = template.get("id") if isinstance(template, dict) else None
        if isinstance(tid, str) and (tid not in owners or name == f"{tid}.json"):
            owners[tid] = name
    loaded = []
    for name, template, valid in files:
        tid = template.get("id") if isinstance(template, dict) else None
        if isinstance(tid, str) and owners[tid] != name:
            problems.append(f"{name}: id: id {tid!r} is also declared by {owners[tid]}")
        elif valid:
            loaded.append(template)
    if problems:
        raise TemplateError(problems)
    loaded.sort(key=lambda t: (t["order"], t["id"]))
    return {t["id"]: t for t in loaded}


def validate(template, stem):
    """Every problem in ``template`` (loaded from ``<stem>.json``); [] when valid."""
    return _Checker(stem).run(template)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _column_number(letters):
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n


class _Checker:
    def __init__(self, stem):
        self.file = f"{stem}.json"
        self.stem = stem
        self.problems = []
        self.params = {}  # name -> param dict (well-formed ones)

    def add(self, path, rule):
        self.problems.append(f"{self.file}: {path}: {rule}")

    # -- shared helpers ---------------------------------------------------

    def keys(self, obj, allowed, path):
        for key in obj:
            if key not in allowed:
                self.add(f"{path}.{key}" if path else key,
                         f"unknown key (allowed: {', '.join(allowed) or 'none'})")

    def string(self, obj, key, path, required=True, tokens=False):
        if key not in obj:
            if required:
                self.add(f"{path}.{key}" if path else key, "is required")
            return None
        value = obj[key]
        p = f"{path}.{key}" if path else key
        if not isinstance(value, str):
            self.add(p, "must be a string")
            return None
        if tokens:
            self.tokens(value, p)
        return value

    def boolean(self, obj, key, path):
        if key in obj and not isinstance(obj[key], bool):
            self.add(f"{path}.{key}", "must be true or false")

    def param_of(self, name, types, path, what):
        if not isinstance(name, str):
            self.add(path, f"must name {what}")
            return
        param = self.params.get(name)
        if param is None:
            self.add(path, f"unknown param {name!r}")
        elif param["type"] not in types:
            self.add(path, f"{name!r} is not {what}")

    def tokens(self, text, path):
        for token in _TOKEN.findall(text):
            if not self.token_ok(token):
                self.add(path, f"unknown token '{{{token}}}'")

    def token_ok(self, token):
        if token in FIXED_TOKENS:
            return True
        if token.startswith("param."):
            return token[len("param."):] in self.params
        if token.endswith(".code"):
            param = self.params.get(token[: -len(".code")])
            return param is not None and param["type"] == "period"
        return token in self.params

    # -- run --------------------------------------------------------------

    def run(self, t):
        if not isinstance(t, dict):
            self.add("(file)", "a template must be a JSON object")
            return self.problems
        for key in t:
            if key not in TOP_KEYS:
                self.add(key, "unknown top-level key")
        for key in REQUIRED_TOP:
            if key not in t:
                self.add(key, "is required")

        tid = t.get("id")
        if "id" in t:
            if not isinstance(tid, str) or not _TEMPLATE_ID.match(tid):
                self.add("id", f"{tid!r} is not a lower-case identifier")
            elif tid != self.stem:
                self.add("id", f"{tid!r} does not match the file name {self.file!r}")
        for key in ("version", "order"):
            if key in t and not _is_int(t[key]):
                self.add(key, f"must be an integer, not {t[key]!r}")
        for key in ("title", "description", "sheet_name"):
            self.string(t, key, "", required=False)
        mode = t.get("mode")
        if "mode" in t and mode not in MODES:
            self.add("mode", f"{mode!r}: must be one of {', '.join(MODES)}")
            mode = None

        self.check_params(t.get("params"))

        if mode == "formulas":
            for key in SNAPSHOT_ONLY:
                if key in t:
                    self.add(key, "only a snapshot template may declare it")
            for key in FORMULAS_REQUIRED:
                if key not in t:
                    self.add(key, "a formulas template must declare it")
        elif mode == "snapshot":
            for key in FORMULAS_ONLY:
                if key in t:
                    self.add(key, "only a formulas template may declare it")
            for key in SNAPSHOT_REQUIRED:
                if key not in t:
                    self.add(key, "a snapshot template must declare it")

        if "header" in t:
            self.check_header(t["header"])
        if "errors" in t:
            self.check_errors(t["errors"])

        if mode == "formulas":
            # None when series is missing or broken: its names are then not checked
            # again in columns and number_format, which would only repeat it.
            series = self.check_series(t.get("series")) if "series" in t else None
            if "sign" in t:
                self.check_sign(t["sign"])
            if "columns" in t:
                self.check_columns(t["columns"], series)
            if "rows" in t:
                self.check_formula_rows(t["rows"])
            if "number_format" in t:
                self.check_number_format(t["number_format"], series)
        elif mode == "snapshot":
            if "source" in t and t["source"] not in SNAPSHOT_SOURCES:
                self.add("source", f"{t['source']!r} is not a registered snapshot source "
                                   f"({', '.join(SNAPSHOT_SOURCES)})")
            if "max_rows" in t and not (_is_int(t["max_rows"]) and t["max_rows"] > 0):
                self.add("max_rows", "must be a positive integer")
            if "fields" in t:
                self.check_fields(t["fields"])
            if "rows" in t:
                self.check_snapshot_rows(t["rows"])
            if "number_format" in t:
                self.check_number_format(t["number_format"], set())
        return self.problems

    # -- params -----------------------------------------------------------

    def check_params(self, params):
        if params is None:
            return
        if not isinstance(params, list):
            self.add("params", "must be a list")
            return
        # First pass: names and types, so depends_on may name a later param.
        for i, p in enumerate(params):
            path = f"params[{i}]"
            if not isinstance(p, dict):
                self.add(path, "must be an object")
                continue
            name = p.get("name")
            if not isinstance(name, str) or not _IDENT.match(name):
                self.add(f"{path}.name", f"{name!r} is not an identifier")
                continue
            if name in self.params:
                self.add(f"{path}.name", f"{name!r} is used twice")
                continue
            ptype = p.get("type")
            if ptype not in PARAM_TYPES:
                self.add(f"{path}.type", f"unknown param type {ptype!r} "
                                         f"(allowed: {', '.join(PARAM_TYPES)})")
                continue
            self.params[name] = p
        for i, p in enumerate(params):
            if not isinstance(p, dict) or p.get("type") not in PARAM_TYPES:
                continue
            if self.params.get(p.get("name")) is not p:
                continue
            self.check_param(p, f"params[{i}]")

    def check_param(self, p, path):
        ptype = p["type"]
        self.keys(p, _COMMON_PARAM_KEYS + PARAM_TYPES[ptype], path)
        self.boolean(p, "required", path)
        self.string(p, "label", path, required=False)
        if "depends_on" in p:
            deps = p["depends_on"]
            if not isinstance(deps, list):
                self.add(f"{path}.depends_on", "must be a list of param names")
            else:
                for j, dep in enumerate(deps):
                    dp = f"{path}.depends_on[{j}]"
                    if dep == p["name"]:
                        self.add(dp, f"{dep!r} depends on itself")
                    elif ptype == "period":
                        self.param_of(dep, ("fiscal_year",), dp, "a fiscal_year param")
                    elif not isinstance(dep, str) or dep not in self.params:
                        self.add(dp, f"unknown param {dep!r}")
        if ptype == "scope":
            kinds = p.get("kinds")
            if (not isinstance(kinds, list) or not kinds
                    or any(k not in SCOPE_KINDS for k in kinds) or len(set(kinds)) != len(kinds)):
                self.add(f"{path}.kinds", f"{kinds!r}: must be a non-empty subset of "
                                          f"{', '.join(SCOPE_KINDS)}")
        elif ptype == "scenario":
            st = p.get("scenario_type")
            if st not in SCENARIO_TYPES:
                self.add(f"{path}.scenario_type", f"{st!r}: must be one of {', '.join(SCENARIO_TYPES)}")
            if "default" in p and p["default"] != "rule":
                self.add(f"{path}.default", f"{p['default']!r}: the only scenario default is 'rule'")
        elif ptype == "choice":
            choices = p.get("choices")
            if (not isinstance(choices, list) or not choices
                    or not all(isinstance(c, str) for c in choices) or len(set(choices)) != len(choices)):
                self.add(f"{path}.choices", "must be a non-empty list of distinct strings")
            elif p.get("default") not in choices:
                self.add(f"{path}.default", f"{p.get('default')!r} is not one of the choices")
        elif ptype == "text":
            self.string(p, "default", path, required=False)
            self.boolean(p, "echo_only", path)
        elif ptype == "int":
            if "default" in p and not _is_int(p["default"]):
                self.add(f"{path}.default", "must be an integer")

    # -- header, errors, sign ----------------------------------------------

    def check_header(self, header):
        if not isinstance(header, list):
            self.add("header", "must be a list of cells")
            return
        for i, cell in enumerate(header):
            path = f"header[{i}]"
            if not isinstance(cell, dict):
                self.add(path, "must be an object")
                continue
            self.keys(cell, ("at", "text", "param"), path)
            self.cell_ref(cell.get("at"), f"{path}.at")
            if ("text" in cell) == ("param" in cell):
                self.add(path, "needs exactly one of text or param")
            elif "text" in cell:
                self.string(cell, "text", path, tokens=True)
            elif not isinstance(cell["param"], str) or cell["param"] not in self.params:
                self.add(f"{path}.param", f"unknown param {cell['param']!r}")

    def cell_ref(self, ref, path):
        m = _A1.match(ref) if isinstance(ref, str) else None
        if not m or _column_number(m.group(1)) > _MAX_COLUMN or int(m.group(2)) > _MAX_ROW:
            self.add(path, f"{ref!r} is not an A1-style cell reference")

    def check_errors(self, errors):
        if not isinstance(errors, dict):
            self.add("errors", "must be an object of code: message")
            return
        for code, message in errors.items():
            if not isinstance(message, str):
                self.add(f"errors.{code}", "must be a string")
            else:
                self.tokens(message, f"errors.{code}")

    def check_sign(self, sign):
        if not isinstance(sign, dict):
            self.add("sign", "must be an object")
            return
        if "convention" in sign:
            self.keys(sign, ("convention", "sentence"), "sign")
            if sign["convention"] not in SIGN_CONVENTIONS:
                self.add("sign.convention", f"{sign['convention']!r}: must be one of "
                                            f"{', '.join(SIGN_CONVENTIONS)}")
            self.string(sign, "sentence", "sign", tokens=True)
        elif "param" in sign:
            self.keys(sign, ("param", "sentences"), "sign")
            name = sign["param"]
            param = self.params.get(name) if isinstance(name, str) else None
            sentences = sign.get("sentences")
            if param is None or param["type"] != "choice":
                self.add("sign.param", f"{name!r} is not a choice param")
            if not isinstance(sentences, dict) or not all(isinstance(v, str) for v in sentences.values()):
                self.add("sign.sentences", "must be an object of choice: sentence")
                return
            for key, sentence in sentences.items():
                self.tokens(sentence, f"sign.sentences.{key}")
            if param is not None and param["type"] == "choice" and isinstance(param.get("choices"), list):
                if set(sentences) != set(param["choices"]):
                    self.add("sign.sentences", f"keys {sorted(sentences)} do not equal the choices "
                                               f"{sorted(param['choices'])} of {name!r}")
        else:
            self.add("sign", "needs convention + sentence, or param + sentences")

    # -- series -----------------------------------------------------------

    def required_kinds(self):
        kinds = []
        for p in self.params.values():
            if p["type"] == "scope" and isinstance(p.get("kinds"), list):
                found = [k for k in p["kinds"] if k in SCOPE_KINDS]
            elif p["type"] in ("entity", "group"):
                found = [p["type"]]
            else:
                found = []
            kinds.extend(k for k in found if k not in kinds)
        return kinds

    def check_series(self, series):
        if not isinstance(series, dict) or not series:
            self.add("series", "must be a non-empty object of name: series")
            return None
        names = set(series)
        kinds = self.required_kinds()
        deps = {}
        for name, s in series.items():
            path = f"series.{name}"
            if not _IDENT.match(name):
                self.add(path, f"{name!r} is not an identifier")
            if not isinstance(s, dict):
                self.add(path, "must be an object")
                continue
            self.string(s, "caption", path, tokens=True)
            if ("read" in s) == ("formula" in s):
                self.add(path, "needs exactly one of read or formula")
                continue
            if "read" in s:
                self.keys(s, ("caption", "read", "scenario_id", "measure"), path)
                self.check_read(s["read"], f"{path}.read", kinds)
                if "scenario_id" in s:
                    m = _PARAM_REF.match(s["scenario_id"]) if isinstance(s["scenario_id"], str) else None
                    if not m:
                        self.add(f"{path}.scenario_id", "must be '{<scenario param>}'")
                    else:
                        self.param_of(m.group(1), ("scenario",), f"{path}.scenario_id",
                                      "a scenario param")
                self.string(s, "measure", path, required=False)
            else:
                self.keys(s, ("caption", "formula"), path)
                deps[name] = self.check_formula(name, s["formula"], f"{path}.formula", names)
        self.check_cycles(deps)
        return names

    def check_read(self, read, path, kinds):
        if not isinstance(read, dict):
            self.add(path, "must be an object of kind: dataset word")
            return
        self.keys(read, SCOPE_KINDS, path)
        for kind, word in read.items():
            if kind in SCOPE_KINDS and (not isinstance(word, str) or not _WORD.match(word)):
                self.add(f"{path}.{kind}", f"{word!r} is not a dataset word")
        for kind in kinds:
            if kind not in read:
                self.add(path, f"no read word for kind {kind!r}, which the template's scope allows")

    def check_formula(self, name, formula, path, names):
        if not isinstance(formula, str) or not formula.strip():
            self.add(path, "must be a non-empty expression")
            return set()
        refs = []
        pos = 0
        depth = 0
        while pos < len(formula):
            m = _FORMULA_PART.match(formula, pos)
            if not m or m.end() == pos:
                if formula[pos:].strip():
                    self.add(path, f"{formula!r}: only series names, numbers and + - * / ( ) are allowed")
                    return set()
                break
            pos = m.end()
            if m.group(1):
                refs.append(m.group(1))
            elif m.group(3) == "(":
                depth += 1
            elif m.group(3) == ")":
                depth -= 1
                if depth < 0:
                    break
        if depth != 0:
            self.add(path, f"{formula!r}: unbalanced parentheses")
        found = set()
        for ref in refs:
            if ref == name:
                self.add(path, f"{formula!r} references itself")
            elif ref not in names:
                self.add(path, f"unknown series {ref!r}")
            else:
                found.add(ref)
        return found

    def check_cycles(self, deps):
        done = set()
        for start in sorted(deps):
            stack = [(start, [start])]
            while stack:
                node, trail = stack.pop()
                for nxt in sorted(deps.get(node, ())):
                    if nxt == start:
                        self.add("series", "formula cycle " + " -> ".join(trail + [start]))
                        done.update(trail)
                        stack = []
                        break
                    if nxt not in trail and nxt not in done:
                        stack.append((nxt, trail + [nxt]))
            done.add(start)

    def check_number_format(self, fmt, series):
        if not isinstance(fmt, dict):
            self.add("number_format", "must be an object")
            return
        for key, value in fmt.items():
            if key != "default" and series is not None and key not in series:
                self.add(f"number_format.{key}", f"unknown series {key!r}")
            if not isinstance(value, str):
                self.add(f"number_format.{key}", "must be a string")

    # -- columns ----------------------------------------------------------

    def check_columns(self, columns, series):
        if not isinstance(columns, dict):
            self.add("columns", "must be an object")
            return
        self.keys(columns, ("start_col", "header_rows", "groups"), "columns")
        start = columns.get("start_col")
        if not isinstance(start, str) or not _COLUMN.match(start) or _column_number(start) > _MAX_COLUMN:
            self.add("columns.start_col", f"{start!r} is not a column letter")
        rows = columns.get("header_rows")
        if (not isinstance(rows, list) or len(rows) not in (1, 2)
                or not all(_is_int(r) and r > 0 for r in rows)):
            self.add("columns.header_rows", "must list one or two positive row numbers")
        groups = columns.get("groups")
        if not isinstance(groups, list) or not groups:
            self.add("columns.groups", "must be a non-empty list")
            return
        regular = set()
        for g in groups:
            if isinstance(g, dict) and g.get("over") == "regular_periods" and isinstance(g.get("series"), list):
                regular.update(s for s in g["series"] if isinstance(s, str))
        for i, g in enumerate(groups):
            path = f"columns.groups[{i}]"
            if not isinstance(g, dict):
                self.add(path, "must be an object")
                continue
            self.keys(g, ("over", "label", "series", "through"), path)
            over = g.get("over")
            if over not in COLUMN_GROUPS:
                self.add(f"{path}.over", f"unknown column group {over!r} "
                                         f"(allowed: {', '.join(COLUMN_GROUPS)})")
            self.string(g, "label", path, tokens=True)
            names = g.get("series")
            if not isinstance(names, list) or not names:
                self.add(f"{path}.series", "must be a non-empty list of series names")
                names = []
            for j, s in enumerate(names):
                if series is None:
                    continue
                if s not in series:
                    self.add(f"{path}.series[{j}]", f"unknown series {s!r}")
                elif over == "ytd" and s not in regular:
                    self.add(f"{path}.series",
                             f"no regular_periods group carries series {s!r}, which YTD sums")
            if "through" in g:
                if over != "ytd":
                    self.add(f"{path}.through", "only a ytd group may declare it")
                else:
                    self.param_of(g["through"], ("period",), f"{path}.through", "a period param")

    # -- rows -------------------------------------------------------------

    def start_row(self, rows):
        if not (_is_int(rows.get("start_row")) and rows["start_row"] > 0):
            self.add("rows.start_row", "must be a positive integer")

    def check_snapshot_rows(self, rows):
        if not isinstance(rows, dict):
            self.add("rows", "must be an object")
            return
        self.keys(rows, ("start_row", "header_row"), "rows")
        self.start_row(rows)
        self.boolean(rows, "header_row", "rows")

    def check_formula_rows(self, rows):
        if not isinstance(rows, dict):
            self.add("rows", "must be an object")
            return
        self.keys(rows, ("start_row", "blocks"), "rows")
        self.start_row(rows)
        blocks = rows.get("blocks")
        if not isinstance(blocks, list) or not blocks:
            self.add("rows.blocks", "must be a non-empty list")
            return
        ids = {}  # id -> block type
        for i, b in enumerate(blocks):
            if isinstance(b, dict) and "id" in b:
                bid = b["id"]
                if not isinstance(bid, str) or not _IDENT.match(bid):
                    self.add(f"rows.blocks[{i}].id", f"{bid!r} is not an identifier")
                elif bid in ids:
                    self.add(f"rows.blocks[{i}].id", f"{bid!r} is used twice")
                else:
                    ids[bid] = b.get("type")
        for i, b in enumerate(blocks):
            self.check_block(b, f"rows.blocks[{i}]", ids)

    def check_block(self, b, path, ids):
        if not isinstance(b, dict):
            self.add(path, "must be an object")
            return
        btype = b.get("type")
        if btype not in BLOCK_TYPES:
            self.add(f"{path}.type", f"unknown block type {btype!r} "
                                     f"(allowed: {', '.join(BLOCK_TYPES)})")
            return
        self.keys(b, ("type",) + BLOCK_TYPES[btype], path)
        if btype != "blank":
            self.string(b, "caption", path, tokens=True)
        self.boolean(b, "one_chart", path)
        self.boolean(b, "bold", path)
        if btype == "accounts":
            f = b.get("filter")
            if not isinstance(f, dict):
                self.add(f"{path}.filter", "is required: an object")
            else:
                self.keys(f, ACCOUNTS_FILTER_KEYS, f"{path}.filter")
                self.string(f, "statement_section", f"{path}.filter", required=False)
                self.string(f, "sub_section", f"{path}.filter", required=False)
                at = f.get("account_type", [])
                if not isinstance(at, list) or not all(isinstance(x, str) for x in at):
                    self.add(f"{path}.filter.account_type", "must be a list of strings")
        elif btype == "account_tree":
            root = b.get("root")
            if "root" in b:
                if not isinstance(root, str) or not root:
                    self.add(f"{path}.root", "must be a param name or a section name")
                elif root in self.params:
                    self.param_of(root, ("account_heading",), f"{path}.root", "an account_heading param")
            if "depth" in b and not (_is_int(b["depth"]) and b["depth"] > 0):
                self.add(f"{path}.depth", "must be a positive integer")
        elif btype == "account_list":
            self.param_of(b.get("param"), ("account_list",), f"{path}.param", "an account_list param")
        elif btype == "hierarchy_nodes":
            self.param_of(b.get("hierarchy"), ("hierarchy",), f"{path}.hierarchy", "a hierarchy param")
            if "node" in b:
                self.param_of(b["node"], ("hierarchy_node",), f"{path}.node", "a hierarchy_node param")
            self.param_of(b.get("account"), ("account",), f"{path}.account", "an account param")
        elif btype == "total":
            of = b.get("of")
            if not isinstance(of, list) or not of:
                self.add(f"{path}.of", "must be a non-empty list of block ids")
                return
            for j, ref in enumerate(of):
                rp = f"{path}.of[{j}]"
                if not isinstance(ref, str) or ref not in ids:
                    self.add(rp, f"unknown block {ref!r}")
                elif ids[ref] == "total":
                    self.add(rp, f"{ref!r} is a total; a total sums account blocks only")

    # -- snapshot fields --------------------------------------------------

    def check_fields(self, fields):
        if not isinstance(fields, list) or not fields:
            self.add("fields", "must be a non-empty list")
            return
        for i, fd in enumerate(fields):
            path = f"fields[{i}]"
            if not isinstance(fd, dict):
                self.add(path, "must be an object")
                continue
            self.keys(fd, ("h", "field", "number", "default"), path)
            self.string(fd, "h", path)
            field = fd.get("field")
            if not isinstance(field, str) or not _IDENT.match(field):
                self.add(f"{path}.field", f"{field!r} is not an identifier")
            self.boolean(fd, "number", path)
            self.string(fd, "default", path, required=False, tokens=True)
