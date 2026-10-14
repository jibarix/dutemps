"""The leak check (scripts/check_private.py): file rules, text decoding, and
what it reads from a real git index.

    python -m unittest discover -s tests -v
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(HERE, "scripts", "check_private.py")
spec = importlib.util.spec_from_file_location("check_private", SCRIPT)
cp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cp)


class Rules(unittest.TestCase):
    def test_blocked_paths(self):
        for p in ("data/x.db", "local/notes.md", ".env", ".env.local",
                  "app/.env.production", "a.pdf", "b.xlsx", "x.sqlite3",
                  "scratch/x.json", "backups/invoice_profile-1.json",
                  "imports/pull_2026-10-14.json", "invoice_profile.json"):
            self.assertTrue(cp.BLOCKED.search(p), p)
        self.assertIn("examples/invoice_profile.json", cp.ALLOWED)
        for p in ("README.md", "dutemps/db.py", "environment.md", "static/app.js"):
            self.assertFalse(cp.BLOCKED.search(p), p)

    def test_text_decoding(self):
        self.assertEqual(cp.text("Café".encode()), "Café")
        self.assertEqual(cp.text("secret".encode("utf-16")), "secret")
        self.assertEqual(cp.text("secret".encode("utf-32")), "secret")
        self.assertEqual(cp.text(b"\xef\xbb\xbfhi"), "hi")
        self.assertIsNone(cp.text("secret".encode("utf-16-le")))   # no BOM
        self.assertIsNone(cp.text(b"\x89PNG\r\n\x1a\n\0\0"))
        self.assertIsNone(cp.text("Caf\xe9".encode("cp1252")))


@unittest.skipUnless(shutil.which("git"), "git not installed")
class Index(unittest.TestCase):
    """A throwaway repository with its own copy of the script."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        os.makedirs(os.path.join(self.dir, "scripts"))
        os.makedirs(os.path.join(self.dir, "local"))
        shutil.copy(SCRIPT, os.path.join(self.dir, "scripts"))
        self.write("local/private-terms.txt", b"Zorblax\n")
        self.git("init", "-q")

    def write(self, rel, data):
        with open(os.path.join(self.dir, rel), "wb") as f:
            f.write(data)

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.dir, check=True, capture_output=True)

    def check(self, *args):
        return subprocess.run([sys.executable, "scripts/check_private.py", *args],
                              cwd=self.dir, capture_output=True).returncode

    def test_staged_text_is_checked_even_after_the_working_copy_is_scrubbed(self):
        self.write("notes.txt", b"call Zorblax\n")
        self.git("add", "notes.txt")
        self.write("notes.txt", b"call someone\n")
        self.assertEqual(self.check(), 1)
        self.assertEqual(self.check("--all"), 1)

    def test_utf16_and_binary_files(self):
        self.write("notes.txt", "call Zorblax\n".encode("utf-16"))
        self.git("add", "notes.txt")
        self.assertEqual(self.check(), 1)
        self.write("notes.txt", "call someone\n".encode("utf-16"))
        self.git("add", "notes.txt")
        self.assertEqual(self.check(), 0)
        self.write("blob.bin", b"\0\1Zorblax")
        self.git("add", "blob.bin")
        self.assertEqual(self.check(), 1)

    def test_env_variants_are_refused(self):
        self.write(".env.local", b"KEY=1\n")
        self.git("add", "-f", ".env.local")
        self.assertEqual(self.check(), 1)


if __name__ == "__main__":
    unittest.main()
