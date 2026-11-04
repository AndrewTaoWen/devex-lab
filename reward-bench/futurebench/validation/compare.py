"""Compare futurebench rescores with the old harness's scores.

usage: python compare.py RESCORE_DIR EXPERIMENTS_DIR > table.md
RESCORE_DIR holds <set>--<repo>.json files written by `futurebench rescore --out`.
"""
import json
import sys
from pathlib import Path

rescore, exp = Path(sys.argv[1]), Path(sys.argv[2])
SETS = {"02-scale-up_patches": ("02-scale-up", exp / "02-scale-up/scores.json"),
        "02-scale-up_sandboxed_patches": ("02 sandboxed", exp / "02-scale-up/sandboxed/scores.json"),
        "04-ab-test_patches": ("04-ab-test", exp / "04-ab-test/scores.json")}
ab = json.loads((exp / "04-ab-test/results.json").read_text())
rows, same_fixed, same_regr, n = [], 0, 0, 0
for f in sorted(rescore.glob("*--*.json")):
    set_key, repo = f.stem.split("--")
    label, old_path = SETS[set_key]
    old = json.loads(old_path.read_text())
    for run, d in sorted(json.loads(f.read_text()).items()):
        o, p = old[run], d["parts"]
        det = p["detail"]
        fixed_new = set(det["target_tests_fixed"])
        regr_new = len(det["regressed_future_tests"])
        n += 1
        same_fixed += fixed_new == set(o["fixed_tests"])
        same_regr += regr_new == o["regressions"]
        old_units = len(ab[run]["bug_commits"]) if label == "04-ab-test" else ""
        rows.append(f"| {label} | {run} | {o['fixed']} | {len(fixed_new)} | {'yes' if fixed_new == set(o['fixed_tests']) else 'no'} "
                    f"| {o['regressions']} | {regr_new} | {old_units} | {det['units']['bug']['any']:g} | {p['bugs_fixed']:g}/{p['bugs_available']} "
                    f"| {p['verified_tests']} | {p['regressions']} | {p['collisions']} | {d['total']:.3f} |")
print(f"{n} patches; same fixed target tests as the old harness: {same_fixed}/{n}; same future-regression count: {same_regr}/{n}\n")
print("| Set | Run | Old fixed tests | New fixed tests | Same set | Old future regressions | New | Old bug units (any) | New bug units (any) | New bugs (all) | Verified tests | Regressions (functions) | Collisions | R |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
print("\n".join(rows))
