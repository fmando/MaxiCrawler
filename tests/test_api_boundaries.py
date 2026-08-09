"""Tests that the web interface stayed a delivery layer.

Sprint 10 was allowed to add a second *client* and forbidden to add a second
*crawler*. Both halves are checked here by reading the import graph rather than
by reading the code: a docstring is a promise, and this is the part that can
fail a pull request.

Nothing here imports the packages it judges. The modules are parsed, so a test
run needs neither the ``web`` extra nor an importable Starlette to say whether
the boundary still holds.
"""

import ast
import json
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

SOURCE = Path("src")
PACKAGE = SOURCE / "maxicrawler"

CORE_PACKAGES = (
    "app",
    "config",
    "crawler",
    "database",
    "documents",
    "domain",
    "downloader",
    "events",
    "extractors",
    "library",
    "plugins",
    "providers",
    "utils",
    "web",
)
"""Everything that has to build and run without the web interface."""

BUILDERS = frozenset(
    {
        "CrawlEngine",
        "DiscoveryPipeline",
        "HtmlLinkParser",
        "SQLiteCrawlRepository",
        "SQLiteDatabase",
        "SQLiteDiscoveryRepository",
        "UrllibPageFetcher",
        "WebDiscoveryService",
    }
)
"""The parts :class:`~maxicrawler.app.CrawlService` assembles into a crawl.

A client that imported one of these would be building a second object graph,
which is the way two clients quietly become two crawlers.
"""


# --- reading the import graph -------------------------------------------------


def modules_of(package: str) -> tuple[Path, ...]:
    """Return every source file of *package*, subpackages included."""
    root = PACKAGE / package if package else PACKAGE
    return tuple(sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts))


def _dotted(path: Path) -> str:
    """Return the name *path* is imported under."""
    relative = path.relative_to(SOURCE).with_suffix("")
    parts = relative.parts[:-1] if relative.name == "__init__" else relative.parts
    return ".".join(parts)


def _base(path: Path, level: int) -> str:
    """Return what a relative import of *level* dots in *path* starts from."""
    name = _dotted(path)
    package = name if path.name == "__init__.py" else name.rpartition(".")[0]
    parts = package.split(".")
    return ".".join(parts[: len(parts) - level + 1])


def _names_of(node: ast.Import | ast.ImportFrom, path: Path) -> set[str]:
    """Return what one import statement in *path* names.

    Each name imported *from* a module is returned beside the module itself,
    because ``from maxicrawler.web import UrllibPageFetcher`` names a class the
    way ``from maxicrawler.api import routes`` names a module, and reading only
    ``node.module`` would see neither.
    """
    if isinstance(node, ast.Import):
        return {alias.name for alias in node.names}
    module = node.module or ""
    if node.level:
        base = _base(path, node.level)
        module = f"{base}.{module}" if module else base
    return {module} | {f"{module}.{alias.name}" for alias in node.names}


