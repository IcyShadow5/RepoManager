"""User-authored local feedback and reviewable drafts for the public issue tracker."""
import json
import os
from pathlib import Path
import platform
import tempfile
from urllib.parse import urlencode
from uuid import uuid4

from . import store, version


CATEGORIES = {
    "positive": "Positive feedback",
    "improvement": "Improvement suggestion",
    "bug": "Bug report",
    "ui": "UI issue",
}
ISSUE_DESTINATION = "https://github.com/IcyShadow5/RepoManager/issues/new"
MAX_MESSAGE = 6000


def make_report(category, title, message, *, include_runtime=False, qt_version=""):
    if category not in CATEGORIES:
        raise ValueError("Choose a feedback category")
    title = title.strip()
    message = message.strip()
    if not message:
        raise ValueError("Describe your feedback before saving or opening a draft")
    if len(title) > 140 or len(message) > MAX_MESSAGE:
        raise ValueError("Use a title up to 140 characters and a message up to 6000 characters")
    report = {"category": category, "title": title or CATEGORIES[category],
              "message": message, "version": version.VERSION,
              "edition": "RepoManager Development"}
    if include_runtime:
        report["runtime"] = {"python": platform.python_version(),
                             "system": platform.system(), "qt": qt_version}
    return report


def markdown(report):
    text = (f"## {CATEGORIES[report['category']]}\n\n{report['message']}\n\n"
            f"### Application\n\n{report['edition']} {report['version']}\n")
    if "runtime" in report:
        runtime = report["runtime"]
        text += (f"\nPython: {runtime['python']}\nSystem: {runtime['system']}\n"
                 f"Qt: {runtime['qt']}\n")
    return text


def issue_url(report):
    return ISSUE_DESTINATION + "?" + urlencode({
        "title": f"[{CATEGORIES[report['category']]}] {report['title']}",
        "body": markdown(report),
    })


def save_report(report):
    directory = store.APP_DIR / "feedback"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{uuid4()}.json"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(report, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination
