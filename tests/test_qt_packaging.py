"""Portable Qt collection and redistribution must fail closed on drift."""
import importlib.util
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]

def module(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'packaging'/(name+'.py'))
    value=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value

class QtPackagingTests(unittest.TestCase):
    def test_source_companion_isolated_entrypoint_rejects_missing_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / 'manifest.json').write_text('[]')
            archive = base / 'sources.zip'
            result = subprocess.run([sys.executable, '-I', str(ROOT/'packaging/distribution_sources.py'),
                str(base), str(archive), str(base/'metadata.json')], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 1)
            self.assertIn('Source archives differ from the reviewed upstream lock', result.stderr)
            self.assertNotIn('ModuleNotFoundError', result.stderr)
            self.assertFalse(archive.exists())

    def test_pe_imports_distinguish_bundled_windows_api_and_icu(self):
        closure = module('pe_import_closure')
        for name, expected in (('Qt6Core.dll', 'bundled'), ('kernel32.dll', 'windows-system'),
                               ('api-ms-win-core-file-l1-1-0.dll', 'windows-api-set'),
                               ('ICU.dll', 'windows-icu')):
            with self.subTest(name=name):
                self.assertEqual(closure.classify_import(name, {'qt6core.dll'}), expected)

    def test_unresolved_pe_import_and_copied_os_dll_fail_closed(self):
        closure = module('pe_import_closure')
        for name, bundled in (('foreign.dll', set()), ('icu.dll', {'icu.dll'}),
                              ('kernel32.dll', {'kernel32.dll'})):
            with self.subTest(name=name), self.assertRaises(ValueError):
                closure.classify_import(name, bundled)

    def test_unreviewed_translation_catalog_blocks_bundle(self):
        collector=module('qt_runtime_licenses')
        with tempfile.TemporaryDirectory() as folder:
            bundle=Path(folder);(bundle/'qtbase_de.qm').write_bytes(b'catalog')
            with self.assertRaisesRegex(ValueError,'translation catalogs'):
                collector.collect(bundle,bundle/'metadata.json',bundle/'sources',ROOT)
            self.assertFalse((bundle/'metadata.json').exists())

    def test_explicit_excluded_qml_import_blocks_collection(self):
        collector=module('qml_inventory')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'Main.qml').write_text('import QtQuick.Controls.Material\n')
            with self.assertRaisesRegex(ValueError,'Application imports'):
                collector.collect(root,root/'imports.json')

    def test_binary_origins_are_confined_and_redacted(self):
        collector=module('verify_binary_origins')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);toc=root/'Analysis.toc';target=root/'origins.json'
            toc.write_text(repr([('QtCore.pyd',str(root/'build/QtCore.pyd'),'EXTENSION'),('python.dll',str(root/'python/python.dll'),'BINARY'),('kernel.dll',str(root/'windows/System32/kernel.dll'),'BINARY')]))
            with mock.patch.object(collector.sys,'prefix',str(root/'build')),mock.patch.object(collector.sys,'base_prefix',str(root/'python')),mock.patch.dict(collector.os.environ,{'SystemRoot':str(root/'windows')}):
                evidence=collector.verify(toc,target)
            self.assertEqual({row['origin'] for row in evidence},{'build-environment','python-runtime','windows-system'})
            self.assertNotIn(str(root),target.read_text())

    def test_foreign_dll_blocks_packaging_without_output(self):
        collector=module('verify_binary_origins')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);toc=root/'Analysis.toc';target=root/'origins.json'
            toc.write_text(repr([('icuuc.dll',str(root/'foreign-tool/icuuc.dll'),'BINARY')]))
            with self.assertRaisesRegex(ValueError,'Unapproved binary origin'):
                collector.verify(toc,target)
            self.assertFalse(target.exists())

    def test_missing_binary_provenance_blocks_packaging(self):
        collector=module('verify_binary_origins')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);toc=root/'Analysis.toc';target=root/'origins.json';toc.write_text(repr([('module','path','PYMODULE')]))
            with self.assertRaisesRegex(ValueError,'No binary provenance'):
                collector.verify(toc,target)
            self.assertFalse(target.exists())

    def test_identity_is_stable(self):
        from repo_manager.version import VERSION, is_prerelease
        self.assertEqual(VERSION, "0.1.4")
        self.assertFalse(is_prerelease())
        self.assertNotEqual(VERSION,'0.1.1')

    def test_probe_is_unavailable_for_stable_and_invalid_versions(self):
        from repo_manager import version
        for value, expected in (("0.1.2", False), ("0.1.2-dev", True),
                                ("0.1.2-rc.1", True), ("0.1.2-rc.0", False),
                                ("0.1.2-rc.1-extra", False)):
            with self.subTest(version=value), mock.patch.object(version, "VERSION", value):
                self.assertEqual(version.is_prerelease(), expected)

    def test_qml_scanner_collects_application_closure(self):
        collector=module('qml_inventory')
        with tempfile.TemporaryDirectory() as folder:
            file=Path(folder)/'imports.json'
            imports=collector.collect(ROOT/'repo_manager/qml',file)
            names={item['name'] for item in imports}
            self.assertTrue({'QtQuick','QtQml','QtQuick.Controls.Basic','QtQuick.Layouts','QtQuick.Dialogs'}.issubset(names))
            self.assertFalse(any('Quick3D' in name or 'VirtualKeyboard' in name or name.startswith('QtQuick.Controls.Material') for name in names))
            self.assertNotIn('C:',file.read_text())

    def test_unresolved_qml_module_blocks_collection(self):
        collector=module('qml_inventory')
        result=mock.Mock(stdout=json.dumps([{'name':'Absent.Module','type':'module'}]),stderr='')
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(collector.subprocess,'run',return_value=result):
            target=Path(folder)/'inventory.json'
            with self.assertRaisesRegex(ValueError,'Unresolved'):
                collector.collect(ROOT/'repo_manager/qml',target)
            self.assertFalse(target.exists())

    def test_qml_scanner_warning_is_not_ignored(self):
        collector=module('qml_inventory')
        with mock.patch.object(collector.subprocess,'run',return_value=mock.Mock(stdout='[]',stderr='missing dependency')):
            with self.assertRaisesRegex(ValueError,'warning'):
                collector.collect(ROOT/'repo_manager/qml',Path('not-written.json'))

    def test_missing_source_closure_fails_before_bundle_write(self):
        collector=module('qt_runtime_licenses')
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);(base/'manifest.json').write_text('[]')
            with self.assertRaisesRegex(ValueError,'reviewed upstream lock'):
                collector.collect(base/'bundle',base/'metadata.json',base,ROOT)
            self.assertFalse((base/'bundle').exists())
            self.assertFalse((base/'metadata.json').exists())

    def test_existing_source_archive_is_never_overwritten(self):
        collector=module('fetch_qt_sources')
        with tempfile.TemporaryDirectory() as folder:
            file=Path(folder)/'source.tar.gz';file.write_bytes(b'owner content')
            with mock.patch.object(collector.urllib.request,'urlopen') as network:
                with self.assertRaisesRegex(ValueError,'Existing file differs'):
                    collector.download({'url':'https://example.invalid','sha256':'0'*64},file)
                network.assert_not_called()
            self.assertEqual(file.read_bytes(),b'owner content')

    def test_source_download_requires_reviewed_hash(self):
        collector=module('fetch_qt_sources')
        response=mock.MagicMock();response.read.return_value=b'changed upstream archive'
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(collector.urllib.request,'urlopen',return_value=response):
            file=Path(folder)/'source.tar.gz'
            with self.assertRaisesRegex(ValueError,'Upstream hash mismatch'):
                collector.download({'url':'https://example.invalid','sha256':'0'*64},file)
            self.assertFalse(file.exists())

if __name__=='__main__':
    unittest.main()
