# Documentation lives at docs.konsolidat.com

Decision (Deepak Pai, 2026-11-05, [konsolidat-docs#2](https://github.com/grynn-in/konsolidat-docs/issues/2)): the Docusaurus site in `grynn-in/konsolidat-docs` is the canonical documentation tree for Konsolidat, konsol included. Reader-facing prose is written there and only there. The former `docs/excel-addin.md` is superseded by the site's [Excel custom functions](https://docs.konsolidat.com/user-guide/excel-custom-functions) and [Excel task pane](https://docs.konsolidat.com/user-guide/excel-taskpane-guide) pages, which were verified against `konsol/public/excel-addin/` on 2026-11-05.

This folder keeps only **engineering records written in the pull-request flow**:

| folder | what |
|---|---|
| `design/` | design notes, hand-offs and PRDs for konsol internals (orchestrator, run-state model, doctype planes) |

`.github/workflows/docs-guard.yml` fails a push or pull request that adds any other markdown under `docs/`. To document something for readers, open a pull request on konsolidat-docs.
