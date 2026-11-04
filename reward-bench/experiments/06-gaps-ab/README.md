# Gaps A/B: do repomap's untested-value gaps help hardening agents catch future bugs?

Status (2026-11-03): stopped before any agent ran. Neither repomap signal points at the real future bugs much better than chance, so Andrew decided not to spend the 12 agents and to record this as a negative result.

## Short answer so far

Before spending agents, we checked whether repomap's signals even point at the 16 real future bugs on werkzeug and pydantic-settings:

- **Value gaps, basic probes** (never empty / zero / negative): 0 clear hits on werkzeug's 10 bugs, 1 partial, 1 near miss.
- **Value gaps, richer string probes:** they flag 75 to 98% of all public string arguments, so a hit means little. Two werkzeug bugs gain a fitting flag (`NumberConverter.to_python: value never non_ascii_digit`, `_plain_int: value never control_char`).
- **Never-run lines:** on werkzeug, 4 of 10 bug fixes touch a statement no snapshot test runs, against a base rate of 26% of statements. On pydantic-settings, 0 of 6, against a base rate of 6%. Every pydantic-settings bug sits in code the tests already run.

The bugs mostly live in code the tests do run, with inputs nobody thought to try that are specific to the domain (a port in a host, `-0` in a range, a UTF-8 file read with the locale encoding). repomap records what tests did, not what they should have done, so it can't name those inputs.

## What this says

- **repomap as built can't tell a hardening agent where the future bugs are.** Its gaps are close to universal once the probes are rich enough to matter, and its never-run lines miss most bugs, all of them on pydantic-settings.
- **This doesn't rule repomap out as an agent tool.** It still tells an agent what a change touches and which tests to run (`../../../devtools/cli/README.md`). It just isn't a bug finder.
- **Where the signal would have to come from:** inputs specific to the domain and the spec (header grammar, URL parts, file encodings). That is the "domain rules" kind that `../03-analysis/` found agents weakest on. A spec-driven probe list, or the improved prompt's spec-conformance step, fits this better than tracing.
- **If someone revisits this,** the cheapest next test is the third option we didn't take: give arm B the inputs the tests actually used per function (`query.py fn`), so the agent sees what was tried rather than a list of what wasn't.

## Plan (as agreed before stopping)

- **Arms.** A = `../04-ab-test/prompt-improved.txt` unchanged. B = the same prompt, plus a `GAPS.txt` file in the workspace from `query.py gaps --public` on a map traced from the snapshot's own tests, and access to `query.py fn/callers`.
- **Repos.** werkzeug and pydantic-settings, the two repos the improved prompt is blind to. 3 runs per arm, 12 agents (Andrew picked the lean option).
- **Metrics.** Primary: distinct future bugs caught per run. Also the union per arm, verified snapshot-failing regression tests, regressions, and whether each caught bug was in a function `GAPS.txt` named.
- **Isolation.** Future data moved out of reach plus a transcript audit, as in round 2. The harness is unchanged.

## Probes

The first gaps view only asked whether a whole argument was ever empty, zero or negative. `repomap_trace.py` now also records, for strings: non-ASCII text and non-ASCII digits, a sign or radix prefix, quotes, control characters, surrounding whitespace, and an empty field between separators (`a,,b`, `user:@host`, `:80`). `query.py gaps` uses each of these only if the map recorded it somewhere, so older maps still give sensible output (`--basic` gives the old view). `--public` drops private and nested functions, and `--limit N` keeps the N most-called.

On werkzeug's snapshot map: 375 flagged functions with the basic view, 260 with `--public`.

## Step 0a: value gaps on werkzeug (old map, basic probes)

Snapshot map: `../../../devtools/cli/werkzeug/snapshot-map.json.gz` (query.py gaps: 375 flagged functions).
Bugs: the 10 distinct future bug commits from reward-bench/experiments/04-ab-test (labels + overrides, ab.py logic).
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

## Step 0b: richer probes and never-run lines (fresh snapshot traces)

Both snapshots were re-traced with the new probes (werkzeug: 988 tests, watchdog reloader tests skipped because they hang in this container; pydantic-settings: 549 tests, `test_docs.py` skipped, the same 7 CLI failures as in 04 from the newer pydantic).

Share of public string arguments the tests never fed each kind of value:

| Probe | werkzeug (276 args) | pydantic-settings (51 args) |
|---|---|---|
| sign prefix | 97% | 92% |
| non-ASCII digit | 97% | n/a (never seen anywhere) |
| control character | 96% | n/a |
| surrounding whitespace | 94% | n/a |
| quote | 88% | 94% |
| non-ASCII | 87% | 98% |
| empty | 80% | 76% |
| empty field | 75% | 80% |

Statements each bug fix edits, mapped back to the snapshot (`coverage_check.py`):

| Repo | Commit | Bug | Run by tests | Never run | Added after snapshot |
|---|---|---|---|---|---|
| werkzeug | 658bf5ed | validate host port | 0 | 0 | 2 |
| werkzeug | 1b8bc581 | validate base64 | 1 | 0 | 0 |
| werkzeug | 5b0dc6d6 | invalid etags | 4 | 0 | 6 |
| werkzeug | 0c419f95 | `bytes=-0` | 0 | 0 | 1 |
| werkzeug | b9761b5b | stricter `_plain_int` | 2 | **2** | 0 |
| werkzeug | 884437ae | external-URL logic | 0 | **1** | 0 |
| werkzeug | c678a231 | stricter converters | 11 | **7** | 1 |
| werkzeug | 6d7c0b4e | numeric converter sort | 1 | 0 | 0 |
| werkzeug | 0e170182 | etag comparison | 3 | 0 | 3 |
| werkzeug | 4da3786f | empty username, port 0 | 5 | **1** | 0 |
| pydantic-settings | 0e8d7d4 | empty JSON config file | 1 | 0 | 0 |
| pydantic-settings | 12cdb0a | JSON/YAML read as UTF-8 | 3 | 0 | 0 |
| pydantic-settings | 081d39f | config/CLI values ignored | 12 | 0 | 1 |
| pydantic-settings | d2fdeda | secret files read as UTF-8 | 1 | 0 | 1 |
| pydantic-settings | e1e0ece | nested secrets follow symlinks | 2 | 0 | 0 |
| pydantic-settings | bf443bd | empty yaml_config_section | 1 | 0 | 0 |

Base rate of never-run statements inside package functions: werkzeug 26%, pydantic-settings 6%.

## Files

- `bugs.py`: writes `$S/bugs.json`, the distinct future bug commits per repo (same labelling as `../04-ab-test/ab.py`). Run from the repo root with `S=<dir>`.
- `coverage_check.py REPO CLONE PKG_PREFIX MAP SNAPSHOT`: step 0b, bug-fix statements run or never run at the snapshot, plus the base rate.
- `touch.py REPO CLONE PKG_PREFIX [MAP]`: for each bug commit, the functions it changed and their gaps in the map.
