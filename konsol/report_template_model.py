"""Report templates: load and validate the template files (konsol#548 v1).

A report template is a JSON file ``konsol/report_templates/<id>.json``
(Deepak Pai, 27 Nov 2026, Q2). This module loads the files and refuses every
broken rule by name: each problem reads ``<file>: <key path>: <rule>``, and
``load_templates`` reports every problem in every file, not only the first.
Pure: no frappe, stdlib only, so the host tests run it directly. The rules
below are the schema; ``validate`` enforces all of them. ``compile_formulas``
(end of the module) interprets a formulas template; its ``ctx`` contract is
written above it.

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
keys equal the param's choices exactly; each choice is a convention
(``debit_positive`` / ``credit_positive``).

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
carried by a ``regular_periods`` group (YTD sums those cells). A formula
series needs every series it names in the same group. With one header row, a
group of several series carries ``{series.caption}`` in its label (the only
place that token is allowed).

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
        self.param_index = {}  # name -> its index in params
        self.formula_refs = {}  # formula series name -> the series it names

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

    def tokens(self, text, path, extra=()):
        for token in _TOKEN.findall(text):
            if token not in extra and not self.token_ok(token):
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
            self.param_index[name] = i
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
            elif isinstance(param.get("choices"), list):
                wrong = [c for c in param["choices"] if c not in SIGN_CONVENTIONS]
                if wrong:
                    self.add(f"params[{self.param_index[name]}].choices",
                             f"{', '.join(repr(c) for c in wrong)}: the choices of the sign param "
                             f"must be sign conventions ({', '.join(SIGN_CONVENTIONS)})")
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
        self.formula_refs = deps
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
            label = self.string(g, "label", path)
            if label is not None:
                self.tokens(label, f"{path}.label", extra=("series.caption",))
            names = g.get("series")
            if not isinstance(names, list) or not names:
                self.add(f"{path}.series", "must be a non-empty list of series names")
                names = []
            if (label is not None and isinstance(rows, list) and len(rows) == 1
                    and len(names) > 1 and "{series.caption}" not in label):
                self.add(f"{path}.label", "one header row and several series: the label must "
                                          "carry {series.caption}, or the columns read alike")
            for s in names:
                missing = sorted(set(self.formula_refs.get(s, ())) - set(names))
                if s in self.formula_refs and missing:
                    self.add(f"{path}.series",
                             f"formula series {s!r} names {', '.join(repr(m) for m in missing)}, "
                             f"which this group does not carry")
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


# ===========================================================================
# FORMULAS-mode interpreter (konsol#548 v1, T5)
# ===========================================================================
#
# compile_formulas(template, params, ctx) turns a validated formulas template
# into an Excel cell map. It is pure: the caller resolves everything that
# needs the site and passes it in ``ctx``:
#
#   ctx["periods"]   the fiscal year's Regular periods, each
#                    {number, code, label, start_date}; ``label`` already falls
#                    back to the code (report_choices.periods). Required: a
#                    year without Regular periods is refused, never Jan-Dec.
#                    A period whose ``period_type`` is present and not
#                    "Regular" is refused.
#   ctx["accounts"]  {block key: [(code, caption), ...]} in display order, one
#                    list per ``accounts`` block. The block key is the block's
#                    ``id``, else ``blocks[<index>]``. The caption is written as
#                    given: the caller applies the code fallback
#                    (``account_name or main_account``), as api.py does today.
#   ctx["charts"]    optional {block key: [chart names]}: the charts the
#                    block's accounts came from. More than one on a
#                    ``one_chart`` block (accounts or account_tree) is refused
#                    with errors.several_charts.
#   ctx["trees"]     {block key: [{code, caption, depth, is_group, parent}]},
#                    one list per ``account_tree`` block, in DFS order (every
#                    parent before its children; ``parent`` None/"" for a
#                    root). A leaf (is_group false) is a K.EPM read. A heading
#                    (is_group true) is ``=SUM`` of its direct children's cells
#                    per column (a run of adjacent rows as a range, the rest
#                    comma-joined): headings never carry postings, so a heading
#                    without a child row in the list is refused. The caller
#                    must not cut the tree (``depth``) below a heading. Captions
#                    are written as given: the schema has no indent option.
#   ctx["account_lists"]  {block key: [(code, caption), ...]}, one list per
#                    ``account_list`` block, in the user's chosen order; read
#                    like ``accounts``.
#   ctx["nodes"]     {block key: [{code, label, depth, is_group}]}, one list per
#                    ``hierarchy_nodes`` block, in tree order. EVERY row, group
#                    or leaf, is a K.EPM hierarchy read of that node for the
#                    block's ``account`` param, entity ``ALL`` (hierarchy_query
#                    _ENTITY_WILDCARD), with the series' ``read["node"]`` word:
#                    a group node is read (the server rolls it up), never
#                    summed (plan decision D6).
#   ctx["scope_name"] optional: the display name of the scope, for the
#                    ``{scope.name}`` token. Absent, a text that uses the token
#                    is refused by name (no fallback to the code).
#
# Scope: exactly one entity, group or scope param. An entity param is kind
# ``entity``, a group param kind ``group``; a scope param's value is
# {kind: entity|group|node, code, hierarchy?} (a dict, or its JSON text), its
# kind one of the param's ``kinds``. The series reads ``read[kind]``; a kind
# without a word is refused by name. The K.EPM entity argument is the code for
# entity and group kinds (a group read with its group dataset word, e.g.
# consolidated); for kind node it is ``ALL``, with the scope's hierarchy and
# the code as the node. Tokens: {scope.code}, {scope.kind}, {scope.name}.
#
# A ``total`` sums the leaf rows of the blocks it names: never a heading, a
# total, or (hierarchy_nodes) a node whose child rows follow it in the list.
# ``heading`` writes its caption only; ``blank`` leaves the row empty.
#
# Columns (T11). Group by group, slot by slot, series by series: a
# regular_periods group has one slot per Regular period (Jan act, Jan bud, Jan
# var, Feb act, ...); quarters one per quarter Q1-Q4 that holds Regular
# periods (K.EPM period "Q1".., as period_read_model.resolve_period accepts);
# fy one slot (period "FY"); ytd one slot. With header_rows [h1, h2], row h1
# holds the group label, REPEATED on every column of its slot (no merged
# cells, so every column reads on its own), and row h2 the series caption.
# With [h], one row: the label, which must carry {series.caption} when the
# group has several series. Label tokens: {month_abbrev} {label} {code}
# {number} of the slot (a quarter: Q1/Q1/1; fy: FY/FY; ytd: those of its
# ``through`` period) plus every text token and {series.caption}.
#
# - ctx["periods"] items carry ``quarter`` (Q1-Q4) for a quarters group; a
#   Regular period without one is refused by name, never left out.
# - A read series writes K.EPM with its dataset word ``read[kind]``; a series
#   with ``scenario_id`` writes the param's value (only K.EPM's own default
#   ``actuals`` is omitted). An empty value, or the unresolved default
#   ``rule``, is refused: the caller resolves the scenario rule first.
# - ytd: ``=SUM`` of the same series' cells in the first regular_periods
#   group carrying it, from the first Regular period through the period in
#   the ``through`` param (its code or number); none, or not a Regular period
#   of the year, is refused.
# - A formula series is a cell formula over its sibling series' cells in the
#   same slot and row (``act - bud`` -> ``=B4-C4``): Actual - Budget in the
#   report's own sign (Deepak Pai, 27 Nov 2026, Q4). Division is plain: the
#   grammar has no IFERROR construct.
# - Sign: credit_positive (the convention, or the sign param's choice) writes
#   every READ as ``=-K.EPM(...)``; formula, YTD and SUM cells follow from
#   their inputs. No ``sign`` means debit_positive, K.EPM's own sign.
# - Totals and tree heading SUMs are per column, every column included.
# - formats: number_format gives each series column over the data rows
#   (start_row to the last row) its own format, else ``default``; adjacent
#   columns with one format share a range. A total with ``bold: true`` gives
#   {range: A<r>:<last col><r>, bold: true}. The ``formats`` key is present
#   only when one of them is declared.

_QUARTERS = ("Q1", "Q2", "Q3", "Q4")

MONTH_ABBREVS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

#: K.EPM's positional arguments, in functions.js makeReq order.
EPM_ARGS = ("entity", "year", "period", "account", "measure", "scenario",
            "dimensions", "scenario_id", "hierarchy", "node", "layer")
#: The entity argument of a cross-entity hierarchy read (functions.json:
#: "ALL for cross-entity hierarchy rollups"; hierarchy_query._ENTITY_WILDCARD).
EPM_ENTITY_ALL = "ALL"
#: K.EPM's own defaults, omitted from a formula: the dataset word and the
#: scenario id ``actuals``.
EPM_DEFAULTS = {"scenario": "actuals", "scenario_id": "actuals"}

_DEFAULT_ERRORS = {
    "no_rows": "{title}: the report has no rows.",
    "several_charts": "{title} reads one chart, and its accounts exist in charts {charts}.",
}


def _quote(value):
    return '"' + str(value).replace('"', '""') + '"'


def _epm_arg(name, value):
    if value is None or value == "":
        return ""
    if name == "year":
        return str(int(value))
    if name == "period":
        try:
            return str(int(value))
        except (TypeError, ValueError):
            return _quote(value)
    return _quote(value)


def epm_formula(entity, year, period, account, measure=None, scenario=None,
                dimensions=None, scenario_id=None, hierarchy=None, node=None, layer=None):
    """One ``=K.EPM(...)`` formula, positional in makeReq order.

    Text is quoted with ``"`` doubled; year and a numeric period are bare.
    ``scenario`` and ``scenario_id`` equal to ``actuals`` are K.EPM's defaults
    and are omitted; empty middle arguments stay as ``, ,``; trailing empty
    arguments are dropped.
    """
    values = dict(zip(EPM_ARGS, (entity, year, period, account, measure, scenario,
                                 dimensions, scenario_id, hierarchy, node, layer)))
    for name, default in EPM_DEFAULTS.items():
        if values[name] == default:
            values[name] = None
    args = [_epm_arg(name, values[name]) for name in EPM_ARGS]
    while args and args[-1] == "":
        args.pop()
    return "=K.EPM(" + ", ".join(args) + ")"


def column_letters(number):
    """1 -> A, 28 -> AB."""
    out = ""
    while number:
        number, rem = divmod(number - 1, 26)
        out = chr(65 + rem) + out
    return out


def _cell_range(col1, row1, col2, row2):
    first = f"{column_letters(col1)}{row1}"
    if (col1, row1) == (col2, row2):
        return first
    return f"{first}:{column_letters(col2)}{row2}"


def render(text, values):
    """Replace every ``{token}`` in ``text`` from ``values``; an unknown one is refused."""
    def one(m):
        token = m.group(1)
        if token not in values:
            raise ValueError(f"report template text {text!r}: no value for token '{{{token}}}'")
        return str(values[token])
    return _TOKEN.sub(one, text)


def _unsupported(template, what):
    return ValueError(
        f"Report template {template.get('id')!r}: {what} is not supported by the "
        f"formulas interpreter yet."
    )


def block_key(block, index):
    """The key of a row block in ctx: its id, else ``blocks[<index>]``."""
    return block.get("id") or f"blocks[{index}]"


def resolve_params(template, params):
    """The template's params from ``params`` + declared defaults, in declared order."""
    params = dict(params or {})
    declared = [p["name"] for p in template.get("params", [])]
    unknown = sorted(set(params) - set(declared))
    if unknown:
        raise ValueError(
            f"Report template {template.get('id')!r} has no param "
            f"{', '.join(repr(u) for u in unknown)} (declared: {', '.join(declared) or 'none'})."
        )
    out = {}
    for p in template.get("params", []):
        name = p["name"]
        value = params.get(name)
        if value is None or value == "":
            value = p.get("default")
        if value is None or value == "":
            if p.get("required"):
                raise ValueError(f"Report template {template.get('id')!r}: {name} is required.")
            value = None
        elif p["type"] in ("fiscal_year", "int"):
            value = int(value)
        out[name] = value
    return out


