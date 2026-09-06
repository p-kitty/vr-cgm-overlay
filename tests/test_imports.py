"""Every import inside `cgm` names a module that exists.

Most of them are proved by the rest of the suite simply running. The
ones that are not are the lazy imports: `run()` pulls in the overlay
inside the function body so that `--dry-run` works on a machine with no
SteamVR, and a line that only executes with a headset attached is a line
no headless check touches. `compileall` does not help -- a stale module
name is valid syntax.

That gap shipped once: after the move to `src/cgm/`, `run()` still said
`from overlay import WristOverlay`, and nothing noticed until someone
started the app for real.

So the check is static. Walk the package's own source, collect every
module named by an `import`, and ask whether it can be found -- without
importing anything, so this stays runnable on a core-only install.
"""

from __future__ import annotations

import ast
import importlib.util
import unittest
from pathlib import Path

import cgm

PACKAGE_ROOT = Path(cgm.__file__).parent

# Imports that are allowed not to resolve, because the install they come
# from is optional. `openvr` is behind the `vr` extra: a desktop-only
# install has no use for the SteamVR bindings, and cgm.vr is the only
# thing that reaches for them.
OPTIONAL = {"openvr"}


def imported_modules(source: Path) -> set[tuple[str, int]]:
    """Every module named by an import in one file, with its line."""
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    found: set[tuple[str, int]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            # A relative import is resolved against a package that by
            # definition exists, so there is nothing here to get wrong.
            if node.level == 0 and node.module:
                found.add((node.module, node.lineno))
    return found


def resolves(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        # A missing parent package raises rather than returning None.
        return False


class Imports(unittest.TestCase):
    def test_the_package_has_source_to_walk(self):
        # An editable install points at src/cgm; a broken one would make
        # every assertion below pass by finding no files at all.
        files = list(PACKAGE_ROOT.rglob("*.py"))
        self.assertGreater(len(files), 5, f"only found {files} under {PACKAGE_ROOT}")

    def test_every_import_names_something_that_exists(self):
        for source in sorted(PACKAGE_ROOT.rglob("*.py")):
            relative = source.relative_to(PACKAGE_ROOT.parent)
            for name, line in sorted(imported_modules(source)):
                if name in OPTIONAL:
                    continue
                with self.subTest(f"{relative}:{line}", module=name):
                    self.assertTrue(
                        resolves(name),
                        f"{relative}:{line} imports {name!r}, which does not exist",
                    )

    def test_the_package_never_imports_by_a_bare_module_name(self):
        # The names the modules had before they were a package. Any of
        # them appearing again means a line was moved without being
        # rewritten -- and would resolve, wrongly, if the working
        # directory happened to contain a file of that name.
        stale = {"armguide", "config", "librelink", "main", "overlay", "renderer"}
        for source in sorted(PACKAGE_ROOT.rglob("*.py")):
            relative = source.relative_to(PACKAGE_ROOT.parent)
            for name, line in sorted(imported_modules(source)):
                with self.subTest(f"{relative}:{line}", module=name):
                    self.assertNotIn(
                        name.split(".")[0],
                        stale,
                        f"{relative}:{line} imports {name!r} by its pre-package "
                        "name; say cgm.<layer>.<module>",
                    )


if __name__ == "__main__":
    unittest.main()
