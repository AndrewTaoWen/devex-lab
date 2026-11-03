import json

from futurebench import cli
from futurebench.reward import Parts, Reward


def test_list_prints_every_task(capsys):
    cli.main(["list"])
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 12 and any(l.startswith("tinydb ") for l in out)
    cli.main(["list", "--split", "eval"])
    assert len(capsys.readouterr().out.splitlines()) == 4


def test_reweigh_uses_stored_parts(tmp_path, capsys):
    parts = Parts(bugs_available=8, bugs_fixed=3, verified_tests=30, regressions=0, collisions=2, changed_lines=100,
                  detail={"target_tests_fixed": ["a"], "target_tests": 9})
    f = tmp_path / "scores.json"
    f.write_text(json.dumps({"run-1": Reward.from_parts(parts).to_dict()}))
    cli.main(["reweigh", str(f), "--collisions", "0"])
    assert "R=0.625" in capsys.readouterr().out