def _sign_sentence(template, params):
    sign = template.get("sign")
    if not sign:
        return ""
    if "sentence" in sign:
        return sign["sentence"]
    return sign["sentences"].get(params.get(sign["param"]), "")


def _token_values(template, params):
    values = {"title": template.get("title", ""), "sign.sentence": _sign_sentence(template, params)}
    for name, value in params.items():
        values[name] = "" if value is None else value
        values[f"param.{name}"] = values[name]
    return values


def _error(template, code, values):
    text = (template.get("errors") or {}).get(code) or _DEFAULT_ERRORS[code]
    return ValueError(render(text, values))


def regular_periods(periods, year):
    """``periods`` checked and sorted by number; none at all is refused."""
    if not periods:
        raise ValueError(
            f"No Regular periods are declared for FY{year}, so the report has no "
            f"columns. Declare the year's Regular periods on EPM Fiscal Year {year}."
        )
    for p in periods:
        ptype = p.get("period_type")
        if ptype is not None and ptype != "Regular":
            raise ValueError(
                f"Period {p.get('code')!r} of FY{year} is {ptype}, not Regular: "
                f"only Regular periods are report columns."
            )
    numbers = [int(p["number"]) for p in periods]
    if len(set(numbers)) != len(numbers):
        raise ValueError(f"FY{year} declares a Regular period number twice: {sorted(numbers)}.")
    return sorted(periods, key=lambda p: int(p["number"]))


