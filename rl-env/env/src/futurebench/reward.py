"""Reward from an episode's measured parts.

R = w_bugs  * bugs_fixed / bugs_available
  + w_tests * min(verified_tests / tests_cap, 1)
  - w_regr  * regressions / bugs_available
  - w_coll  * collisions_per_100_lines

Every part is stored alongside the total, so a change of weights re-scores old episodes with
`Reward.from_parts(parts, weights)` and no test runs.
"""
import re
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Weights:
    bugs: float = 1.0
    tests: float = 0.25
    tests_cap: int = 20
    regressions: float = 1.0
    collisions: float = 0.1
    # how a bug unit with several tests counts: "all" (every test passes), "any", or "fraction"
    bug_credit: str = "all"


@dataclass
class Parts:
    bugs_available: int
    bugs_fixed: float
    verified_tests: int
    regressions: int
    collisions: int
    changed_lines: int
    # extra detail, not used by the formula
    detail: dict = field(default_factory=dict)

    @property
    def collisions_per_100_lines(self) -> float:
        return 100.0 * self.collisions / self.changed_lines if self.changed_lines else 0.0


@dataclass
class Reward:
    total: float
    terms: dict
    parts: Parts
    weights: Weights

    @classmethod
    def from_parts(cls, parts: Parts, weights: Weights = Weights()) -> "Reward":
        n = max(parts.bugs_available, 1)
        terms = {
            "bugs": weights.bugs * parts.bugs_fixed / n,
            "tests": weights.tests * min(parts.verified_tests / weights.tests_cap, 1.0) if weights.tests_cap else 0.0,
            "regressions": -weights.regressions * parts.regressions / n,
            "collisions": -weights.collisions * parts.collisions_per_100_lines,
        }
        return cls(total=round(sum(terms.values()), 6), terms={k: round(v, 6) for k, v in terms.items()},
                   parts=parts, weights=weights)

    def to_dict(self) -> dict:
        return {"total": self.total, "terms": self.terms, "parts": asdict(self.parts), "weights": asdict(self.weights)}

    @classmethod
    def from_dict(cls, d: dict, weights: Weights | None = None) -> "Reward":
        return cls.from_parts(Parts(**d["parts"]), weights or Weights(**d["weights"]))


def test_function(test_id: str) -> str:
    """Strip pytest parametrization: a.b::test_x[1-2] -> a.b::test_x."""
    return re.sub(r"\[.*\]$", "", test_id)


def bug_credit(units: list[dict], passing: set[str], mode: str = "all") -> float:
    """Credit over bug units given the set of target tests that now pass."""
    total = 0.0
    for u in units:
        hit = [t in passing for t in u["tests"]]
        if mode == "all":
            total += all(hit)
        elif mode == "any":
            total += any(hit)
        elif mode == "fraction":
            total += sum(hit) / len(hit)
        else:
            raise ValueError(f"unknown bug_credit mode {mode!r}")
    return total