def imports_of(path: Path) -> frozenset[str]:
    """Return every module *path* imports, relative imports resolved."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return frozenset(
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for name in _names_of(node, path)
    )


def module_level_imports_of(path: Path) -> frozenset[str]:
    """Return only what *path* imports on being imported itself.

    An import inside a function costs nothing until the function is called,
    which is the whole difference between ``serve`` needing the extra and every
    other command needing it.
    """
    pending: list[ast.AST] = list(ast.parse(path.read_text(encoding="utf-8")).body)
    found: set[str] = set()
    while pending:
        node = pending.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if isinstance(node, ast.Import | ast.ImportFrom):
            found |= _names_of(node, path)
        pending.extend(ast.iter_child_nodes(node))
    return frozenset(found)


def imports_across(package: str) -> dict[Path, frozenset[str]]:
    """Return what every module of *package* imports."""
    return {path: imports_of(path) for path in modules_of(package)}


def offenders(package: str, forbidden: Iterable[str]) -> dict[str, set[str]]:
    """Return the modules of *package* importing anything under *forbidden*."""
    prefixes = tuple(forbidden)
    found: dict[str, set[str]] = {}
    for path, imported in imports_across(package).items():
        hits = {
            name
            for name in imported
            if any(name == prefix or name.startswith(f"{prefix}.") for prefix in prefixes)
        }
        if hits:
            found[path.as_posix()] = hits
    return found


def test_the_reader_finds_an_import_that_is_really_there() -> None:
    """The guard is worthless if it silently reads nothing."""
    imported = imports_of(PACKAGE / "api" / "views.py")

    assert "maxicrawler.web.report" in imported
    assert "maxicrawler.web.report.CrawlReport" in imported


def test_the_reader_covers_every_module_of_the_interface() -> None:
    names = {path.name for path in modules_of("api")}

    assert {"__init__.py", "application.py", "errors.py", "jobs.py", "routes.py"} <= names


# --- what the interface may not reach for -------------------------------------


def test_the_interface_never_imports_a_provider_a_downloader_or_the_library() -> None:
    """It has no business with any of the three, and the library page proves it.

    That page lists nothing yet precisely because listing it will go through a
    service in :mod:`maxicrawler.app`, the way crawling does.
    """
    found = offenders(
        "api",
        ("maxicrawler.providers", "maxicrawler.downloader", "maxicrawler.library"),
    )

    assert found == {}


def test_the_interface_holds_no_copy_of_the_command_line() -> None:
    """Not importing it is the weaker half; the stronger half is a review.

    Shared logic moves into :mod:`maxicrawler.app` and is used by both, which
    is how ``crawl_document`` and ``CrawlService`` got there.
    """
    assert offenders("api", ("maxicrawler.cli",)) == {}


def test_the_interface_builds_no_crawler_of_its_own() -> None:
    """Every crawl it runs is assembled by the service, not by a handler."""
    built = {
        path.as_posix(): sorted(name for name in imported if name.rpartition(".")[2] in BUILDERS)
        for path, imported in imports_across("api").items()
    }

    assert {path: names for path, names in built.items() if names} == {}


def test_the_interface_reaches_the_crawler_through_the_service() -> None:
    """Stated positively, so the test above cannot pass by importing nothing."""
    imported = imports_of(PACKAGE / "api" / "jobs.py")

    assert "maxicrawler.app.CrawlService" in imported


# --- what may not reach for the interface -------------------------------------


def test_no_core_package_imports_the_web_interface() -> None:
    """They are what the interface is a client of, and clients are optional.

    An installation without the ``web`` extra runs every command; a core
    package importing this one would end that quietly.
    """
    found = {
        package: hits
        for package in CORE_PACKAGES
        if (hits := offenders(package, ("maxicrawler.api",)))
    }

    assert found == {}


def test_the_command_line_is_the_one_exception_and_only_for_the_message() -> None:
    """``serve`` lives there, so the entry point is where this layer is known.

    On import it may reach for the module that names the missing extra — which
    by definition has to be readable on an installation that has not got it —
    and for nothing else.
    """
    reached = {
        name
        for path in modules_of("cli")
        for name in module_level_imports_of(path)
        if name.startswith("maxicrawler.api")
    }

    assert reached == {
        "maxicrawler.api.errors",
        "maxicrawler.api.errors.MISSING_EXTRA",
        "maxicrawler.api.errors.WebDependencyError",
    }


def test_the_command_line_reaches_the_application_only_inside_a_command() -> None:
    """A module-level import would make every other command need the extra."""
    inside_the_command = imports_of(PACKAGE / "cli" / "__init__.py")
    on_import = module_level_imports_of(PACKAGE / "cli" / "__init__.py")

    assert "maxicrawler.api.create_app" in inside_the_command
    assert "uvicorn" in inside_the_command
    assert "maxicrawler.api.create_app" not in on_import
    assert "uvicorn" not in on_import


def test_only_the_interface_imports_the_web_framework() -> None:
    """Starlette and Jinja stop at this package; nothing else learns about HTML."""
    framework = ("starlette", "jinja2")
    found = {
        package: hits
        for package in (*CORE_PACKAGES, "gui")
        if (hits := offenders(package, framework))
    }

    assert found == {}


def test_the_interface_does_import_the_framework() -> None:
    """Otherwise the test above would be passing on a package that does nothing."""
    assert offenders("api", ("starlette",)) != {}


# --- and the same read from a running interpreter -----------------------------


def _modules_after(statement: str) -> set[str]:
    """Return the top-level modules a fresh interpreter loaded to run *statement*."""
    program = f"{statement}\nimport json, sys\nprint(json.dumps(sorted(sys.modules)))"
    completed = subprocess.run(  # noqa: S603 - this interpreter, a literal program
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        check=True,
    )
    return {name.partition(".")[0] for name in json.loads(completed.stdout)}


def test_the_command_line_starts_without_the_extra_installed() -> None:
    """The import graph says so; this is the same question asked of Python.

    ``maxicrawler.cli`` imports ``maxicrawler.api.errors`` at module level, so
    the thing worth proving is that the rest of the package does not come with
    it.
    """
    loaded = _modules_after("import maxicrawler.cli")

    assert "starlette" not in loaded
    assert "uvicorn" not in loaded
    assert "jinja2" not in loaded


def test_asking_whether_the_interface_exists_costs_nothing() -> None:
    loaded = _modules_after("import maxicrawler.api")

    assert "starlette" not in loaded
