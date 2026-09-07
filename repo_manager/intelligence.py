"""Read-only repository understanding based on objective filesystem evidence."""

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


PRESENT = "PRESENT"
MISSING = "MISSING"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Evidence:
    """One observable signal used by an intelligence result."""

    source: str
    target: str
    observation: str


@dataclass(frozen=True)
class DocumentationItem:
    key: str
    status: str
    paths: tuple[str, ...]
    evidence: tuple[Evidence, ...]
    freshness: str = "CURRENT"


@dataclass(frozen=True)
class StackResult:
    languages: tuple[str, ...]
    frameworks: tuple[str, ...]
    package_managers: tuple[str, ...]
    runtimes: tuple[str, ...]
    manifests: tuple[str, ...]
    build_systems: tuple[str, ...]
    evidence: tuple[Evidence, ...]


_DOCUMENTS = (
    ("README", ("README.md", "README.rst", "README.txt")),
    ("ARCHITECTURE", ("ARCHITECTURE.md", "docs/ARCHITECTURE.md")),
    ("ROADMAP", ("ROADMAP.md", "docs/ROADMAP.md")),
    ("TESTING", ("TESTING.md", "docs/TESTING.md")),
    ("SECURITY", ("SECURITY.md", ".github/SECURITY.md")),
    ("CONTRIBUTING", ("CONTRIBUTING.md", ".github/CONTRIBUTING.md")),
    ("CHANGELOG", ("CHANGELOG.md", "HISTORY.md", "docs/CHANGELOG.md")),
    ("LICENSE", ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING")),
    ("GITIGNORE", (".gitignore",)),
    ("GITATTRIBUTES", (".gitattributes",)),
    ("CI", (".github/workflows", ".gitlab-ci.yml", "azure-pipelines.yml", "Jenkinsfile")),
    ("DOCUMENTATION_DIRECTORY", ("docs", "documentation", "doc")),
)


def _path_exists(path: Path) -> bool | None:
    try:
        return path.is_file() or path.is_dir()
    except OSError:
        return None


def _existing(root: Path, candidates: Iterable[str]) -> tuple[tuple[Path, ...], bool]:
    paths = []
    unavailable = False
    for candidate in candidates:
        path = root / candidate
        state = _path_exists(path)
        if state:
            paths.append(path)
        elif state is None:
            unavailable = True
    return tuple(paths), unavailable


def inspect_documentation(root: str | Path) -> tuple[DocumentationItem, ...]:
    """Inspect known documentation/configuration locations without reading prose."""
    base = Path(root)
    result = []
    for key, candidates in _DOCUMENTS:
        paths, unavailable = _existing(base, candidates)
        evidence = tuple(Evidence("filesystem", str(path), "exists") for path in paths)
        result.append(DocumentationItem(
            key=key,
            status=PRESENT if paths else UNKNOWN if unavailable else MISSING,
            paths=tuple(str(path) for path in paths),
            evidence=evidence or (Evidence(
                "filesystem", str(base),
                "one or more locations were unavailable" if unavailable
                else "no known location exists"),),
            freshness="UNKNOWN" if unavailable and not paths else "CURRENT",
        ))
    return tuple(result)


def inspect_stack(root: str | Path) -> StackResult:
    """Detect a bounded set of ecosystems from manifests/configuration only."""
    base = Path(root)
    values: dict[str, set[str]] = {}
    evidence: list[Evidence] = []
    manifests: set[str] = set()

    checks = (
        ("pyproject.toml", "Python", "Python", "uv/poetry/pip"),
        ("requirements.txt", "Python", "Python", "pip"),
        ("Pipfile", "Python", "Python", "pipenv"),
        ("package.json", "JavaScript/TypeScript", "Node.js", "npm-compatible"),
        ("package-lock.json", "JavaScript/TypeScript", "Node.js", "npm"),
        ("pnpm-lock.yaml", "JavaScript/TypeScript", "Node.js", "pnpm"),
        ("yarn.lock", "JavaScript/TypeScript", "Node.js", "Yarn"),
        ("Cargo.toml", "Rust", "Rust", "Cargo"),
        ("Cargo.lock", "Rust", "Rust", "Cargo"),
        ("CMakeLists.txt", "C/C++", "CMake", "CMake"),
        ("build.gradle", "Kotlin/Java", "JVM", "Gradle"),
        ("build.gradle.kts", "Kotlin/Java", "JVM", "Gradle"),
        ("pom.xml", "Java", "JVM", "Maven"),
        ("mix.exs", "Elixir", "Elixir", "Mix"),
    )
    for filename, language, runtime, package_manager in checks:
        path = base / filename
        state = _path_exists(path)
        if state:
            manifests.add(filename)
            values.setdefault("languages", set()).add(language)
            values.setdefault("runtimes", set()).add(runtime)
            values.setdefault("package_managers", set()).add(package_manager)
            evidence.append(Evidence("filesystem", str(path), "known manifest/lockfile exists"))
        elif state is None:
            evidence.append(Evidence("filesystem", str(path), "inspection unavailable"))

    for filename, label in (("tsconfig.json", "TypeScript"), ("vite.config.js", "Vite"), ("vite.config.ts", "Vite"), ("next.config.js", "Next.js"), ("next.config.ts", "Next.js"), ("Dockerfile", "Docker")):
        path = base / filename
        state = _path_exists(path)
        if state:
            manifests.add(filename)
            if label == "TypeScript":
                values.setdefault("languages", set()).add(label)
            elif label in {"Vite", "Next.js"}:
                values.setdefault("frameworks", set()).add(label)
            else:
                values.setdefault("build_systems", set()).add(label)
            evidence.append(Evidence("filesystem", str(path), "known configuration exists"))
        elif state is None:
            evidence.append(Evidence("filesystem", str(path), "inspection unavailable"))

    workflows = base / ".github" / "workflows"
    try:
        has_workflows = workflows.is_dir() and any(workflows.iterdir())
    except OSError:
        has_workflows = None
    if has_workflows:
        values.setdefault("build_systems", set()).add("GitHub Actions")
        evidence.append(Evidence("filesystem", str(workflows), "workflow directory contains files"))
    elif has_workflows is None:
        evidence.append(Evidence("filesystem", str(workflows), "inspection unavailable"))
    return StackResult(
        languages=tuple(sorted(values.get("languages", set()))),
        frameworks=tuple(sorted(values.get("frameworks", set()))),
        package_managers=tuple(sorted(values.get("package_managers", set()))),
        runtimes=tuple(sorted(values.get("runtimes", set()))),
        manifests=tuple(sorted(manifests)),
        build_systems=tuple(sorted(values.get("build_systems", set()))),
        evidence=tuple(evidence),
    )