def _start_month(period):
    start = period.get("start_date")
    if not start:
        return None
    if hasattr(start, "month"):
        return start.month
    m = re.match(r"^\d{4}-(\d{2})-\d{2}", str(start))
    return int(m.group(1)) if m else None


def _period_tokens(periods):
    """The label tokens of each period (decided by Deepak Pai, 27 Nov 2026, Q1).

    ``{month_abbrev}`` is the month abbreviation of the period's start only
    when every period starts in a different month of the year (Jan-Dec ->
    Jan..Dec, Apr-Mar -> Apr..Mar). Otherwise, or when a start date is
    missing, it is the period's declared ``label`` (already the code when no
    label is declared), so no two columns read the same month.
    """
    months = [_start_month(p) for p in periods]
    by_month = None not in months and len(set(months)) == len(months)
    out = []
    for p, month in zip(periods, months):
        declared = p.get("label") or p.get("code") or str(p["number"])
        out.append({
            "month_abbrev": MONTH_ABBREVS[month - 1] if by_month else declared,
            "label": declared,
            "code": p.get("code") or "",
            "number": int(p["number"]),
        })
    return out


def period_labels(periods, label, values=None):
    """The column label of each period: ``label`` rendered with its tokens."""
    return [render(label, dict(values or {}, **tokens)) for tokens in _period_tokens(periods)]


