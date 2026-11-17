"""What the readable Graph tab draws: a stable folder-tree layout with one cell per function, and each cell's
numbers, landmark flag and coupling group.

Positions come from the folder tree alone (folders, then files, then functions in source order), so a cell stays
put while the code inside it changes. Only adding or removing functions moves things.
"""
from . import coupling, graph, graphmetrics, landmarks

W = 1000
FOLDER_HEAD, FILE_HEAD, PAD = 18, 13, 3


def height(n_cells):
    return 420 + min(680, n_cells)


def squarify(items, x, y, w, h):
    """items: [(weight, name)] -> {name: (x, y, w, h)}. Heaviest first, ties by name, so the result is stable."""
    out = {}
    items = sorted(items, key=lambda item: (-item[0], item[1]))
    total = sum(weight for weight, _ in items)
    if not items or w <= 0 or h <= 0:
        return out
    scale = w * h / total
    items = [(weight * scale, name) for weight, name in items]

    def worst(row, side):
        area = sum(r for r, _ in row)
        return max(max(side * side * r / (area * area), (area * area) / (side * side * r)) for r, _ in row)

    while items:
        side = min(w, h)
        row = [items.pop(0)]
        while items and worst(row + [items[0]], side) <= worst(row, side):
            row.append(items.pop(0))
        area = sum(r for r, _ in row)
        if w >= h:
            col = area / h
            cy = y
            for r, name in row:
                out[name] = (x, cy, col, r / col)
                cy += r / col
            x, w = x + col, w - col
        else:
            band = area / w
            cx = x
            for r, name in row:
                out[name] = (cx, y, r / band, band)
                cx += r / band
            y, h = y + band, h - band
    return out


def tree(keys):
    """Nested dict of folders -> files -> [keys in source order]."""
    root = {}
    for key in keys:
        parts = graph.file_of(key).split("/")
        node = root
        for part in parts[:-1]:
            node = node.setdefault(part + "/", {})
        node.setdefault(parts[-1], []).append(key)
    return root


def weight(node):
    return len(node) + 2 if isinstance(node, list) else sum(weight(child) for child in node.values()) + 2


def layout(keys, order):
    """keys: unit keys; order: key -> sort position inside its file. Returns (cells, folders, files, W, H)."""
    H = height(len(keys))
    cells, folders, files = {}, [], []

    def place(node, path, x, y, w, h, depth):
        if isinstance(node, list):  # a file: its functions in a grid, in source order
            files.append(dict(name=path, x=x, y=y, w=w, h=h))
            ix, iy, iw, ih = x + PAD, y + FILE_HEAD, w - 2 * PAD, h - FILE_HEAD - PAD
            node = sorted(node, key=lambda key: (order.get(key, 0), key))
            n = len(node)
            cols = max(1, round((n * max(iw, 1) / max(ih, 1)) ** 0.5))
            rows = -(-n // cols)
            cw, ch = iw / cols, ih / rows
            for i, key in enumerate(node):
                r, c = divmod(i, cols)
                cells[key] = [round(ix + c * cw, 2), round(iy + r * ch, 2), round(cw, 2), round(ch, 2)]
            return
        if path:
            folders.append(dict(name=path, x=x, y=y, w=w, h=h, depth=depth))
            x, y, w, h = x + PAD, y + FOLDER_HEAD, w - 2 * PAD, h - FOLDER_HEAD - PAD
        spots = squarify([(weight(child), name) for name, child in node.items()], x, y, w, h)
        for name, (cx, cy, cw, ch) in spots.items():
            place(node[name], path + name, cx + 1, cy + 1, cw - 2, ch - 2, depth + 1)

    place(tree(keys), "", 0, 0, W, H, 0)
    return cells, folders, files, W, H


def payload(db, memory, since=graphmetrics.DEFAULT_SINCE, cwd="."):
    """Everything the readable views need, keyed by graph key (the page maps keys to its own cells)."""
    res = graphmetrics.metrics(db, memory, since=since, cwd=cwd)
    rows = res["functions"]
    starts = dict(db.execute('SELECT key, start FROM dots'))
    order = {row["key"]: -1 if row["kind"] == "module_level" else (starts.get(row["key"]) or 0) for row in rows}
    cells, folders, files, width, tall = layout([row["key"] for row in rows], order)
    picked = landmarks.pick(rows, landmarks.load_config())
    groups, group_of = coupling.groups(db, rows)
    fields = ("references", "tests", "calls", "reach", "dependents", "changes", "pace", "tested")
    return dict(W=width, H=tall, since=res["since"], commits=res["commits"], slow_floor=res["slow_floor"],
                fields=list(fields), folders=folders, files=files,
                units={row["key"]: dict(xy=cells[row["key"]], n=[row[field] for field in fields],
                                        group=group_of.get(row["key"])) for row in rows},
                landmarks=[row["key"] for row in picked], groups=[dict(id=g["id"], name=g["name"], size=g["size"])
                                                                   for g in groups])
