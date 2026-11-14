"""The Konsol Desk navigation: rail, sidebars and pages (Desk IA, 7 Nov 2026).

Decided by Deepak Pai, 7 Nov 2026 (plan "Konsol Desk information
architecture"): the rail is a shipped Dock with Close, Planning, Structure,
Data and Handbook; each rail entry opens one Sidebar and one landing Workspace; each
doctype has one label everywhere. Rejected: Setup instead of Structure,
Integration instead of Data, Ownership Periods under Close, the Handbook as a
link inside Close only.

The rail entry Budget became Planning (Deepak Pai, 7 Nov 2026, option B on
#443; rejected A, separate Budget and Forecast rail entries; C, Forecast
doctypes; D, unfiltered duplicates). Budget and forecast share Budget Cycle
and Budget Annual Input; a forecast is one whose Scenario has
``scenario_type = forecast``. Planning's sidebar lists each of them filtered
to budget, filtered to forecast, and unfiltered under Shared.

This file pins the plan's "Labelling rule":

1. One label map, ``konsol/desk_labels.py``. Every sidebar link and every
   workspace shortcut to a DocType, Report or Page uses exactly its label. A
   filtered "waiting" shortcut takes no suffix: its count badge says
   "N waiting" (Deepak Pai, 7 Nov 2026; rejected: a " waiting" suffix, and
   #439's short nouns as an exception).
2. Each non-child doctype of the Consolidation, EPM and Pipeline modules is
   listed in exactly one shipped Sidebar (allowlist below). A sidebar link is
   filtered only by a pair declared in ``desk_labels.FILTERED_LABELS``, and
   uses that pair's label (#443); every other filtered list lives on a
   workspace.

Frappe v16 mechanics it relies on (frappe/desk/doctype/sidebar/sidebar.py,
frappe/desk/doctype/dock/dock.py, frappe/model/sync.py):
- a shipped Sidebar is ``<module>/sidebar/<scrub(title)>/<scrub(title)>.json``
  and its name is its title; a module with any shipped sidebar gets no
  computed shell, so EPM and Pipeline each ship one;
- an app's Dock is ``<app>/dock/<app>/<app>.json``, named after the app, and
  every row on it needs an icon and a title (Dock.validate_added_rows);
- a Sidebar, Dock or Workspace file re-imports on migrate only when its
  ``modified`` changes.

Every link names its target by string, and nothing in Frappe refuses a dead
one at import: the item renders and fails when clicked. These tests fail
instead.
"""
import ast
import glob
import importlib.util
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: This edit's timestamp. Every Sidebar, Dock and Workspace file it touches
#: carries at least this ``modified``, or migrate does not re-import it.
BUMPED = "2026-11-07 23:30:00"

#: The Planning edit (#443): the dock, the Planning sidebar and workspace.
PLANNING_BUMPED = "2026-11-08 02:00:00"

MODULE_FOLDERS = {"Consolidation": "consolidation", "EPM": "epm", "Pipeline": "pipeline",
                  "Structure": "structure"}

#: Non-child doctypes deliberately on no sidebar, with the reason.
NOT_ON_A_SIDEBAR = {
    # Edited inside its Reporting Hierarchy; a list of members alone means nothing.
    "Reporting Hierarchy Member",
    # Site-written Handbook pages: listed from the Konsol Handbook workspace (plan, Pages table).
    "Konsol Guide Page",
}

#: The rail, top to bottom: (title, link_type, link_to).
DOCK = [
    ("Close", "Sidebar", "Close"),
    ("Planning", "Sidebar", "Planning"),
    ("Structure", "Sidebar", "Structure"),
    # "Data" is a Frappe module and sidebar, so konsol's sidebar is titled
    # Pipeline; the dock row's title is what the rail shows.
    ("Data", "Sidebar", "Pipeline"),
    # Rail title "Info", not "Handbook" (Deepak Pai, 7 Nov 2026); the page stays Konsol Handbook.
    ("Info", "Workspace", "Konsol Handbook"),
]

