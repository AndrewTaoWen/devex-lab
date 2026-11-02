# Gaps A/B: do repomap's untested-value gaps help hardening agents catch future bugs?

Status (2026-11-02): planning. Agents not run yet.

## Plan (agreed so far)

- **Arms.** A = `../04-ab-test/prompt-improved.txt` unchanged. B = the same prompt, plus a `GAPS.txt` file in the workspace from `query.py gaps --public` on a map traced from the snapshot's own tests, and access to `query.py fn/callers`.
- **Repos.** werkzeug and pydantic-settings, the two repos the improved prompt is blind to. 3 runs per arm, 12 agents (Andrew picked the lean option).
- **Metrics.** Primary: distinct future bugs caught per run. Also the union per arm, verified snapshot-failing regression tests, regressions, and whether each caught bug was in a function `GAPS.txt` named.
- **Isolation.** Future data moved out of reach plus a transcript audit, as in round 2. The harness is unchanged.

## Probes

The first gaps view only asked whether a whole argument was ever empty, zero or negative. `repomap_trace.py` now also records, for strings: non-ASCII text and non-ASCII digits, a sign or radix prefix, quotes, control characters, surrounding whitespace, and an empty field between separators (`a,,b`, `user:@host`, `:80`). `query.py gaps` uses each of these only if the map recorded it somewhere, so older maps still give sensible output (`--basic` gives the old view). `--public` drops private and nested functions, and `--limit N` keeps the N most-called.

On werkzeug's snapshot map: 375 flagged functions with the basic view, 260 with `--public`.

## Step 0: ceiling check on werkzeug (before any agents)

Snapshot map: `../../../devtools/cli/werkzeug/snapshot-map.json.gz` (query.py gaps: 375 flagged functions).
Bugs: the 10 distinct future bug commits from rl-env/experiments/04-ab-test (labels + overrides, ab.py logic).
For each commit: functions it changed (git diff at parent, AST), and the gaps the snapshot map reports for them.

| Commit | Bug | Gaps on changed functions | Points at the bug? |
|---|---|---|---|
| 658bf5ed | validate host port | host_is_trusted: trusted_list never empty | no |
| 1b8bc581 | validate base64 in Authorization | none | no |
| 5b0dc6d6 | discard invalid (unquoted) etags | unquote_etag/from_header: value never empty | no (bug is malformed values, not empty) |
| 0c419f95 | `bytes=-0` range | parse_range_header not in map | no |
| b9761b5b | stricter `_plain_int` (0x, hex per base, CRLF) | _plain_int: value never empty; base never zero/negative | no |
| 884437ae | external-URL boolean logic | MapAdapter.build: endpoint/method/url_scheme never empty | no |
| c678a231 | stricter int/float converters (non-ASCII digits, `-` vs fixed_digits) | NumberConverter.to_url: value never zero/negative | partial (negative) |
| 6d7c0b4e | numeric sort of converter matches | _match not traced under that key | no |
| 0e170182 | etag comparison functions | is_resource_modified: args never empty | no |
| 4da3786f | keep empty username/password, port 0 | uri_to_iri/iri_to_uri: uri never empty | near miss (right function, wrong level: the URL is non-empty, a component is) |

Result: 0 clear hits, 1 partial, 1 near miss out of 10.

Why: gaps are per top-level argument and only probe empty/zero/negative. werkzeug's bugs are mostly
spec conformance on non-empty strings (malformed, non-ASCII, signs, prefixes) or an empty component
inside a structured string. Probes that would have named most of them: string character classes seen
(non-ASCII, sign chars, whitespace, quotes, `0x`), and empties inside parsed structures (URL parts, header items).

Other 3 repos (python-dotenv, cattrs, pydantic-settings): not mapped yet; installing and tracing their
snapshots was blocked by the session's permission check.

With the probes already in the old map (non-ASCII, surrounding whitespace), `NumberConverter.to_python: value never non_ascii` turns c678a231 into a hit, so 1 hit, 1 partial and 1 near miss. The rest of the new probes need a fresh trace of the snapshot.

## Files

- `bugs.py`: writes `$S/bugs.json`, the distinct future bug commits per repo (same labelling as `../04-ab-test/ab.py`). Run from the repo root with `S=<dir>`.
- `touch.py REPO CLONE PKG_PREFIX [MAP]`: for each bug commit, the functions it changed and their gaps in the map.
