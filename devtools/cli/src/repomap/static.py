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
'unresolved' so a later step can decide. Method calls whose name the package never defines (str.join, other
libraries) are dropped.

Usage: python -m repomap.static src/pkg   (prints JSON)
"""
import ast, builtins, json, sys
from pathlib import Path

MODULE_LEVEL = "<module>"  # the tracer's key for module-level code (code.co_qualname)
BUILTINS = set(dir(builtins))
FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)


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
        for statement in tree.body:
            self._top(statement)

    def _top(self, statement):
        if isinstance(statement, FUNCTION_NODES):
            self.defs[statement.name] = statement.name
        elif isinstance(statement, ast.ClassDef):
            self.defs[statement.name] = statement.name
            self._class(statement, statement.name)
        elif isinstance(statement, ast.Import):
            for alias in statement.names:
                if alias.asname:
                    self.imports[alias.asname] = ("module", alias.name)
                else:  # 'import a.b' binds 'a'
                    top = alias.name.split(".")[0]
                    self.imports[top] = ("module", top)
        elif isinstance(statement, ast.ImportFrom):
            source = self.resolve_from(statement)
            for alias in statement.names:
                if alias.name != "*":
                    self.imports[alias.asname or alias.name] = ("name", source, alias.name)
        elif isinstance(statement, (ast.If, ast.Try)):  # 'if TYPE_CHECKING:' / 'try: import x' blocks bind names
            for child in ast.iter_child_nodes(statement):
                if isinstance(child, ast.stmt):
                    self._top(child)

    def _class(self, class_node, qualname):
        info = self.classes[qualname] = {"methods": {}, "bases": class_node.bases, "node": class_node}
        for member in class_node.body:
            if isinstance(member, FUNCTION_NODES):
                info["methods"][member.name] = f"{qualname}.{member.name}"
            elif isinstance(member, ast.ClassDef):
                self._class(member, f"{qualname}.{member.name}")

    def resolve_from(self, import_node):
        """The absolute module name a 'from ... import' reads from, resolving leading dots."""
        if not import_node.level:
            return import_node.module or ""
        parts = self.name.split(".")
        if not self.is_pkg:
            parts = parts[:-1]
        if import_node.level > 1:
            parts = parts[:len(parts) - (import_node.level - 1)]
        return ".".join(parts + ([import_node.module] if import_node.module else []))


class Package:
    """All modules of one package, parsed once, with cross-module name resolution.

    A binding is what a name means: ('def', module, qualname) for a function or class, ('module', modname) for a
    module, or None when it is not part of the package.
    """

    def __init__(self, root):
        root = Path(root).resolve()
        self.base = root.parent
        self.modules = {}   # modname -> Module
        self.errors = []
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(self.base).as_posix()
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=rel)
            except SyntaxError as e:
                self.errors.append(f"{rel}: {e.msg} (line {e.lineno})")
                continue
            module = Module(rel, tree)
            self.modules[module.name] = module
        # function or method name -> every key that defines it, for the calls_by_name guess
        self.by_name = {}
        for module in self.modules.values():
            for name, qualname in module.defs.items():
                if qualname not in module.classes:
                    self.by_name.setdefault(name, set()).add(f"{module.rel}:{qualname}")
            for info in module.classes.values():
                for name, qualname in info["methods"].items():
                    self.by_name.setdefault(name, set()).add(f"{module.rel}:{qualname}")

    # name resolution ------------------------------------------------------------------------------------------

    def lookup(self, modname, attr, seen=None):
        """The binding of 'modname.attr', following re-exports from one module to another."""
        seen = seen or set()
        if (modname, attr) in seen:
            return None
        seen.add((modname, attr))
        module = self.modules.get(modname)
        if module is None:
            submodule = f"{modname}.{attr}"
            return ("module", submodule) if submodule in self.modules else None
        if attr in module.defs:
            return ("def", module, module.defs[attr])
        if attr in module.imports:
            return self.binding(module, attr, seen)
        submodule = f"{modname}.{attr}"
        return ("module", submodule) if submodule in self.modules else None

    def binding(self, module, name, seen=None):
        """The binding of a bare name at the top level of a module."""
        if name in module.defs:
            return ("def", module, module.defs[name])
        imported = module.imports.get(name)
        if imported is None:
            return None
        if imported[0] == "module":
            return ("module", imported[1]) if imported[1] in self.modules else None
        _, source, attr = imported
        return self.lookup(source, attr, seen)

    def class_of(self, module, expr):
        """A base-class expression -> (module, class qualname) inside the package, or None."""
        if isinstance(expr, ast.Name):
            bound = self.binding(module, expr.id)
        elif isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
            bound = self.binding(module, expr.value.id)
            bound = self.lookup(bound[1], expr.attr) if bound and bound[0] == "module" else None
        else:
            return None
        if bound and bound[0] == "def" and bound[2] in bound[1].classes:
            return bound[1], bound[2]
        return None

    def method(self, module, class_qualname, name, depth=0):
        """Find 'name' on a class or its in-package bases (method resolution, left to right)."""
        if depth > 20:
            return None
        info = module.classes.get(class_qualname)
        if info is None:
            return None
        if name in info["methods"]:
            return f"{module.rel}:{info['methods'][name]}"
        for base_expr in info["bases"]:
            base = self.class_of(module, base_expr)
            if base:
                found = self.method(base[0], base[1], name, depth + 1)
                if found:
                    return found
        return None

    def callable_key(self, bound):
        """A binding that is called -> the key of the code that runs (a class runs its __init__)."""
        if not bound or bound[0] != "def":
            return None
        _, module, qualname = bound
        if qualname in module.classes:
            return self.method(module, qualname, "__init__")
        return f"{module.rel}:{qualname}"

    # extraction -----------------------------------------------------------------------------------------------

    def extract(self, files=None):
        """Dots and arrows for every module, or only for the given relative paths (for per-file updates)."""
        dots, arrows, unresolved = [], set(), set()
        for module in self.modules.values():
            if files is not None and module.rel not in files:
                continue
            Walker(self, module, dots, arrows, unresolved).run()
        return {"dots": dots, "arrows": sorted(arrows), "unresolved": sorted(unresolved), "errors": self.errors}


class Walker:
    """Walk one module: record its dots, and an arrow for every call, import and base class it contains."""

    def __init__(self, pkg, module, dots, arrows, unresolved):
        self.pkg, self.module, self.dots, self.arrows, self.unresolved = pkg, module, dots, arrows, unresolved
        self.inline = set()

    def key(self, qualname):
        return f"{self.module.rel}:{qualname}"

    def run(self):
        module = self.module
        last_line = max([getattr(s, "end_lineno", 1) or 1 for s in module.tree.body] or [1])
        self.dots.append({"key": module.rel, "kind": "module", "parent": None, "start": 1, "end": last_line})
        self.dots.append({"key": self.key(MODULE_LEVEL), "kind": "module_level", "parent": module.rel, "start": 1,
                          "end": 1})
        for node in ast.walk(module.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.import_arrow(alias.name)
            elif isinstance(node, ast.ImportFrom):
                source = module.resolve_from(node)
                for alias in node.names:
                    submodule = f"{source}.{alias.name}"
                    self.import_arrow(submodule if submodule in self.pkg.modules else source)
        self.body(module.tree.body, prefix="", owner=self.key(MODULE_LEVEL), cls=None, parent=module.rel)

    def import_arrow(self, modname):
        # 'import pkg.sub.mod' depends on the deepest module that exists in the package
        parts = modname.split(".")
        while parts and ".".join(parts) not in self.pkg.modules:
            parts.pop()
        if parts and ".".join(parts) != self.module.name:
            self.arrows.add((self.module.rel, self.pkg.modules[".".join(parts)].rel, "imports"))

    def body(self, statements, prefix, owner, cls, parent, local=None):
        """Statements at one scope. Nested defs and classes get their own dots; other code belongs to owner.

        prefix is the qualname prefix for things defined here ("f.<locals>.", "C.", or "" at module level), so keys
        match code.co_qualname. local maps names of functions defined in enclosing function scopes to their keys.
        """
        local = dict(local or {})
        if prefix.endswith(".<locals>."):  # a function scope: its own nested defs are callable by bare name
            for statement in statements:
                if isinstance(statement, FUNCTION_NODES):
                    local[statement.name] = self.key(prefix + statement.name)
        for statement in statements:
            if isinstance(statement, FUNCTION_NODES):
                qualname = f"{prefix}{statement.name}"
                dot_key = self.key(qualname)
                start = min([statement.lineno] + [dec.lineno for dec in statement.decorator_list])
                self.dots.append({"key": dot_key, "kind": "function", "parent": parent, "start": start,
                                  "end": statement.end_lineno})
                args = statement.args
                defaults = [default for default in args.defaults + args.kw_defaults if default]
                for expr in statement.decorator_list + defaults:
                    self.calls(expr, owner, cls, prefix, parent, local)  # these run in the enclosing scope
                self.body(statement.body, f"{qualname}.<locals>.", dot_key, cls, dot_key, local)
            elif isinstance(statement, ast.ClassDef):
                qualname = f"{prefix}{statement.name}"
                dot_key = self.key(qualname)
                self.dots.append({"key": dot_key, "kind": "class", "parent": parent, "start": statement.lineno,
                                  "end": statement.end_lineno})
                for base_expr in statement.bases:
                    base = self.pkg.class_of(self.module, base_expr)
                    if base:
                        self.arrows.add((dot_key, f"{base[0].rel}:{base[1]}", "inherits"))
                keywords = [kw.value for kw in statement.keywords]
                for expr in statement.decorator_list + statement.bases + keywords:
                    self.calls(expr, owner, cls, prefix, parent, local)
                # the class body runs as its own code object (key = the class), once, when the class is defined
                self.arrows.add((owner, dot_key, "calls"))
                body_cls = qualname if qualname in self.module.classes else None
                self.body(statement.body, f"{qualname}.", dot_key, body_cls, dot_key, local)
            else:
                self.calls(statement, owner, cls, prefix, parent, local)

    def calls(self, root, owner, cls, prefix, parent, local):
        """Every call under root -> arrows from owner. Lambdas and generator expressions are their own code objects
        at runtime (key "...<locals>.<genexpr>"), so they get a dot and the calls inside them start from it."""
        stack = [root]
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.Lambda, ast.GeneratorExp)):
                qualname = f"{prefix}{'<lambda>' if isinstance(node, ast.Lambda) else '<genexpr>'}"
                inline_key = self.key(qualname)
                if inline_key not in self.inline:  # several genexprs in one function share a key, as at runtime
                    self.inline.add(inline_key)
                    self.dots.append({"key": inline_key, "kind": "function", "parent": parent,
                                      "start": node.lineno, "end": node.end_lineno})
                self.arrows.add((owner, inline_key, "calls"))
                inner_prefix = f"{qualname}.<locals>."
                if isinstance(node, ast.Lambda):
                    stack.extend(default for default in node.args.defaults + node.args.kw_defaults if default)
                    self.calls(node.body, inline_key, cls, inner_prefix, inline_key, local)
                else:
                    # the first iterable is evaluated in the enclosing scope, the rest inside the generator
                    stack.append(node.generators[0].iter)
                    inside = [node.elt]
                    for i, comp in enumerate(node.generators):
                        inside += ([comp.iter] if i else []) + comp.ifs
                    for expr in inside:
                        self.calls(expr, inline_key, cls, inner_prefix, inline_key, local)
                continue
            if isinstance(node, ast.Call):
                self.call(node, owner, cls, local)
            elif isinstance(node, ast.Compare) and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
                self.guess(owner, "__contains__")  # 'x in obj' runs obj.__contains__ when the package defines one
            stack.extend(ast.iter_child_nodes(node))

    def call(self, call_node, owner, cls, local):
        func = call_node.func
        target = None
        if isinstance(func, ast.Name):
            if func.id in local:
                target = local[func.id]
            else:
                target = self.pkg.callable_key(self.pkg.binding(self.module, func.id))
            imported = func.id in self.module.imports  # imported but not from the package: another library
            if target is None and func.id not in BUILTINS and not imported:
                self.unresolved.add((owner, func.id))
            if target:
                self.arrows.add((owner, target, "calls"))
            return
        if not isinstance(func, ast.Attribute):
            return
        receiver, attr = func.value, func.attr
        if isinstance(receiver, ast.Name) and receiver.id in ("self", "cls") and cls:
            target = self.pkg.method(self.module, cls, attr)
        elif (isinstance(receiver, ast.Call) and isinstance(receiver.func, ast.Name)
              and receiver.func.id == "super" and cls):
            for base_expr in self.module.classes[cls]["bases"]:
                base = self.pkg.class_of(self.module, base_expr)
                if base and (target := self.pkg.method(base[0], base[1], attr)):
                    break
        elif isinstance(receiver, ast.Name):
            bound = self.pkg.binding(self.module, receiver.id)
            if bound and bound[0] == "module":
                target = self.pkg.callable_key(self.pkg.lookup(bound[1], attr))
            elif bound and bound[0] == "def" and bound[2] in bound[1].classes:  # Class.method(...)
                target = self.pkg.method(bound[1], bound[2], attr)
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
