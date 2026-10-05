import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from repo_manager import instance_lock, store


@unittest.skipUnless(os.name == "nt", "Windows single-instance lock")
class InstanceLockTests(unittest.TestCase):
    def test_competing_process_cannot_truncate_or_replace_owner_pid(self):
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(store, "APP_DIR", Path(folder)), mock.patch.object(instance_lock, "_handle", None):
                try:
                    self.assertTrue(instance_lock.acquire())
                    self.assertTrue(instance_lock.acquire())
                    command = "import sys; from pathlib import Path; from repo_manager import store,instance_lock; store.APP_DIR=Path(sys.argv[1]); print(instance_lock.acquire())"
                    child = subprocess.run([sys.executable, "-c", command, folder], capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
                    self.assertEqual(child.returncode, 0, child.stderr)
                    self.assertEqual(child.stdout.strip(), "False")
                    instance_lock._handle.seek(0)
                    self.assertEqual(instance_lock._handle.read(), str(os.getpid()))
                finally:
                    if instance_lock._handle is not None:
                        instance_lock._handle.close()


if __name__ == "__main__":
    unittest.main()
