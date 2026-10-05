"""Prepare the matching upstream source companion beside the portable binary."""
import hashlib
import json
from pathlib import Path
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from qt_runtime_licenses import source_notices


def prepare(sources: Path, archive: Path, metadata: Path) -> dict:
    _, manifest = source_notices(sources)
    lock = json.loads(Path(__file__).with_name('qt-source-lock.json').read_text(encoding='utf-8'))
    allowed = {'manifest.json', *(row['archive'] for row in manifest),
               *('additional-notices/' + row['file'] for row in lock['additional_notices'])}
    actual = {file.relative_to(sources).as_posix() for file in sources.rglob('*') if file.is_file()}
    if actual != allowed:
        raise ValueError('Third-party source directory contains missing or unreviewed files')
    records = []
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_STORED) as output:
        for file in sorted(sources.rglob('*')):
            if not file.is_file():
                continue
            relative = file.relative_to(sources).as_posix()
            data = file.read_bytes()
            output.writestr('THIRD_PARTY_SOURCES/' + relative, data)
            records.append({'file': relative, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    evidence = {'archive': archive.name, 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                'bytes': archive.stat().st_size, 'matching_upstream_sources': manifest,
                'files': records, 'publication_requirement': 'Distribute this source companion with the portable binary and retain notices.'}
    metadata.write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
    return evidence


if __name__ == '__main__':
    prepare(*(Path(value) for value in sys.argv[1:]))
