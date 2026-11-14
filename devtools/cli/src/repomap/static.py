"""Read a package's source and list its dots (modules, classes, functions) and arrows (calls, imports, subclasses).

This is the static half of the code graph. The traced half (what tests actually ran) stays the source of truth;
static arrows fill in code that no test runs. Keys match the tracer's: "pkg/mod.py:Qual.name", with
"pkg/mod.py:<module>" for code at module level, the class name for a class body, and
"f.<locals>.<genexpr>" / "<lambda>" for inline code objects, so static and traced arrows line up without translation.

Arrow kinds:
  calls          a call resolved by name binding: a local def, an import, self/cls/super() method, module.attr
  calls_by_name  obj.attr(...) where no binding is known but exactly one function or method in the package has
                 that name: a guess, weighted lower later
  imports        module -> module, for imports inside the package
  inherits       class -> base class, for bases inside the package
Bare names that bind to nothing known, and method names defined more than once in the package, go to
`unresolved` so a later step can decide. Method calls whose name the package never defines (str.join, other
libraries) are dropped.

Usage: python -m repomap.static src/pkg   (prints JSON)
"""
import ast, builtins, json, sys
from pathlib import Path

MODULE_LEVEL = "<module>"  # the tracer's key for module-level code (code.co_qualname)
BUILTINS = set(dir(builtins))


def module_name(rel):
    """'pkg/sub/mod.py' -> 'pkg.sub.mod'; 'pkg/__init__.py' -> 'pkg'."""
    parts = rel[:-3].split("/")
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


class Module:
    """One file's symbol table: what each top-level name and class member is bound to."""

    def __init__(self, rel, tree):
        self.rel, self.tree, self.name = rel, tree, module_name(rel)
        self.is_pkg = rel.endswith("/__init__.py")
        self.defs = {}      # top-level name -> qualname (function or class)
        self.classes = {}   # class qualname -> {"methods": {name: qualname}, "bases": [ast expr], "node": node}
        self.imports = {}   # local name -> ("module", modname) or ("name", modname, attr)
        for n in tree.body:
            self._top(n)

    def _top(self, n):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self.defs[n.name] = n.name
        elif isinstance(n, ast.ClassDef):
            self.defs[n.name] = n.name
            self._class(n, n.name)
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.asname:
                    self.imports[a.asname] = ("module", a.name)
                else:  # `import a.b` binds `a`
                    top = a.name.split(".")[0]
                    self.imports[top] = ("module", top)
        elif isinstance(n, ast.ImportFrom):
            base = self.resolve_from(n)
            for a in n.names:
                if a.name != "*":
                    self.imports[a.asname or a.name] = ("name", base, a.name)
        elif isinstance(n, (ast.If, ast.Try)):  # `if TYPE_CHECKING:` / `try: import x` blocks still bind names
            for c in ast.iter_child_nodes(n):
                if isinstance(c, ast.stmt):
                    self._top(c)

    def _class(self, n, q):
        info = self.classes[q] = {"methods": {}, "bases": n.bases, "node": n}
        for c in n.body:
            if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef)):
                info["methods"][c.name] = f"{q}.{c.name}"
            elif isinstance(c, ast.ClassDef):
                self._class(c, f"{q}.{c.name}")

    def resolve_from(self, n):
        if not n.level:
            return n.module or ""
        parts = self.name.split(".")
        if not self.is_pkg:
            parts = parts[:-1]
        parts = parts[:len(parts) - (n.level - 1)] if n.level > 1 else parts
        return ".".join(parts + ([n.module] if n.module else []))