def find_period(periods, value):
    """The period of ``periods`` that a period param value names (its code or number); else None."""
    if value is None or value == "":
        return None
    text = str(value).strip()
    for p in periods:
        if p.get("code") and str(p["code"]).upper() == text.upper():
            return p
    try:
        number = int(text)
    except ValueError:
        return None
    for p in periods:
        if int(p["number"]) == number:
            return p
    return None


def _sign_convention(template, params):
    """debit_positive or credit_positive; a template without ``sign`` reads K.EPM's own sign."""
    sign = template.get("sign") or {}
    if "convention" in sign:
        return sign["convention"]
    if "param" in sign:
        value = params.get(sign["param"])
        if value not in SIGN_CONVENTIONS:
            raise ValueError(
                f"Report template {template.get('id')!r}: sign param {sign['param']!r} is "
                f"{value!r}, not a sign convention ({', '.join(SIGN_CONVENTIONS)})."
            )
        return value
    return "debit_positive"


def _formula_parts(formula):
    """[(kind, text)] of a validated formula: kind ``series``, ``number`` or ``op``."""
    parts = []
    pos = 0
    while pos < len(formula):
        m = _FORMULA_PART.match(formula, pos)
        if not m or m.end() == pos:
            break
        pos = m.end()
        if m.group(1):
            parts.append(("series", m.group(1)))
        elif m.group(2):
            parts.append(("number", m.group(2)))
        else:
            parts.append(("op", m.group(3)))
    return parts


def _row_refs(cols, row):
    """``B4:D4`` / ``B4,E4``: the cells of columns ``cols`` in ``row``, adjacent ones as a range."""
    runs = []
    for c in sorted(cols):
        if runs and c == runs[-1][1] + 1:
            runs[-1][1] = c
        else:
            runs.append([c, c])
    return ",".join(_cell_range(a, row, b, row) for a, b in runs)


def _column_slots(template, group, periods, values, year):
    """[(epm period | None, label tokens, through number | None)] of one column group."""
    tid = template.get("id")
    over = group["over"]
    if over == "regular_periods":
        return [(p["number"], tokens, None) for p, tokens in zip(periods, _period_tokens(periods))]
    if over == "quarters":
        missing = [p.get("code") or str(p["number"]) for p in periods if p.get("quarter") not in _QUARTERS]
        if missing:
            raise ValueError(
                f"Report template {tid!r}: a quarters column group needs the quarter of every "
                f"Regular period of FY{year}, and {', '.join(missing)} declare none (or not "
                f"Q1-Q4). Declare the quarter on EPM Fiscal Year {year}."
            )
        found = {p["quarter"] for p in periods}
        return [(q, {"label": q, "code": q, "number": i + 1}, None)
                for i, q in enumerate(_QUARTERS) if q in found]
    if over == "fy":
        return [("FY", {"label": "FY", "code": "FY"}, None)]
    # ytd: one slot, summing the regular_periods cells up to ``through``.
    name = group.get("through")
    value = values.get(name) if name else None
    if value is None or value == "":
        raise ValueError(
            f"Report template {tid!r}: a YTD column group sums through the period in param "
            f"{name!r}, and none is given."
        )
    through = find_period(periods, value)
    if through is None:
        raise ValueError(
            f"Report template {tid!r}: YTD through period {value!r} is not a Regular period "
            f"of FY{year} (declared: {', '.join(p.get('code') or str(p['number']) for p in periods)})."
        )
    tokens = _period_tokens(periods)[periods.index(through)]
    return [(None, tokens, int(through["number"]))]


