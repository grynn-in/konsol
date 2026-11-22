"""pytest writes no cache into the shipped tree.

A pytest run on one file from the repo root used to take ``konsol/setup.py``
(a konsol module) for the project's setup.py and write ``konsol/.pytest_cache``;
its ``CACHEDIR.TAG`` (no suffix) then failed
``test_no_hardcoded_dimensions::test_no_shipped_text_suffix_escapes_the_scan``
on the next run. ``pytest.ini`` at the repo root fixes both at the root: it
is the ini pytest finds first (so the rootdir is the repo root), and its
``addopts`` turns the cache provider off. ``scripts/run-host-tests.py``
passes the same flag itself.
"""
import configparser
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INI = os.path.join(REPO_ROOT, "pytest.ini")
RUNNER = os.path.join(REPO_ROOT, "scripts", "run-host-tests.py")


def test_the_repo_root_ini_turns_the_cache_provider_off():
    assert os.path.exists(INI), "pytest.ini at the repo root: the rootdir anchor and the cache switch"
    parser = configparser.ConfigParser()
    parser.read(INI)
    addopts = parser.get("pytest", "addopts", fallback="").split()
    assert "-p" in addopts and addopts[addopts.index("-p") + 1] == "no:cacheprovider", addopts


def test_the_host_test_runner_passes_the_same_flag():
    with open(RUNNER) as f:
        source = f.read()
    assert '"-p", "no:cacheprovider"' in source


def test_no_cache_directory_sits_in_the_shipped_tree():
    """The symptom itself: a stale cache under konsol/ is a shipped file type
    the dimension scan cannot classify. Move it out (never delete a path you
    did not check), then run with -p no:cacheprovider or from the repo root."""
    found = []
    for dirpath, dirnames, _ in os.walk(os.path.join(REPO_ROOT, "konsol")):
        dirnames[:] = [d for d in dirnames if d not in ("node_modules", ".git")]
        if ".pytest_cache" in dirnames:
            found.append(os.path.relpath(os.path.join(dirpath, ".pytest_cache"), REPO_ROOT))
    assert found == [], found