class Package:
    """All modules of one package, parsed once, with cross-module name resolution."""

    def __init__(self, root):
        root = Path(root).resolve()
        self.base = root.parent
        self.modules = {}   # modname -> Module
        self.errors = []
        for p in sorted(root.rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            rel = p.relative_to(self.base).as_posix()
            try:
                tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"), filename=rel)
            except SyntaxError as e:
                self.errors.append(f"{rel}: {e.msg} (line {e.lineno})")
                continue
            m = Module(rel, tree)
            self.modules[m.name] = m
        # method name -> every key that defines it, for the calls_by_name guess
        self.by_name = {}
        for m in self.modules.values():
            for name, q in m.defs.items():
                if q not in m.classes:
                    self.by_name.setdefault(name, set()).add(f"{m.rel}:{q}")
            for c in m.classes.values():
                for name, q in c["methods"].items():
                    self.by_name.setdefault(name, set()).add(f"{m.rel}:{q}")

    # name resolution ------------------------------------------------------------------------------------------

    def lookup(self, modname, attr, seen=None):
        """What `modname.attr` is, following re-exports: ('def', module, qualname) | ('module', modname) | None."""
        seen = seen or set()
        if (modname, attr) in seen:
            return None
        seen.add((modname, attr))
        m = self.modules.get(modname)
        if m is None:
            sub = f"{modname}.{attr}"
            return ("module", sub) if sub in self.modules else None
        if attr in m.defs:
            return ("def", m, m.defs[attr])
        if attr in m.imports:
            return self.binding(m, attr, seen)
        sub = f"{modname}.{attr}"
        return ("module", sub) if sub in self.modules else None

    def binding(self, m, name, seen=None):
        """What a bare name means at the top level of module m."""
        if name in m.defs:
            return ("def", m, m.defs[name])
        imp = m.imports.get(name)
        if imp is None:
            return None
        if imp[0] == "module":
            return ("module", imp[1]) if imp[1] in self.modules else None
        return self.lookup(imp[1], imp[2], seen)

    def class_of(self, m, expr):
        """A base-class expression -> (module, class qualname) inside the package, or None."""
        if isinstance(expr, ast.Name):
            b = self.binding(m, expr.id)
        elif isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
            b = self.binding(m, expr.value.id)
            b = self.lookup(b[1], expr.attr) if b and b[0] == "module" else None
        else:
            return None
        if b and b[0] == "def" and b[2] in b[1].classes:
            return b[1], b[2]
        return None

    def method(self, m, cls, name, depth=0):
        """Find `name` on class cls of module m or its in-package bases (method resolution, left to right)."""
        if depth > 20:
            return None
        info = m.classes.get(cls)
        if info is None:
            return None
        if name in info["methods"]:
            return f"{m.rel}:{info['methods'][name]}"
        for b in info["bases"]:
            found = self.class_of(m, b)
            if found:
                k = self.method(found[0], found[1], name, depth + 1)
                if k:
                    return k
        return None

    def callable_key(self, b):
        """A binding that is called -> the key of the code that runs (a class runs its __init__)."""
        if not b or b[0] != "def":
            return None
        m, q = b[1], b[2]
        if q in m.classes:
            return self.method(m, q, "__init__")
        return f"{m.rel}:{q}"

    # extraction -----------------------------------------------------------------------------------------------

    def extract(self, files=None):
        """Dots and arrows for every module, or only for the given relative paths (for per-file updates)."""
        dots, arrows, unresolved = [], set(), set()
        for m in self.modules.values():
            if files is not None and m.rel not in files:
                continue
            Walker(self, m, dots, arrows, unresolved).run()
        return {"dots": dots, "arrows": sorted(arrows), "unresolved": sorted(unresolved), "errors": self.errors}


class Walker:
    """Walk one module: record its dots, and an arrow for every call, import and base class it contains."""

    def __init__(self, pkg, m, dots, arrows, unresolved):
        self.pkg, self.m, self.dots, self.arrows, self.unresolved = pkg, m, dots, arrows, unresolved
        self.inline = set()

    def key(self, q):
        return f"{self.m.rel}:{q}"

    def run(self):
        m = self.m
        self.dots.append({"key": m.rel, "kind": "module", "parent": None, "start": 1,
                          "end": max([getattr(n, "end_lineno", 1) or 1 for n in m.tree.body] or [1])})
        self.dots.append({"key": self.key(MODULE_LEVEL), "kind": "module_level", "parent": m.rel, "start": 1,
                          "end": 1})
        for n in ast.walk(m.tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    self.import_arrow(a.name)
            elif isinstance(n, ast.ImportFrom):
                base = m.resolve_from(n)
                for a in n.names:
                    sub = f"{base}.{a.name}"
                    self.import_arrow(sub if sub in self.pkg.modules else base)
        self.body(m.tree.body, prefix="", owner=self.key(MODULE_LEVEL), cls=None, parent=m.rel)

    def import_arrow(self, modname):
        # `import pkg.sub.mod` depends on the deepest module that exists in the package
        parts = modname.split(".")
        while parts and ".".join(parts) not in self.pkg.modules:
            parts.pop()
        if parts and ".".join(parts) != self.m.name:
            self.arrows.add((self.m.rel, self.pkg.modules[".".join(parts)].rel, "imports"))

    def body(self, stmts, prefix, owner, cls, parent, local=None):
        """Statements at one scope. Nested defs and classes get their own dots; other code belongs to owner.

        prefix is the qualname prefix for things defined here ("f.<locals>.", "C.", or "" at module level), so keys
        match code.co_qualname. local maps names of functions defined in enclosing function scopes to their keys.
        """
        local = dict(local or {})
        if prefix.endswith(".<locals>."):  # a function scope: its own nested defs are callable by bare name
            for n in stmts:
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    local[n.name] = self.key(prefix + n.name)
        for n in stmts:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                q = f"{prefix}{n.name}"
                k = self.key(q)
                start = min([n.lineno] + [d.lineno for d in n.decorator_list])
                self.dots.append({"key": k, "kind": "function", "parent": parent, "start": start,
                                  "end": n.end_lineno})
                for d in n.decorator_list + [a for a in (n.args.defaults + n.args.kw_defaults) if a]:
                    self.calls(d, owner, cls, prefix, parent, local)  # these run in the enclosing scope
                self.body(n.body, f"{q}.<locals>.", k, cls, k, local)
            elif isinstance(n, ast.ClassDef):
                q = f"{prefix}{n.name}"
                k = self.key(q)
                self.dots.append({"key": k, "kind": "class", "parent": parent, "start": n.lineno,
                                  "end": n.end_lineno})
                for b in n.bases:
                    found = self.pkg.class_of(self.m, b)
                    if found:
                        self.arrows.add((k, f"{found[0].rel}:{found[1]}", "inherits"))
                for d in n.decorator_list + n.bases + [kw.value for kw in n.keywords]:
                    self.calls(d, owner, cls, prefix, parent, local)
                # the class body runs as its own code object (key = the class), once, when the class is defined
                self.arrows.add((owner, k, "calls"))
                self.body(n.body, f"{q}.", k, q if q in self.m.classes else None, k, local)
            else:
                self.calls(n, owner, cls, prefix, parent, local)

    def calls(self, node, owner, cls, prefix, parent, local):
        """Every call in node -> arrows from owner. Lambdas and generator expressions are their own code objects
        at runtime (key "...<locals>.<genexpr>"), so they get a dot and the calls inside them start from it."""
        stack = [node]
        while stack:
            n = stack.pop()
            if isinstance(n, (ast.Lambda, ast.GeneratorExp)):
                q = f"{prefix}{'<lambda>' if isinstance(n, ast.Lambda) else '<genexpr>'}"
                k = self.key(q)
                if k not in self.inline:  # several genexprs in one function share a key, as they do at runtime
                    self.inline.add(k)
                    self.dots.append({"key": k, "kind": "function", "parent": parent, "start": n.lineno,
                                      "end": n.end_lineno})
                self.arrows.add((owner, k, "calls"))
                inner = f"{q}.<locals>."
                if isinstance(n, ast.Lambda):
                    for d in n.args.defaults + [a for a in n.args.kw_defaults if a]:
                        stack.append(d)
                    self.calls(n.body, k, cls, inner, k, local)
                else:
                    # the first iterable is evaluated in the enclosing scope, the rest inside the generator
                    stack.append(n.generators[0].iter)
                    rest = [n.elt] + [x for i, g in enumerate(n.generators)
                                      for x in ([g.iter] if i else []) + g.ifs]
                    for x in rest:
                        self.calls(x, k, cls, inner, k, local)
                continue
            if isinstance(n, ast.Call):
                self.call(n, owner, cls, local)
            elif isinstance(n, ast.Compare) and any(isinstance(o, (ast.In, ast.NotIn)) for o in n.ops):
                self.guess(owner, "__contains__")  # `x in obj` runs obj.__contains__ when the package defines one
            stack.extend(ast.iter_child_nodes(n))

    def call(self, n, owner, cls, local):
        f = n.func
        target = None
        if isinstance(f, ast.Name):
            if f.id in local:
                target = local[f.id]
            else:
                target = self.pkg.callable_key(self.pkg.binding(self.m, f.id))
            if target is None and f.id not in BUILTINS and f.id not in self.m.imports:  # imports: other libraries
                self.unresolved.add((owner, f.id))
            if target:
                self.arrows.add((owner, target, "calls"))
            return
        if not isinstance(f, ast.Attribute):
            return
        v, attr = f.value, f.attr
        if isinstance(v, ast.Name) and v.id in ("self", "cls") and cls:
            target = self.pkg.method(self.m, cls, attr)
        elif isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id == "super" and cls:
            for b in self.m.classes[cls]["bases"]:
                found = self.pkg.class_of(self.m, b)
                if found and (target := self.pkg.method(found[0], found[1], attr)):
                    break
        elif isinstance(v, ast.Name):
            b = self.pkg.binding(self.m, v.id)
            if b and b[0] == "module":
                target = self.pkg.callable_key(self.pkg.lookup(b[1], attr))
            elif b and b[0] == "def" and b[2] in b[1].classes:  # Class.method(...) or Class.classmethod(...)
                target = self.pkg.method(b[1], b[2], attr)
        if target:
            self.arrows.add((owner, target, "calls"))
            return
        self.guess(owner, attr)

    def guess(self, owner, name):
        """A method call on an object of unknown type: link it only if the package defines that name once."""
        guesses = self.pkg.by_name.get(name, set())
        if len(guesses) == 1:
            self.arrows.add((owner, next(iter(guesses)), "calls_by_name"))
        elif guesses:  # ambiguous inside the package: keep the name so a later step can decide
            self.unresolved.add((owner, name))


def extract(root, files=None):
    """Static dots and arrows for the package at root (e.g. 'src/shopkit')."""
    return Package(root).extract(files)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        sys.exit("usage: python -m repomap.static <package dir>")
    json.dump(extract(argv[0]), sys.stdout, indent=1)
    print()


if __name__ == "__main__":
    main()
