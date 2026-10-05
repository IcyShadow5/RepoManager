"""Explicit current-product gates and separately labelled historical references.

The checked-in manifest records reviewed test IDs, not a filename heuristic.
New or removed tests require a taxonomy decision before this gate can run.
"""
import argparse
import ast
from collections import Counter
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "suite_manifest.json"
CATEGORIES = (
    "CURRENT_CORE", "CURRENT_SERVICE", "CURRENT_QT_BRIDGE", "CURRENT_QML_UI",
    "CURRENT_PACKAGE_RUNTIME", "CURRENT_WINDOWS_INTEGRATION",
    "LEGACY_TKINTER_PARITY", "LEGACY_OBSOLETE", "REDUNDANT", "INVALID",
)
LAYERS = {
    "core": {"CURRENT_CORE"}, "service": {"CURRENT_SERVICE"},
    "bridge": {"CURRENT_QT_BRIDGE"}, "qml": {"CURRENT_QML_UI"},
    "package": {"CURRENT_PACKAGE_RUNTIME"}, "windows": {"CURRENT_WINDOWS_INTEGRATION"},
    "legacy": {"LEGACY_TKINTER_PARITY", "LEGACY_OBSOLETE"},
    "auxiliary": {"CURRENT_CORE"},
    "current": set(CATEGORIES[:6]), "all": set(CATEGORIES),
}


def source_ids(root):
    ids = set()
    for path in (root / "tests").rglob("test_*.py"):
        module = ".".join(path.relative_to(root).with_suffix("").parts)
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for cls in tree.body:
            if isinstance(cls, ast.ClassDef):
                for method in cls.body:
                    if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name.startswith("test_"):
                        ids.add(f"{module}.{cls.name}.{method.name}")
    return ids


def catalog(root=ROOT, manifest=MANIFEST):
    document = json.loads(manifest.read_text(encoding="utf-8"))
    if document.get("schema_version") != 1:
        raise ValueError("Unsupported test manifest schema")
    entries = {}
    for group in document["groups"]:
        for method in group["tests"]:
            identity = f"{group['module']}.{group['class']}.{method}"
            category = group.get("method_categories", {}).get(method, group["category"])
            if category not in CATEGORIES or identity in entries:
                raise ValueError(f"Invalid or duplicate classification: {identity}")
            entries[identity] = {**group, "category": category}
    found = source_ids(root)
    if found != entries.keys():
        raise ValueError(f"Unclassified tests: {sorted(found - entries.keys())}; absent tests: {sorted(entries.keys() - found)}")
    return entries


def select(entries, layer):
    selected = {}
    for identity, group in entries.items():
        included = (not group["gate"] if layer == "auxiliary"
                    else layer in {"all", "legacy"} or group["gate"])
        if included and group["category"] in LAYERS[layer]:
            selected[identity] = group
    return selected


class LayerResult(unittest.TextTestResult):
    def __init__(self, *args, entries, **kwargs):
        super().__init__(*args, **kwargs)
        self.entries = entries
        self.counts = Counter()

    def startTest(self, test):
        super().startTest(test)
        if test.id() not in self.entries:
            raise ValueError("Loader produced an unclassified test: " + test.id())
        self.counts[self.entries[test.id()]["category"]] += 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layer", choices=LAYERS, required=True)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    entries = select(catalog(), args.layer)
    if args.list:
        print(json.dumps(dict(sorted(Counter(g["category"] for g in entries.values()).items())), indent=2))
        return 0
    if args.layer == "legacy":
        print("LEGACY / PARITY REFERENCE — NOT CURRENT UI PROOF", flush=True)
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromNames(list(entries))
    if loader.errors:
        raise RuntimeError("\n".join(loader.errors))
    if args.layer not in {"legacy", "all"} and "repo_manager.main" in sys.modules:
        raise RuntimeError("Current gate imported the legacy Tkinter presentation")
    result = unittest.TextTestRunner(verbosity=1, resultclass=lambda *a, **kw: LayerResult(*a, entries=entries, **kw)).run(suite)
    if args.layer not in {"legacy", "all"} and "repo_manager.main" in sys.modules:
        raise RuntimeError("Current tests loaded the legacy presentation during execution")
    current_skips = [test.id() for test, _ in result.skipped
                     if entries[test.id()]["gate"]]
    success = result.wasSuccessful() and not current_skips
    summary = {"layer": args.layer, "counts": dict(sorted(result.counts.items())),
               "run": result.testsRun, "skipped": len(result.skipped),
               "failures": len(result.failures), "errors": len(result.errors),
               "current_skips": current_skips, "passed": success}
    print(json.dumps(summary, indent=2))
    if args.json:
        args.json.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