def compile_formulas(template, params, ctx):
    """A formulas template + params + ctx -> the cell map (see the contract above).

    Returns {template_id, mode, <every param, in declared order>, sheet_name,
    cells, formats?, report}; ``report`` is {template_id, template_version,
    params, area}, ``area`` the A1 rectangle every cell and format lies in.
    ``formats`` is present only when the template declares number_format or a
    bold total. Refusals raise ValueError with the template's own error text
    where it declares one.
    """
    if template.get("mode") != "formulas":
        raise ValueError(f"Report template {template.get('id')!r} is not a formulas template.")
    tid = template.get("id")
    ctx = ctx or {}
    params = resolve_params(template, params)
    values = _token_values(template, params)

    scope = _scope(template, params)
    values["scope.code"] = "" if scope["code"] is None else scope["code"]
    values["scope.kind"] = scope["kind"]
    if ctx.get("scope_name") is not None:
        values["scope.name"] = ctx["scope_name"]
    years = [p["name"] for p in template["params"] if p["type"] == "fiscal_year"]
    if len(years) != 1:
        raise _unsupported(template, "a template without exactly one fiscal_year param")
    year = params[years[0]]
    negate = _sign_convention(template, params) == "credit_positive"

    all_series = template["series"]

    def read_for(name, kind):
        series = all_series[name]
        word = series["read"].get(kind)
        if not word:
            raise ValueError(
                f"Report template {tid!r}: series {name!r} has no "
                f"read word for scope kind {kind!r}, so it cannot be read for it."
            )
        scenario_id = None
        if "scenario_id" in series:
            pname = series["scenario_id"][1:-1]
            scenario_id = params.get(pname)
            if scenario_id is None or scenario_id == "":
                raise ValueError(
                    f"Report template {tid!r}: series {name!r} reads the scenario in param "
                    f"{pname!r}, and none is given."
                )
            if scenario_id == "rule":
                # The declared default "rule" names the site's scenario rule; the
                # caller resolves it to a scenario id before compiling.
                raise ValueError(
                    f"Report template {tid!r}: series {name!r} reads the scenario in param "
                    f"{pname!r}, which is still 'rule': resolve the site's scenario rule "
                    f"to a scenario id before compiling."
                )
        return {"scenario": word, "measure": series.get("measure"), "scenario_id": scenario_id}

    if scope["kind"] == "node":
        target = {"entity": EPM_ENTITY_ALL, "hierarchy": scope["hierarchy"], "node": scope["code"]}
    else:
        target = {"entity": scope["code"], "hierarchy": None, "node": None}

    periods = regular_periods(ctx.get("periods"), year)
    for p in template["params"]:
        if p["type"] == "period":
            found = find_period(periods, params.get(p["name"]))
            if found is not None:
                values[f"{p['name']}.code"] = found.get("code") or ""

    # -- columns: each group, each of its slots (period, quarter, FY, YTD),
    # each of its series, in that order.
    columns = template["columns"]
    header_rows = columns["header_rows"]
    first_col = _column_number(columns["start_col"])
    cols = []  # {col, letter, series, group, slot, period, through}
    for gi, group in enumerate(columns["groups"]):
        if len(header_rows) == 1 and len(group["series"]) > 1 and "{series.caption}" not in group["label"]:
            raise ValueError(
                f"Report template {tid!r}: columns.groups[{gi}] has several series and one header "
                f"row, so its label must carry {{series.caption}}, or the columns read alike."
            )
        for si, (period, tokens, through) in enumerate(_column_slots(template, group, periods,
                                                                       params, year)):
            for name in group["series"]:
                col = first_col + len(cols)
                caption_text = render(all_series[name]["caption"], values)
                cols.append({
                    "col": col, "letter": column_letters(col), "series": name, "group": gi,
                    "slot": si, "period": period, "through": through, "over": group["over"],
                    "caption": caption_text,
                    "label": render(group["label"], dict(values, **tokens,
                                                         **{"series.caption": caption_text})),
                })
    last_col = cols[-1]["col"]
    letters = [c["letter"] for c in cols]
    where = {(c["group"], c["slot"], c["series"]): c["col"] for c in cols}

    for c in cols:
        series = all_series[c["series"]]
        if "formula" in series:
            parts = _formula_parts(series["formula"])
            for kind, text in parts:
                if kind == "series" and (c["group"], c["slot"], text) not in where:
                    raise ValueError(
                        f"Report template {tid!r}: formula series {c['series']!r} names series "
                        f"{text!r}, which columns.groups[{c['group']}] does not carry."
                    )
            c["parts"] = parts
        elif c["over"] == "ytd":
            # The same series' cells of the first regular_periods group carrying it.
            source = next(gi for gi, g in enumerate(columns["groups"])
                          if g["over"] == "regular_periods" and c["series"] in g["series"])
            c["sum_cols"] = [k["col"] for k in cols
                             if k["group"] == source and k["series"] == c["series"]
                             and int(k["period"]) <= c["through"]]
        else:
            read_for(c["series"], scope["kind"])  # refuse a missing word before any row

    cells = []
    for cell in template.get("header", []):
        if "param" not in cell:
            value = render(cell["text"], values)
        elif cell["param"] == scope["param"]:
            value = scope["code"]  # a scope value is a dict; the cell shows its code
        else:
            value = params[cell["param"]]
        cells.append({"range": cell["at"], "values": [[value]]})
    cells.append({"range": _cell_range(first_col, header_rows[0], last_col, header_rows[0]),
                  "values": [[c["label"] for c in cols]]})
    if len(header_rows) == 2:
        cells.append({"range": _cell_range(first_col, header_rows[1], last_col, header_rows[1]),
                      "values": [[c["caption"] for c in cols]]})

    start_row = template["rows"]["start_row"]
    row = start_row
    block_rows = {}  # block key -> [leaf row numbers]: what a total sums
    bold_rows = []
    accounts = ctx.get("accounts") or {}
    charts = ctx.get("charts") or {}
    trees = ctx.get("trees") or {}
    account_lists = ctx.get("account_lists") or {}
    nodes = ctx.get("nodes") or {}

    def caption(r, text):
        cells.append({"range": f"A{r}", "values": [[text]]})

    def read_row(r, account, kind, at):
        out = []
        for c in cols:
            if "parts" in c:
                out.append("=" + "".join(
                    f"{column_letters(where[(c['group'], c['slot'], text)])}{r}" if k == "series" else text
                    for k, text in c["parts"]))
            elif "sum_cols" in c:
                out.append("=SUM(" + _row_refs(c["sum_cols"], r) + ")")
            else:
                f = epm_formula(at["entity"], year, c["period"], account, hierarchy=at["hierarchy"],
                                node=at["node"], **read_for(c["series"], kind))
                out.append("=-" + f[1:] if negate else f)
        cells.append({"range": _cell_range(first_col, r, last_col, r), "formulas": [out]})

    def sum_row(r, refs):
        cells.append({
            "range": _cell_range(first_col, r, last_col, r),
            "formulas": [["=SUM(" + _sum_refs(c, refs) + ")" for c in letters]],
        })

    def one_chart(block, key):
        found = sorted(set(charts.get(key) or ()))
        if block.get("one_chart") and len(found) > 1:
            raise _error(template, "several_charts", dict(values, charts=", ".join(found)))

    def account_lines(block, key, lines):
        nonlocal row
        if not lines:
            raise _error(template, "no_rows", values)
        block_rows[key] = []
        for code, text in lines:
            line = dict(values, **{"account.code": code, "account.name": text})
            caption(row, render(block["caption"], line))
            read_row(row, code, scope["kind"], target)
            block_rows[key].append(row)
            row += 1

    for i, block in enumerate(template["rows"]["blocks"]):
        key = block_key(block, i)
        btype = block["type"]
        if btype == "accounts":
            one_chart(block, key)
            account_lines(block, key, accounts.get(key) or [])
        elif btype == "account_list":
            account_lines(block, key, account_lists.get(key) or [])
        elif btype == "account_tree":
            one_chart(block, key)
            lines = trees.get(key) or []
            if not lines:
                raise _error(template, "no_rows", values)
            row_of, children = {}, {}
            for offset, n in enumerate(lines):
                code, parent = n["code"], n.get("parent")
                if code in row_of:
                    raise ValueError(f"Report template {tid!r}: block {key!r} "
                                     f"lists account {code!r} twice.")
                if parent:
                    if parent not in row_of:
                        raise ValueError(
                            f"Report template {tid!r}: block {key!r} lists account "
                            f"{code!r} before its parent {parent!r}, or without it; the tree must "
                            f"be in DFS order, every parent before its children."
                        )
                    children[parent].append(row + offset)
                row_of[code] = row + offset
                children[code] = []
            block_rows[key] = []
            for n in lines:
                r = row_of[n["code"]]
                caption(r, render(block["caption"], dict(values, **{
                    "account.code": n["code"], "account.name": n["caption"]})))
                if n.get("is_group"):
                    if not children[n["code"]]:
                        raise ValueError(
                            f"Report template {tid!r}: heading {n['code']!r} of "
                            f"block {key!r} has no child rows. A heading never carries postings, "
                            f"so its row is the sum of its children: list them."
                        )
                    sum_row(r, children[n["code"]])
                else:
                    read_row(r, n["code"], scope["kind"], target)
                    block_rows[key].append(r)
            row += len(lines)
        elif btype == "hierarchy_nodes":
            account = params.get(block["account"])
            if not account:
                raise ValueError(f"Report template {tid!r}: block {key!r} reads "
                                 f"the account in param {block['account']!r}, and none is given.")
            for c in cols:
                if "parts" not in c and "sum_cols" not in c:
                    read_for(c["series"], "node")
            hierarchy = params.get(block["hierarchy"]) or None
            lines = nodes.get(key) or []
            if not lines:
                raise _error(template, "no_rows", values)
            block_rows[key] = []
            for idx, n in enumerate(lines):
                caption(row, render(block["caption"], dict(values, **{"node.label": n["label"]})))
                read_row(row, account, "node",
                         {"entity": EPM_ENTITY_ALL, "hierarchy": hierarchy, "node": n["code"]})
                after = lines[idx + 1] if idx + 1 < len(lines) else None
                if after is None or int(after["depth"]) <= int(n["depth"]):
                    block_rows[key].append(row)  # no child rows follow: a leaf of the list
                row += 1
        elif btype == "heading":
            caption(row, render(block["caption"], values))
            row += 1
        elif btype == "blank":
            row += 1
        elif btype == "total":
            rows = sorted(r for ref in block["of"] for r in block_rows.get(ref, ()))
            runs = []
            for r in rows:
                if runs and r == runs[-1][1] + 1:
                    runs[-1][1] = r
                else:
                    runs.append([r, r])
            caption(row, render(block["caption"], values))
            cells.append({
                "range": _cell_range(first_col, row, last_col, row),
                "formulas": [[
                    "=SUM(" + ",".join(f"{c}{a}:{c}{b}" for a, b in runs) + ")"
                    for c in letters
                ]],
            })
            if block.get("bold"):
                bold_rows.append(row)
            row += 1
        else:
            raise _unsupported(template, f"a {btype!r} row block")

    formats = []
    number_format = template.get("number_format")
    if number_format and row > start_row:
        runs = []  # [first col, last col, format]
        for c in cols:
            fmt = number_format.get(c["series"], number_format.get("default"))
            if fmt is None:
                continue
            if runs and runs[-1][2] == fmt and runs[-1][1] == c["col"] - 1:
                runs[-1][1] = c["col"]
            else:
                runs.append([c["col"], c["col"], fmt])
        formats.extend({"range": _cell_range(a, start_row, b, row - 1), "number_format": fmt}
                       for a, b, fmt in runs)
    formats.extend({"range": _cell_range(1, r, last_col, r), "bold": True} for r in bold_rows)

    out = {"template_id": template["id"], "mode": "formulas"}
    out.update(params)
    out["sheet_name"] = template["sheet_name"]
    out["cells"] = cells
    if formats:
        out["formats"] = formats
    out["report"] = {
        "template_id": template["id"],
        "template_version": template["version"],
        "params": dict(params),
        "area": _area(cells + formats),
    }
    return out


