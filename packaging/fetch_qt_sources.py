"""Download the reviewed, hash-pinned upstream source/notice closure for a build."""
import hashlib
import json
from pathlib import Path
import sys
import urllib.request


def download(item, destination):
    if destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == item["sha256"]:
        return
    if destination.exists():
        raise ValueError(f"Existing file differs from the upstream lock: {destination.name}")
    request = urllib.request.Request(item["url"], headers={"User-Agent": "RepoManager-build"})
    data = urllib.request.urlopen(request, timeout=120).read()
    if hashlib.sha256(data).hexdigest() != item["sha256"]:
        raise ValueError(f"Upstream hash mismatch: {destination.name}")
    destination.write_bytes(data)


def fetch(destination):
    lock = json.loads(Path(__file__).with_name("qt-source-lock.json").read_text(encoding="utf-8"))
    destination.mkdir(parents=True, exist_ok=True)
    for item in lock["qt_sources"]:
        download(item, destination / item["archive"])
    notices = destination / "additional-notices"
    notices.mkdir(exist_ok=True)
    for item in lock["additional_notices"]:
        if item["file"] != "Microsoft-VC-Runtime.txt":
            download(item, notices / item["file"])
    # Preserve the vendor document; additionally produce readable plain text.
    import xml.etree.ElementTree as ET
    import zipfile
    with zipfile.ZipFile(notices / "Microsoft-VC-Runtime.docx") as archive:
        document = ET.fromstring(archive.read("word/document.xml"))
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    text = "\n".join("".join(t.text or "" for t in paragraph.findall(".//w:t", ns))
                     for paragraph in document.findall(".//w:p", ns))
    data = text.encode("utf-8")
    expected = next(item for item in lock["additional_notices"] if item["file"] == "Microsoft-VC-Runtime.txt")
    if hashlib.sha256(data).hexdigest() != expected["sha256"]:
        raise ValueError("Microsoft notice extraction changed")
    (notices / expected["file"]).write_bytes(data)
    (destination / "manifest.json").write_text(json.dumps(lock["qt_sources"], indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    fetch(Path(sys.argv[1]))
