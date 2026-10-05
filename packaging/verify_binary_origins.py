"""Reject dependency collection from unrelated developer tool installations."""
import ast
import json
import os
from pathlib import Path
import sys


def verify(toc, output):
    roots = {"build-environment": Path(sys.prefix).resolve(),
             "python-runtime": Path(sys.base_prefix).resolve(),
             "windows-system": Path(os.environ["SystemRoot"]).resolve()}
    evidence = []

    def inspect(value):
        if isinstance(value, (list, tuple)):
            if len(value) == 3 and isinstance(value[2], str) and value[2] in {"BINARY", "EXTENSION"}:
                name, source, kind = value
                source = Path(source).resolve()
                origin = next((label for label, root in roots.items()
                               if source.is_relative_to(root)), None)
                if origin is None:
                    raise ValueError(f"Unapproved binary origin: {name}")
                evidence.append({"file": name.replace("\\", "/"), "origin": origin,
                                 "source_relative": source.relative_to(roots[origin]).as_posix()})
            else:
                for item in value:
                    inspect(item)
    inspect(ast.literal_eval(Path(toc).read_text(encoding="utf-8")))
    if not evidence:
        raise ValueError("No binary provenance found")
    Path(output).write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    return evidence


if __name__ == "__main__":
    verify(Path(sys.argv[1]), Path(sys.argv[2]))