def _scope(template, params):
    """{param, kind, code, hierarchy} of the template's one scope param (see the contract)."""
    found = [p for p in template["params"] if p["type"] in ("entity", "group", "scope")]
    if len(found) != 1:
        raise _unsupported(template, "a template without exactly one entity, group or scope param")
    p = found[0]
    value = params[p["name"]]
    if p["type"] in ("entity", "group"):
        return {"param": p["name"], "kind": p["type"], "code": value, "hierarchy": None}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = None
    if not isinstance(value, dict) or not value.get("code"):
        raise ValueError(f"Report template {template.get('id')!r}: {p['name']} must be "
                         f"{{kind, code, hierarchy?}}, not {params[p['name']]!r}.")
    kind = value.get("kind")
    if kind not in p["kinds"]:
        raise ValueError(f"Report template {template.get('id')!r}: {p['name']} is scope kind "
                         f"{kind!r}, and the template's scope allows {', '.join(p['kinds'])}.")
    return {"param": p["name"], "kind": kind, "code": value["code"],
            "hierarchy": value.get("hierarchy") or None}


def _sum_refs(col, rows):
    """``B4,B7`` / ``B5:B6``: the cells of ``rows`` in column ``col``, adjacent ones as a range."""
    runs = []
    for r in sorted(rows):
        if runs and r == runs[-1][1] + 1:
            runs[-1][1] = r
        else:
            runs.append([r, r])
    return ",".join(f"{col}{a}" if a == b else f"{col}{a}:{col}{b}" for a, b in runs)


