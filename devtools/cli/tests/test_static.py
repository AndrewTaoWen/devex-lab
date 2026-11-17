"""The static half of the code graph: dots and arrows read from source, with keys that match the tracer's."""
import json, textwrap

from conftest import SHOPKIT
from repomap.static import extract, module_name


def arrows(g, kind=None):
    return {(a, b) for a, b, k in g["arrows"] if kind is None or k == kind}


def make_pkg(tmp_path, files):
    root = tmp_path / "src" / "pkg"
    for rel, src in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(src))
    return root


def test_module_names():
    """File paths turn into Python module names, and a package's __init__.py is named after its folder."""
    assert module_name("pkg/sub/mod.py") == "pkg.sub.mod"
    assert module_name("pkg/__init__.py") == "pkg"
    assert module_name("pkg/sub/__init__.py") == "pkg.sub"


def test_shopkit_calls_through_imports():
    """When one function calls another that it imported from a different file, the parser draws an
    arrow between them."""
    g = extract(SHOPKIT / "src/shopkit")
    calls = arrows(g, "calls")
    assert ("shopkit/checkout.py:total", "shopkit/tax.py:tax_for") in calls
    assert ("shopkit/checkout.py:total", "shopkit/money.py:round_cents") in calls
    assert ("shopkit/checkout.py:receipt", "shopkit/checkout.py:total") in calls
    assert ("shopkit/tax.py:tax_for", "shopkit/money.py:round_cents") in calls


def test_shopkit_untested_code_gets_its_callee_by_name():
    """to_csv calls price_of on an object whose type the parser can't see. Only one function in the package is named
    price_of, so the parser links them as a guess. No test runs to_csv, so this guess is the only arrow it has."""
    g = extract(SHOPKIT / "src/shopkit")
    assert ("shopkit/export.py:to_csv", "shopkit/catalog.py:Catalog.price_of") in arrows(g, "calls_by_name")


def test_shopkit_imports_and_no_noise():
    """Imports between files become arrows. Calls to Python built-ins, string methods and other libraries are left out
    and not reported as unknown, so the graph only holds the package's own code."""
    g = extract(SHOPKIT / "src/shopkit")
    assert ("shopkit/checkout.py", "shopkit/money.py") in arrows(g, "imports")
    assert ("shopkit/tax.py", "shopkit/money.py") in arrows(g, "imports")
    # builtins, str methods and other libraries (decimal.Decimal) are not package arrows or unresolved names
    assert g["unresolved"] == [] and g["errors"] == []


def test_dots_have_kinds_parents_and_spans():
    """Each module, class and function gets a dot that knows what kind it is, what it sits inside, and which lines it
    covers."""
    g = extract(SHOPKIT / "src/shopkit")
    d = {x["key"]: x for x in g["dots"]}
    assert d["shopkit/cart.py"]["kind"] == "module"
    assert d["shopkit/cart.py:Cart"]["kind"] == "class" and d["shopkit/cart.py:Cart"]["parent"] == "shopkit/cart.py"
    sub = d["shopkit/cart.py:Cart.subtotal"]
    assert sub["kind"] == "function" and sub["parent"] == "shopkit/cart.py:Cart"
    assert sub["start"] == 14 and sub["end"] == 15
    assert d["shopkit/cart.py:<module>"]["kind"] == "module_level"


def test_keys_match_the_tracer(recorded):
    """Every function the tests ran has a dot with exactly the same name here, so the read-from-source graph and the
    seen-in-tests graph join up without any renaming."""
    memory = json.loads((recorded / ".repomap/memory.json").read_text())
    static = {x["key"] for x in extract(recorded / "src/shopkit")["dots"]}
    traced = set(memory["functions"])
    assert traced and traced <= static


def test_finds_every_call_the_tracer_saw(recorded):
    """Every call the tests really made between shopkit functions is also found by reading the source, including
    the hidden call that 'x in obj' makes."""
    memory = json.loads((recorded / ".repomap/memory.json").read_text())
    traced = {tuple(e) for e in memory["edges"] if e[0].startswith("shopkit/")}
    assert len(traced) >= 10
    assert traced <= arrows(extract(recorded / "src/shopkit"))