#: Each sidebar's module and rows, exactly as the plan's sidebar tree.
#: ("Section", label) is a Section Break; the rest are (link_type, link_to),
#: labelled from the map (a Workspace row carries its own label). A link
#: filtered on scenario_type is (link_type, link_to, scenario_type).
SIDEBARS = {
    "Close": ("Consolidation", [
        ("Workspace", "Konsol", "Home"),
        ("Workspace", "My Work", "My Work"),
        ("Workspace", "Konsol Handbook", "Handbook"),
        ("Section", "Trial Balances"),
        ("DocType", "Trial Balance Upload"),
        ("DocType", "Trial Balance Submission"),
        ("DocType", "TB Exception"),
        ("Section", "Rates"),
        ("DocType", "Group Exchange Rate"),
        ("DocType", "Historical Equity Rate"),
        ("Section", "Intercompany"),
        ("DocType", "IC Balance"),
        ("Section", "Adjustments"),
        ("DocType", "Consolidation Journal"),
        ("DocType", "Business Combination"),
        ("DocType", "Business Disposal"),
        ("Section", "Review"),
        ("Report", "Period Close"),
        ("Report", "Close Assertions"),
        ("Report", "Consolidated Statement"),
        ("Report", "Consolidation Worksheet"),
        ("Report", "Statement Drill"),
        ("DocType", "Statement Commentary"),
        ("DocType", "Assertion Run"),
        ("DocType", "Period Status"),
        ("DocType", "Close Event"),
    ]),
    "Planning": ("EPM", [
        ("Workspace", "Planning", "Home"),
        ("Section", "Budget"),
        ("DocType", "Budget Cycle", "budget"),
        ("DocType", "Budget Annual Input", "budget"),
        ("Section", "Forecast"),
        ("DocType", "Budget Cycle", "forecast"),
        ("DocType", "Budget Annual Input", "forecast"),
        ("Section", "Shared"),
        ("DocType", "Scenario"),
        ("DocType", "Spread Profile"),
        ("DocType", "Budget Layer"),
        ("DocType", "Budget Upload"),
        ("DocType", "Budget Cycle"),
        ("DocType", "Budget Entry"),
        ("DocType", "Budget Annual Input"),
    ]),
    "Structure": ("Structure", [
        ("Workspace", "Structure", "Home"),
        ("Section", "Group"),
        ("DocType", "Entity"),
        ("DocType", "Entity Fiscal Calendar"),
        ("DocType", "Consolidation Group"),
        ("DocType", "Ownership Period"),
        ("Section", "Accounts"),
        ("DocType", "Main Account"),
        ("DocType", "Main Account Category"),
        ("DocType", "Cash Flow Category"),
        ("DocType", "Reporting Hierarchy"),
        ("Section", "Dimensions"),
        ("DocType", "Dimension"),
        ("DocType", "Dimension Value"),
        ("DocType", "Dimension Mapping"),
        ("Section", "Intercompany"),
        ("DocType", "Intercompany Account"),
        ("DocType", "IC Elimination Rule"),
        ("Section", "Calendar"),
        ("DocType", "EPM Fiscal Year"),
        ("DocType", "Fiscal Period"),
        ("Section", "Deals Setup"),
        ("DocType", "Fair Value Allocation Profile"),
        ("Section", "Currencies"),
        ("DocType", "ISO Currency"),
        ("Section", "Settings"),
        ("DocType", "Close Settings"),
        ("DocType", "Close Holiday Calendar"),
    ]),
    "Pipeline": ("Pipeline", [
        ("Workspace", "Data", "Home"),
        ("Section", "Sources"),
        ("DocType", "Connector"),
        ("DocType", "Connector Health"),
        ("Section", "Pipelines"),
        ("DocType", "Pipeline"),
        ("DocType", "Pipeline Schedule"),
        ("DocType", "Pipeline Run"),
        ("Section", "Builds"),
        ("DocType", "Build Approval"),
        ("DocType", "Build Scope"),
        ("DocType", "Build Model"),
        ("Section", "Model"),
        ("DocType", "Dataset"),
        ("DocType", "Measure"),
        ("Section", "Settings"),
        ("DocType", "EPM Settings"),
    ]),
}