def _area(cells):
    cols, rows = [], []
    for cell in cells:
        for ref in cell["range"].split(":"):
            m = _A1.match(ref)
            cols.append(_column_number(m.group(1)))
            rows.append(int(m.group(2)))
    return _cell_range(min(cols), min(rows), max(cols), max(rows))


# ===========================================================================
# SNAPSHOT-mode interpreter (konsol#548 v1, T6)
# ===========================================================================
#
# compile_snapshot(template, params, rows) turns a validated snapshot
# template, its params and the fetched rows into a values-only cell map. Pure:
# the caller fetches ``rows`` (a list of dicts keyed by each field's ``field``).
#
# - Header cells in file order, then (``rows.header_row``) the fields' ``h``
#   on ``rows.start_row``, then one row per input row.
# - A field whose key is absent from a row takes its ``default``, a token
#   string rendered from the params (a lone ``{param}`` keeps the param's
#   type); no default gives None. A key present with None stays None.
# - ``number`` fields: float(value), and 0 for None, an absent key, or
#   anything float() refuses.
# - ``max_rows``: today the SQL LIMIT in api._fetch_trial_balance_rows is the
#   only cap and the builder never refused a longer list, so neither does this.
#   (The template's max_rows feeds that LIMIT through TB_LONG_MAX_ROWS.)
# - Snapshot periods stay period_from/period_to 1..12, as api.build_snapshot
#   validates today. Follow-up (konsol#210): periods should come from the
#   site's Fiscal Year, as formulas templates do.