def test_self_super_and_inheritance(tmp_path):
    """Calls through self, super() and a parent class are followed to the right method, and creating an object
    points at the __init__ that really runs, even when it comes from the parent class."""
    root = make_pkg(tmp_path, {
        "__init__.py": "",
        "base.py": """
            class Base:
                def __init__(self):
                    self.setup()
                def setup(self):
                    pass
                def save(self):
                    return self.validate()
                def validate(self):
                    return True
            """,
        "child.py": """
            from .base import Base

            class Child(Base):
                def validate(self):
                    return super().validate() and self.extra()
                def extra(self):
                    return True

            def make():
                return Child()
            """,
    })
    g = extract(root)
    calls = arrows(g, "calls")
    assert ("pkg/child.py:Child", "pkg/base.py:Base") in arrows(g, "inherits")
    assert ("pkg/base.py:Base.__init__", "pkg/base.py:Base.setup") in calls
    assert ("pkg/child.py:Child.validate", "pkg/base.py:Base.validate") in calls  # super()
    assert ("pkg/child.py:Child.validate", "pkg/child.py:Child.extra") in calls  # self
    assert ("pkg/child.py:make", "pkg/base.py:Base.__init__") in calls  # Child() runs the inherited __init__


def test_reexports_module_aliases_and_nested_functions(tmp_path):
    """Calls are still found when a function is imported through the package's __init__, through a module imported
    under another name, or defined inside another function."""
    root = make_pkg(tmp_path, {
        "__init__.py": "from .core import run\n",
        "core.py": """
            def run():
                def inner():
                    return helper()
                return inner()

            def helper():
                return 1
            """,
        "app.py": """
            import pkg.core as core
            from pkg import run

            def main():
                return run() + core.helper()
            """,
    })
    g = extract(root)
    calls = arrows(g, "calls")
    assert ("pkg/app.py:main", "pkg/core.py:run") in calls  # through the re-export in __init__
    assert ("pkg/app.py:main", "pkg/core.py:helper") in calls  # module alias
    assert ("pkg/core.py:run", "pkg/core.py:run.<locals>.inner") in calls
    assert ("pkg/core.py:run.<locals>.inner", "pkg/core.py:helper") in calls
    assert ("pkg/app.py", "pkg/core.py") in arrows(g, "imports")
    assert ("pkg/__init__.py", "pkg/core.py") in arrows(g, "imports")


def test_ambiguous_names_stay_unresolved(tmp_path):
    """When two classes both have a close method and the parser can't tell which one is meant, it draws no arrow.
    It keeps the name in a separate list so a later step can decide, and it does the same for names it can't find."""
    root = make_pkg(tmp_path, {
        "__init__.py": "",
        "a.py": "class A:\n    def close(self):\n        pass\n",
        "b.py": "class B:\n    def close(self):\n        pass\n\ndef shut(x):\n    x.close()\n    mystery()\n",
    })
    g = extract(root)
    assert not any(a == "pkg/b.py:shut" for a, _, _ in g["arrows"])
    assert ["pkg/b.py:shut", "close"] in map(list, g["unresolved"]) and ["pkg/b.py:shut", "mystery"] in map(list, g["unresolved"])


def test_module_level_code_and_syntax_errors(tmp_path):
    """Code that runs when a file is imported gets its own dot, so its calls are kept. A file with a syntax error is
    reported and skipped, and the rest of the package is still read."""
    root = make_pkg(tmp_path, {
        "__init__.py": "",
        "conf.py": "def load():\n    return {}\n\nSETTINGS = load()\n",
        "broken.py": "def oops(:\n",
    })
    g = extract(root)
    assert ("pkg/conf.py:<module>", "pkg/conf.py:load") in arrows(g, "calls")
    assert len(g["errors"]) == 1 and g["errors"][0].startswith("pkg/broken.py")


def test_only_some_files(tmp_path):
    """Asking for one file returns only that file's dots and arrows, which is what lets the
    graph update one file at a time."""
    g = extract(SHOPKIT / "src/shopkit", files={"shopkit/tax.py"})
    assert {a for a, _, _ in g["arrows"]} == {"shopkit/tax.py", "shopkit/tax.py:tax_for"}
    assert {x["key"].split(":")[0] for x in g["dots"]} == {"shopkit/tax.py"}
