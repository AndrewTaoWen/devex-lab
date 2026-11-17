# Five-question check: do the readable graph views help?

**Status: not run yet.** It needs werkzeug recorded with call order (`repomap record`, then `repomap graph update`) and one session from Andrew. This file is the protocol, with an empty results table to fill in.

## The question

Can someone new to werkzeug answer basic questions about it faster and more correctly with the readable Graph views (Overview, Neighborhood, Test replay) than with plain code search?

## The five questions

1. **Where is a URL matched to a rule?** Name the function that does the matching and the one that calls it when a request comes in.
2. **What happens between `Client.get(...)` and the `Response` that comes back?** List the main steps in order, about 5 to 10 functions.
3. **If `Headers.get` changes, what could break?** Name its direct callers, roughly how much code depends on it, and two tests that run it.
4. **Which parts of werkzeug change most often, and which parts does everything rely on but rarely change?** Name three of each.
5. **What does `Range.range_for_length` actually receive in the tests, and which inputs are never tested?**

Before the run, someone who doesn't answer the questions writes reference answers from the source and the trace. They go at the bottom of this file, so they don't steer the answers.

## Conditions

- **Views:** `repomap serve` on werkzeug, with the Graph tab's Overview, Neighborhood and Test replay. The Map tab and source files are allowed too.
- **Search:** source files, `grep`/`rg`, and an editor. No repomap.

## Who answers

- **Andrew** answers questions 1, 3 and 5 with the views, and 2 and 4 with search. Each question is answered in only one condition, since you can't un-learn an answer.
- **An agent** (Claude Code, fresh session per condition, same model for both) answers all five questions twice, in separate sessions. With views, it gets the same data through `repomap graph metrics|landmarks|groups --json`, `/api/replay` and the MCP tools. With search, it gets only the source and shell search tools.

## What we record per answer

- time to answer (stop when the person says "done");
- correctness against the reference: 2 = right and complete, 1 = partly right, 0 = wrong or no answer;
- for the agent: tool calls and tokens.

The views help if correctness is at least as high and time (or tool calls) is lower on most questions.

## Results

| Q | Who | Condition | Time | Correct (0-2) | Notes |
|---|-----|-----------|------|---------------|-------|
| 1 | Andrew | views | | | |
| 2 | Andrew | search | | | |
| 3 | Andrew | views | | | |
| 4 | Andrew | search | | | |
| 5 | Andrew | views | | | |
| 1-5 | agent | views | | | |
| 1-5 | agent | search | | | |

## Reference answers

(Written before the run.)