#: The landing workspace of each rail entry: (folder, name, module).
WORKSPACES = {
    "Konsol": ("consolidation", "Konsol", "Consolidation"),
    "Konsol Handbook": ("consolidation", "Konsol Handbook", "Consolidation"),
    "Planning": ("epm", "Planning", "EPM"),
    "Structure": ("structure", "Structure", "Structure"),
    "Data": ("pipeline", "Data", "Pipeline"),
}


# --- helpers -------------------------------------------------------------------------------

def _scrub(name):
    return name.replace(" ", "_").replace("-", "_").lower()


def _load(rel):
    with open(os.path.join(APP_DIR, rel)) as f:
        return json.load(f)


def _label_module():
    spec = importlib.util.spec_from_file_location(
        "_desk_labels_under_test", os.path.join(APP_DIR, "desk_labels.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _labels():
    return _label_module().LABELS


def _filtered_labels():
    """(doctype, scenario_type) -> label, for the declared filtered links (#443)."""
    return _label_module().FILTERED_LABELS


def _scenario_filter(link_to, raw):
    """The scenario_type a link's ``filters`` (or a shortcut's ``stats_filter``)
    selects, or None. Sidebar Item ``filters`` is a JSON list of
    ``[doctype, field, operator, value]`` (frappe sidebar.py
    ``options_as_filters``; sidebar_item.js ``filters_as_options`` turns it into
    the list's route options)."""
    for flt in json.loads(raw or "[]"):
        if flt[1] == "scenario_type":
            assert flt[0] == link_to and flt[2] == "=" and len(flt) == 4, flt
            return flt[3]
    return None


def _docs(kind, pattern):
    out = {}
    for path in glob.glob(os.path.join(APP_DIR, *pattern.split("/"))):
        with open(path) as f:
            try:
                d = json.load(f)
            except ValueError:
                continue
        if isinstance(d, dict) and d.get("doctype") == kind:
            out[d["name"]] = (os.path.relpath(path, APP_DIR), d)
    return out


def _doctypes():
    return _docs("DocType", "*/doctype/*/*.json")


def _reports():
    return set(_docs("Report", "*/report/*/*.json"))


def _pages():
    return set(_docs("Page", "*/page/*/*.json"))


def _workspaces():
    return _docs("Workspace", "*/workspace/*/*.json")


def _sidebars():
    """Every shipped Sidebar: name -> (path, doc)."""
    return _docs("Sidebar", "*/sidebar/*/*.json")


def _number_cards():
    return _docs("Number Card", "*/number_card/*/*.json")


def _navigated_doctypes():
    """Non-child doctypes of the three navigated modules: name -> module."""
    out = {}
    for name, (path, d) in _doctypes().items():
        if d.get("module") in MODULE_FOLDERS and not d.get("istable"):
            out[name] = d["module"]
    return out


def _links(sidebar):
    return [i for i in sidebar["items"] if i["type"] == "Link"]


# --- the dock --------------------------------------------------------------------------------

DOCK_FILE = os.path.join("dock", "konsol", "konsol.json")


def test_the_dock_lists_the_five_rail_entries_in_order():
    dock = _load(DOCK_FILE)
    assert (dock["doctype"], dock["name"], dock["app"], dock["standard"]) == (
        "Dock", "konsol", "konsol", 1)
    assert not dock.get("user")
    rows = [(i["title"], i["link_type"], i["link_to"]) for i in dock["items"]]
    assert rows == DOCK, rows


def test_every_dock_row_has_an_icon_and_opens_something_shipped():
    """Dock.validate_added_rows refuses a row on the app's dock without an icon
    and a title; a row naming a sidebar or workspace konsol does not ship opens
    nothing."""
    with open(ICON_LIST) as f:
        known = {line.strip() for line in f if line.strip()}
    sidebars, workspaces = _sidebars(), _workspaces()
    for row in _load(DOCK_FILE)["items"]:
        assert row["icon"] in known and row["title"], row
        assert row["added"] == 0 and row["hidden"] == 0, row
        pool = sidebars if row["link_type"] == "Sidebar" else workspaces
        assert row["link_to"] in pool, row


def test_the_handbook_dock_row_resolves_to_a_shell():
    """A Workspace dock row opens in the shell of a sidebar that links it."""
    linked = {i["link_to"] for _, s in _sidebars().values() for i in _links(s)
              if i["link_type"] == "Workspace"}
    assert "Konsol Handbook" in linked


def test_the_dock_was_bumped():
    assert _load(DOCK_FILE)["modified"] >= BUMPED


# --- the sidebars ------------------------------------------------------------------------------

def test_konsol_ships_exactly_the_four_planned_sidebars():
    assert set(_sidebars()) == set(SIDEBARS), sorted(_sidebars())


def test_each_sidebar_lives_where_frappe_looks_for_it():
    """``<module>/sidebar/<scrub(title)>/<scrub(title)>.json``, named by its title."""
    for name, (path, d) in _sidebars().items():
        module, _ = SIDEBARS[name]
        slug = _scrub(d["title"])
        assert path == os.path.join(MODULE_FOLDERS[module], "sidebar", slug, slug + ".json"), path
        assert (d["name"], d["title"], d["module"], d["app"], d["standard"]) == (
            name, name, module, "konsol", 1), path
        assert d["modified"] >= BUMPED, (path, d["modified"])


def test_every_navigated_module_ships_a_sidebar():
    """A module with no shipped sidebar gets a computed one, an alphabetical
    list of its doctypes, as its own rail entry (sidebar.py get_sidebar_bases)."""
    owners = {d["module"] for _, d in _sidebars().values()}
    assert owners == set(MODULE_FOLDERS), owners


def test_each_sidebar_has_the_planned_rows():
    labels = _labels()
    for name, (module, rows) in SIDEBARS.items():
        _, d = _sidebars()[name]
        got = []
        for i in d["items"]:
            if i["type"] == "Section Break":
                got.append(("Section", i["label"]))
            elif i["link_type"] == "Workspace":
                got.append((i["link_type"], i["link_to"], i["label"]))
            elif i.get("filters"):
                got.append((i["link_type"], i["link_to"], _scenario_filter(i["link_to"], i["filters"])))
            else:
                got.append((i["link_type"], i["link_to"]))
        assert got == rows, name


def test_every_sidebar_link_resolves():
    doctypes, reports, pages, workspaces = _doctypes(), _reports(), _pages(), _workspaces()
    pools = {"DocType": doctypes, "Report": reports, "Page": pages, "Workspace": workspaces}
    for name, (_, d) in _sidebars().items():
        for item in _links(d):
            assert item["link_type"] in pools, (name, item["link_type"])
            assert item["link_to"] in pools[item["link_type"]], (name, item["link_to"])


def test_every_sidebar_link_uses_the_label_map():
    """An unfiltered link takes LABELS; a filtered one its declared pair's label."""
    labels, filtered = _labels(), _filtered_labels()
    for name, (_, d) in _sidebars().items():
        for item in _links(d):
            if item["link_type"] == "Workspace":
                continue
            if item.get("filters"):
                key = (item["link_to"], _scenario_filter(item["link_to"], item["filters"]))
                assert key in filtered, (name, key, "not in desk_labels.FILTERED_LABELS")
                assert item["label"] == filtered[key], (name, item["label"], filtered[key])
                continue
            assert item["link_to"] in labels, (name, item["link_to"], "not in desk_labels.LABELS")
            assert item["label"] == labels[item["link_to"]], (name, item["label"])


def test_each_navigated_doctype_is_on_exactly_one_sidebar():
    seen = {}
    for name, (_, d) in _sidebars().items():
        for item in _links(d):
            if item["link_type"] == "DocType" and name not in seen.get(item["link_to"], []):
                # Planning lists a doctype up to three times: still one sidebar.
                seen.setdefault(item["link_to"], []).append(name)
    navigated = _navigated_doctypes()
    missing = sorted(set(navigated) - set(seen) - NOT_ON_A_SIDEBAR)
    assert missing == [], f"on no sidebar: {missing}"
    twice = {dt: where for dt, where in seen.items() if len(where) > 1}
    assert twice == {}, f"on more than one sidebar: {twice}"
    allowlisted = sorted(NOT_ON_A_SIDEBAR & set(seen))
    assert allowlisted == [], f"allowlisted yet listed: {allowlisted}"
    assert NOT_ON_A_SIDEBAR <= set(navigated), "the allowlist names a doctype that is gone"


def test_only_declared_pairs_are_filtered_and_each_link_claims_its_doctype():
    """A sidebar link is filtered only by a pair declared in
    ``desk_labels.FILTERED_LABELS`` (Deepak Pai, 7 Nov 2026, option B on #443;
    rejected A, C, D), on scenario_type alone, and each declared pair is one
    link; any other filtered list belongs on a workspace. ``is_default_module``
    is what routes a doctype's pages to its sidebar (frappe/boot.py
    build_entity_module_map); Planning's links all claim theirs for Planning."""
    declared = _filtered_labels()
    seen = []
    for name, (_, d) in _sidebars().items():
        for item in _links(d):
            if item.get("filters"):
                flts = json.loads(item["filters"])
                assert len(flts) == 1, (name, item["label"], flts)
                key = (item["link_to"], _scenario_filter(item["link_to"], item["filters"]))
                assert key in declared, (name, item["label"], key)
                seen.append(key)
            if item["link_type"] == "DocType":
                assert item["is_default_module"] == 1, (name, item["link_to"])
    assert sorted(seen) == sorted(declared), (seen, sorted(declared))


#: Budget and forecast share these doctypes (#443).
PLANNING_DOCTYPES = ("Budget Cycle", "Budget Annual Input")


def test_the_planning_doctypes_are_all_on_planning():
    on = {i["link_to"] for i in _links(_sidebars()["Planning"][1]) if i["link_type"] == "DocType"}
    assert set(PLANNING_DOCTYPES) <= on, on


def test_the_unfiltered_planning_links_say_all():
    """Unfiltered, a planning list holds every scenario type, so its label says
    so: "All Cycles", not "Budget Cycles" (Deepak Pai, 7 Nov 2026, option B on
    #443; rejected A, C, D)."""
    labels = _labels()
    assert [labels[dt] for dt in PLANNING_DOCTYPES] == [
        "All Cycles", "All Annual Inputs"]


def test_no_sidebar_shows_one_label_twice():
    """Two rows labelled alike on one sidebar cannot be told apart."""
    for name, (_, d) in _sidebars().items():
        labels = [i["label"] for i in _links(d)]
        assert len(labels) == len(set(labels)), (name, sorted(l for l in labels if labels.count(l) > 1))


def test_every_scenario_type_but_actual_is_reachable():
    """Each non-actual Scenario type reaches each planning doctype from some
    link: its own filtered link, or the unfiltered "All ..." link. Strategic and
    operational cycles have no section of their own, so without the "All" link
    they would be listed nowhere (the HMD on #443). ``actual`` is refused by
    BudgetCycle.validate, so no link filters on it."""
    scenario = _doctypes()["Scenario"][1]
    field = next(f for f in scenario["fields"] if f["fieldname"] == "scenario_type")
    types = [t for t in field["options"].split("\n") if t]
    assert "actual" in types and len(types) > 3, types
    filtered, unfiltered = set(), {}
    for _, (_, d) in _sidebars().items():
        for i in _links(d):
            if i["link_type"] != "DocType" or i["link_to"] not in PLANNING_DOCTYPES:
                continue
            if i.get("filters"):
                filtered.add((i["link_to"], _scenario_filter(i["link_to"], i["filters"])))
            else:
                unfiltered[i["link_to"]] = i["label"]
    assert not {k for k in filtered if k[1] == "actual"}, filtered
    for dt in PLANNING_DOCTYPES:
        for t in types:
            if t == "actual":
                continue
            reach = (dt, t) in filtered or unfiltered.get(dt, "").startswith("All ")
            assert reach, (dt, t, "reachable from no link")


def test_every_sidebar_starts_at_its_home():
    for name, (_, d) in _sidebars().items():
        first = d["items"][0]
        assert (first["type"], first["link_type"], first["label"]) == (
            "Link", "Workspace", "Home"), name


def test_close_lists_my_work_then_the_handbook_under_home():
    """My Work is the second Close row, not a rail entry (plan); the Handbook
    row stays so the Handbook dock row resolves a shell."""
    items = _sidebars()["Close"][1]["items"]
    assert [(i["label"], i["link_to"], i["child"]) for i in items[1:3]] == [
        ("My Work", "My Work", 0), ("Handbook", "Konsol Handbook", 0)]


def test_the_old_konsol_sidebar_is_gone():
    assert not os.path.exists(os.path.join(APP_DIR, "consolidation", "sidebar", "konsol"))


def test_the_old_budget_sidebar_and_page_are_gone():
    """Planning replaced them (#443); the patch delete_budget_rail removes a site's copies."""
    assert not os.path.exists(os.path.join(APP_DIR, "epm", "sidebar", "budget"))
    assert not os.path.exists(os.path.join(APP_DIR, "epm", "workspace", "budget"))


def test_the_planning_sections_start_closed():
    """Every section collapsed by default (Deepak Pai, 7 Nov 2026)."""
    for i in _sidebars()["Planning"][1]["items"]:
        if i["type"] == "Section Break":
            assert (i["collapsible"], i["keep_closed"]) == (1, 1), i["label"]


def test_the_planning_edit_was_bumped():
    assert _load(DOCK_FILE)["modified"] >= PLANNING_BUMPED
    assert _sidebars()["Planning"][1]["modified"] >= PLANNING_BUMPED
    assert _workspaces()["Planning"][1]["modified"] >= PLANNING_BUMPED


ICON_LIST = os.path.join(APP_DIR, "tests", "data", "frappe_v16_lucide_icons.txt")


def test_every_sidebar_icon_exists_in_frappe_v16():
    """A sidebar icon Frappe does not ship renders as a blank (Deepak Pai, 5 Nov
    2026: "some menu items on the left doesnt have icons"). Lucide renamed
    bar-chart-3 to chart-column and check-circle to circle-check.

    The list is the v16 image's frappe/public/icons/lucide.svg symbol ids:
    grep -o 'id="icon-[a-z0-9-]*"' lucide.svg | sed 's/id="icon-//;s/"//' | sort -u
    Regenerate it when the Frappe image moves."""
    with open(ICON_LIST) as f:
        known = {line.strip() for line in f if line.strip()}
    assert len(known) > 1000, "icon list looks truncated"
    for name, (_, d) in _sidebars().items():
        missing = [(i["label"], i.get("icon")) for i in _links(d) if i.get("icon") not in known]
        assert missing == [], (name, missing)


def test_sidebar_positions_follow_the_file_order():
    """Two rows with the same idx are ordered arbitrarily: Builds showed before
    IC balances on demo.konsolidat.com (5 Nov 2026) because both were 11."""
    for name, (_, d) in _sidebars().items():
        idx = [i["idx"] for i in d["items"]]
        assert idx == list(range(1, len(idx) + 1)), (name, idx)


def test_rows_under_a_section_are_its_children():
    for name, (_, d) in _sidebars().items():
        in_section = False
        for i in d["items"]:
            if i["type"] == "Section Break":
                in_section = True
                assert (i["child"], i["collapsible"]) == (0, 1), (name, i["label"])
                continue
            assert i["child"] == (1 if in_section else 0), (name, i["label"])


# --- the workspaces ---------------------------------------------------------------------------

def _ws(name):
    folder, ws_name, _ = WORKSPACES[name]
    slug = _scrub(ws_name)
    return _load(os.path.join(folder, "workspace", slug, slug + ".json"))


def test_each_rail_entry_has_its_landing_workspace():
    for name, (folder, ws_name, module) in WORKSPACES.items():
        ws = _ws(name)
        assert (ws["doctype"], ws["name"], ws["label"], ws["title"], ws["module"]) == (
            "Workspace", ws_name, ws_name, ws_name, module), name
        assert ws["public"] == 1 and ws["standard"] == 1 and not ws["for_user"], name
        assert ws["modified"] >= BUMPED, (name, ws["modified"])


def test_every_navigation_module_is_declared_in_modules_txt():
    """migrate imports only the modules modules.txt declares (model/sync.py
    sync_for), so a module dropped from it would ship a sidebar nobody imports
    (re-review of #451)."""
    with open(os.path.join(APP_DIR, "modules.txt")) as f:
        declared = {line.strip() for line in f if line.strip()}
    assert declared == set(MODULE_FOLDERS), (declared, set(MODULE_FOLDERS))
    for module, folder in MODULE_FOLDERS.items():
        assert folder == _scrub(module), (module, folder)


def test_each_workspace_module_has_exactly_one_sidebar():
    """A sidebar lists every workspace of its module (sidebar.py
    get_sidebar_bases: ``workspaces = context.workspaces.get(base.module)``), and
    the desk opens a workspace in the first sidebar that lists it
    (sidebar.js module_for_workspace, sidebars ordered by name). With Close and
    Structure both in Consolidation, the Structure page opened under the Close
    sidebar (review of #451). So Structure is a module of its own, holding only
    its sidebar and page, and no module may hold two konsol sidebars."""
    per_module = {}
    for _, d in _sidebars().values():
        per_module.setdefault(d["module"], []).append(d["name"])
    for name, (path, ws) in _workspaces().items():
        assert len(per_module.get(ws["module"], [])) == 1, (path, ws["module"], per_module.get(ws["module"]))


def test_every_workspace_shortcut_uses_the_label_map():
    """A shortcut counting one scenario type takes that pair's label (#443)."""
    labels, filtered = _labels(), _filtered_labels()
    for name, (path, ws) in _workspaces().items():
        for s in ws["shortcuts"]:
            if s["type"] not in ("DocType", "Report", "Page"):
                continue
            stype = _scenario_filter(s["link_to"], s.get("stats_filter"))
            if stype:
                assert (s["link_to"], stype) in filtered, (path, s["link_to"], stype)
                assert s["label"] == filtered[(s["link_to"], stype)], (path, s["label"])
                continue
            assert s["link_to"] in labels, (path, s["link_to"], "not in desk_labels.LABELS")
            assert s["label"] == labels[s["link_to"]], (path, s["label"], labels[s["link_to"]])


def test_every_workspace_shortcut_and_card_resolves():
    doctypes, reports, pages, cards = _doctypes(), _reports(), _pages(), _number_cards()
    for name, (path, ws) in _workspaces().items():
        for s in ws["shortcuts"]:
            pool = {"DocType": doctypes, "Page": pages, "Report": reports}[s["type"]]
            assert s["link_to"] in pool, (path, s["type"], s["link_to"])
            if s.get("stats_filter"):
                for flt in json.loads(s["stats_filter"]):
                    # Four elements, never five (frappe.utils.cleanup_filters).
                    assert flt[0] == s["link_to"] and len(flt) == 4, (path, flt)
        blocks = json.loads(ws["content"])
        shortcut_names = [s["label"] for s in ws["shortcuts"]]
        assert len(shortcut_names) == len(set(shortcut_names)), path
        card_names = {c["number_card_name"] for c in ws["number_cards"]}
        for b in blocks:
            if b["type"] == "shortcut":
                assert b["data"]["shortcut_name"] in shortcut_names, (path, b)
            if b["type"] == "number_card":
                assert b["data"]["number_card_name"] in card_names, (path, b)
        on_page = {b["data"].get("shortcut_name") for b in blocks if b["type"] == "shortcut"}
        # #497 row 11: the home page body is the Konsol Home Tabs block, which draws
        # the shortcut rows from the child table (test_konsol_home_look pins the names)
        tabs_only = [b["data"].get("custom_block_name") for b in blocks] == ["Konsol Home Tabs"]
        assert tabs_only or set(shortcut_names) <= on_page, (path, sorted(set(shortcut_names) - on_page))
        for card_name in card_names:
            assert card_name in cards, (path, card_name)
            _, card = cards[card_name]
            assert card["is_standard"] == 1 and card["document_type"] in doctypes, card_name
            for flt in json.loads(card.get("filters_json") or "[]"):
                assert flt[0] == card["document_type"], flt
        ids = [b["id"] for b in blocks]
        assert len(ids) == len(set(ids)), path


def test_setup_moved_from_the_close_home_to_structure():
    """Plan: the Konsol Setup and Entities shortcuts move to Structure, whose
    page leads with Konsol Setup."""
    home = {s["link_to"] for s in _ws("Konsol")["shortcuts"]}
    assert "konsol-setup" not in home and "Entity" not in home
    structure = _ws("Structure")
    first = next(b for b in json.loads(structure["content"]) if b["type"] == "shortcut")
    assert first["data"]["shortcut_name"] == "Konsol Setup"
    targets = {s["link_to"] for s in structure["shortcuts"]}
    assert {"konsol-setup", "Entity", "Consolidation Group", "Main Account", "Dimension",
            "EPM Fiscal Year"} <= targets


def test_the_handbook_lists_the_site_guide_pages():
    ws = _ws("Konsol Handbook")
    assert ("DocType", "Konsol Guide Page") in {(s["type"], s["link_to"]) for s in ws["shortcuts"]}


def test_planning_and_data_pages_show_their_counts():
    """Planning counts open budget cycles and open forecast cycles apart, and
    links Scenarios (#443). Open is a draft Cycle (docstatus 0: Draft or
    Pending Approval); the Cycle has no status field since S2-C W2a."""
    planning = _ws("Planning")["shortcuts"]
    counted = sorted(
        (s["link_to"], tuple(tuple(f[1:]) for f in json.loads(s["stats_filter"])), s.get("format"))
        for s in planning if s.get("stats_filter"))
    assert counted == [
        ("Budget Cycle", (("scenario_type", "=", "budget"), ("docstatus", "=", 0)), "{} open"),
        ("Budget Cycle", (("scenario_type", "=", "forecast"), ("docstatus", "=", 0)), "{} open"),
    ], counted
    assert "Scenario" in {s["link_to"] for s in planning}
    data = _ws("Data")
    assert {"Pipeline Run", "Build Approval", "Connector", "Connector Health"} <= {
        s["link_to"] for s in data["shortcuts"]}
    assert "Warehouse Freshness" in {c["number_card_name"] for c in data["number_cards"]}


# --- the desktop tile -----------------------------------------------------------------------

def test_desktop_tile_opens_the_close_home():
    with open(os.path.join(APP_DIR, "hooks.py")) as f:
        tree = ast.parse(f.read())
    screen = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                  and getattr(n.targets[0], "id", None) == "add_to_apps_screen")
    assert screen[0]["route"] == "/desk/" + _ws("Konsol")["name"].lower()
