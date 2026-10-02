import tempfile
import unittest
from pathlib import Path
from beamlib.files import install_files, restore_files


class FileTransactions(unittest.TestCase):
    def test_upgrade_uninstall_and_local_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            source,target,state=root/"source",root/"target",root/"state"
            source.write_bytes(b"v1"); target.write_bytes(b"original")
            install_files([(source,target)],state)
            source.write_bytes(b"v2")
            install_files([(source,target)],state)
            restore_files(state)
            self.assertEqual(target.read_bytes(),b"original")
            install_files([(source,target)],state)
            target.write_bytes(b"user")
            restore_files(state)
            self.assertEqual(target.read_bytes(),b"user")

    def test_partial_failure_restores_files_and_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            source,target,state=root/"source",root/"target",root/"state"
            source.write_bytes(b"new");target.write_bytes(b"old")
            with self.assertRaises(OSError):
                install_files([(source,target),(root/"missing",root/"other")],state)
            self.assertEqual(target.read_bytes(),b"old")
            self.assertFalse((state/"files.json").exists())
