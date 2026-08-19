import tempfile
import unittest
from pathlib import Path

from jarvis.rag import MemoryRagStore, UnsafePath, chunk_text, is_sensitive, resolve_allowed_path


class RagTests(unittest.TestCase):
    def test_path_escape_and_symlink_escape_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "allowed"
            root.mkdir()
            good = root / "file.txt"
            good.write_text("hello")
            outside = base / "outside.txt"
            outside.write_text("secret")
            self.assertEqual(resolve_allowed_path(good, (root,)), good.resolve())
            with self.assertRaises(UnsafePath):
                resolve_allowed_path(outside, (root,))
            link = root / "link.txt"
            link.symlink_to(outside)
            with self.assertRaises(UnsafePath):
                resolve_allowed_path(link, (root,))

    def test_sensitive_files_ignored(self):
        for path in [
            Path(".env"),
            Path("private.key"),
            Path("node_modules/x.js"),
            Path("client_secret.txt"),
        ]:
            self.assertTrue(is_sensitive(path), path)
        self.assertFalse(is_sensitive(Path("src/main.py")))

    def test_tenant_project_isolation_and_citations(self):
        store = MemoryRagStore()
        store.upsert("tenant-a", "project-1", chunk_text(Path("a.py"), "Tamil voice helper"))
        self.assertEqual(len(store.retrieve("tenant-a", "project-1", "voice")), 1)
        self.assertEqual(store.retrieve("tenant-b", "project-1", "voice"), [])
        self.assertEqual(store.retrieve("tenant-a", "project-2", "voice"), [])


if __name__ == "__main__":
    unittest.main()