def _snapshot_number(value):
    if value is None:
        return 0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0


def _snapshot_default(text, params, values):
    m = _PARAM_REF.match(text)
    if m and m.group(1) in params:
        return params[m.group(1)]
    return render(text, values)


def compile_snapshot(template, params, rows):
    """A snapshot template + params + rows -> the cell map (values only).

    Returns {template_id, mode, <every param, in declared order>, sheet_name,
    row_count, cells, report}; ``report`` as for compile_formulas.
    """
    if template.get("mode") != "snapshot":
        raise ValueError(f"Report template {template.get('id')!r} is not a snapshot template.")
    params = resolve_params(template, params)
    values = _token_values(template, params)
    fields = template["fields"]
    last_col = column_letters(len(fields))
    start = template["rows"]["start_row"]

    cells = []
    for cell in template.get("header", []):
        value = params[cell["param"]] if "param" in cell else render(cell["text"], values)
        cells.append({"range": cell["at"], "values": [[value]]})
    first = start
    if template["rows"].get("header_row"):
        cells.append({"range": f"A{start}:{last_col}{start}",
                      "values": [[f["h"] for f in fields]]})
        first = start + 1

    data = []
    for row in rows:
        line = []
        for f in fields:
            name = f["field"]
            if name in row:
                value = row[name]
            elif "default" in f:
                value = _snapshot_default(f["default"], params, values)
            else:
                value = None
            line.append(_snapshot_number(value) if f.get("number") else value)
        data.append(line)
    if data:
        cells.append({"range": f"A{first}:{last_col}{first + len(data) - 1}", "values": data})

    out = {"template_id": template["id"], "mode": "snapshot"}
    out.update(params)
    out["sheet_name"] = template["sheet_name"]
    out["row_count"] = len(data)
    out["cells"] = cells
    out["report"] = {
        "template_id": template["id"],
        "template_version": template["version"],
        "params": dict(params),
        "area": _area(cells),
    }
    return out
