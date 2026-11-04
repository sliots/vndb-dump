import gzip
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('sync', Path(__file__).resolve().parents[1] / 'scripts/sync.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class SyncTests(unittest.TestCase):
    def test_fingerprint_ignores_order_and_metadata_but_detects_content(self):
        records = [{'name': 'a', 'sha256': 'one', 'retrieved_at': 'old'}, {'name': 'b', 'sha256': 'two'}]
        changed_metadata = [dict(records[1]), dict(records[0], retrieved_at='new')]
        self.assertEqual(sync.fingerprint(records), sync.fingerprint(changed_metadata))
        changed_metadata[0]['sha256'] = 'different'
        self.assertNotEqual(sync.fingerprint(records), sync.fingerprint(changed_metadata))

    def test_split_is_lossless_and_below_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'dump'
            path.write_bytes(bytes(range(100)))
            parts = sync.split_asset(path, limit=31)
            self.assertEqual(b''.join(p.read_bytes() for p in parts), path.read_bytes())
            self.assertTrue(all(p.stat().st_size <= 31 for p in parts))
            self.assertEqual([p.name for p in parts], ['dump.part001', 'dump.part002', 'dump.part003', 'dump.part004'])

    def test_reject_corrupt_gzip_and_wrong_json(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'tags.json.gz'
            path.write_bytes(gzip.compress(b'[{"id":1}]'))
            sync.validate(path)
            path.write_bytes(gzip.compress(b'{}'))
            with self.assertRaises(ValueError):
                sync.validate(path)
            path.write_bytes(gzip.compress(b'[1]')[:-4])
            with self.assertRaises((EOFError, OSError)):
                sync.validate(path)

    def test_auth_failure_is_not_mistaken_for_missing_release(self):
        with patch.dict(os.environ, GH_REPO='owner/repo'), patch.object(sync.subprocess, 'run') as run:
            run.return_value = subprocess.CompletedProcess([], 1, '', 'Bad credentials (HTTP 401)')
            with self.assertRaises(RuntimeError):
                sync.get_release('tag')
            run.return_value = subprocess.CompletedProcess([], 1, '', 'Not Found (HTTP 404)')
            self.assertIsNone(sync.get_release('tag'))

    def test_uploaded_size_digest_and_set_are_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'dump'
            path.write_bytes(b'data')
            asset = {'name': 'dump', 'state': 'uploaded', 'size': 4, 'digest': 'sha256:' + sync.sha256(path)}
            sync.verify_assets({'assets': [asset]}, [path])
            for invalid in (dict(asset, size=3), dict(asset, digest='sha256:bad'), dict(asset, state='starter')):
                with self.assertRaises(ValueError):
                    sync.verify_assets({'assets': [invalid]}, [path])
            with self.assertRaises(ValueError):
                sync.verify_assets({'assets': []}, [path])

    def test_upload_failure_leaves_draft_unpublished(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            path = directory / 'dump'
            path.write_bytes(b'data')
            (directory / 'manifest.json').write_text(json.dumps({'snapshot_date': '2026-11-04'}))
            with patch.dict(os.environ, GITHUB_SHA='abc'), patch.object(sync, 'get_release', return_value=None), patch.object(sync, 'run') as run:
                run.side_effect = [None, subprocess.CalledProcessError(1, 'gh')]
                with self.assertRaises(subprocess.CalledProcessError):
                    sync.publish('tag', directory, [path], 'notes')
                self.assertEqual(run.call_args_list[0].args[:3], ('gh', 'release', 'create'))
                self.assertFalse(any(call.args[:3] == ('gh', 'release', 'edit') for call in run.call_args_list))

    def test_existing_draft_is_repaired_before_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            path = directory / 'dump'
            path.write_bytes(b'data')
            release = {'draft': True, 'assets': [{'name': 'dump', 'state': 'uploaded', 'size': 4}], 'html_url': 'https://example.test/release'}
            with patch.object(sync, 'get_release', return_value=release), patch.object(sync, 'run') as run:
                sync.publish('tag', directory, [path], 'notes')
                self.assertEqual(run.call_args_list[0].args[:3], ('gh', 'release', 'upload'))
                self.assertEqual(run.call_args_list[-1].args[:3], ('gh', 'release', 'edit'))


if __name__ == '__main__':
    unittest.main()
