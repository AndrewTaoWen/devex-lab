"""Coupling groups: functions that call each other a lot, found by greedy modularity merging (Clauset, Newman and
Moore, 2004) over the call arrows impact uses. Deterministic: the same graph always gives the same groups.

Functions with no calls in or out stay with their own file, so every function lands in exactly one group.
"""
from collections import defaultdict

from .graphmetrics import call_maps, units


def weights(db):
    """Undirected arrow weights between units: one per call arrow in either direction."""
    _, callees = call_maps(db)
    known = units(db)
    out = defaultdict(float)
    for src, dsts in callees.items():
        for dst in dsts:
            if src in known and dst in known:
                out[tuple(sorted((src, dst)))] += 1.0
    return dict(out)


def merge(edges):
    """Greedy modularity merging. edges: {(a, b): weight}. Returns {node: community}, community = smallest member."""
    total = sum(edges.values())
    if not total:
        return {}
    e, a = defaultdict(dict), defaultdict(float)
    for (x, y), w in edges.items():
        share = w / (2 * total)
        e[x][y] = e[x].get(y, 0) + share
        e[y][x] = e[y].get(x, 0) + share
        a[x] += share
        a[y] += share
    members = {node: [node] for node in a}
    while True:
        best = None
        for i in sorted(e):
            for j in sorted(e[i]):
                if i < j:
                    gain = 2 * (e[i][j] - a[i] * a[j])
                    if gain > 1e-12 and (best is None or gain > best[0] + 1e-12):
                        best = (gain, i, j)
        if best is None:
            break
        _, i, j = best  # fold j into i (i < j keeps the smallest member as the name)
        for k, share in e.pop(j).items():
            e[k].pop(j, None)
            if k != i:
                e[i][k] = e[i].get(k, 0) + share
                e[k][i] = e[k].get(i, 0) + share
        e[i].pop(i, None)
        a[i] += a.pop(j)
        members[i] += members.pop(j)
    return {node: community for community, nodes in members.items() for node in nodes}


def groups(db, metrics_rows=None):
    """[{id, name, members}] covering every unit, plus {key: id}. name = the member with the most reach (or the
    file, for a group of uncalled code)."""
    reach = {row["key"]: row["reach"] for row in metrics_rows or []}
    community = merge(weights(db))
    buckets = defaultdict(list)
    for key in sorted(units(db)):
        buckets[community.get(key, "file:" + key.split(":", 1)[0])].append(key)
    out = []
    for number, (label, keys) in enumerate(sorted(buckets.items(), key=lambda item: (-len(item[1]), item[0]))):
        lead = max(keys, key=lambda key: (reach.get(key, 0), -len(key), key)) if not label.startswith("file:") \
            else label[5:]
        out.append(dict(id=number, name=lead, size=len(keys), members=keys))
    return out, {key: group["id"] for group in out for key in group["members"]}
