# repomap VS Code extension

**Status: planned.**

Put the map in the editor so impact is visible while typing, not only after a command.

## Features, in build order

1. **Gutter counts.** "N tests run this line" from the memory; lines no test reaches are marked.
2. **Hover.** Value summary for the function under the cursor (types seen, empty / zero / negative / None) and its gaps.
3. **Impact panel.** The `serve` page's Map and Graph views for the current unsaved diff, updated on save.
4. **Run affected tests.** A command and a status-bar button that run the selected tests through the Python test explorer.
5. **Refresh on save** (optional), using `repomap.py refresh` in the background.

## Plan

- TypeScript extension that shells out to the packaged CLI with `--json`; no reimplementation of the tracer.
- Reuse `repomap_ui.html` as the webview for the impact panel.
- Watch `.repomap/memory.json` and re-read on change, as `serve` does.
