import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import handoff  # noqa: E402


class HandoffTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["PR_GATE_HOME"] = self.tmp.name

    def tearDown(self):
        del os.environ["PR_GATE_HOME"]
        self.tmp.cleanup()

    def cli(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = handoff.main(list(args))
        return rc, json.loads(buf.getvalue())

    def test_send_names_file_like_state_and_redacts(self):
        body = Path(self.tmp.name) / "fix.md"
        body.write_text("pr-gate round 1/3\ntoken=ghp_" + "q" * 36 + "\n", encoding="utf-8")
        rc, out = self.cli("send", "o/my.repo", "7", "--sha", "abcdef1234", "--body-file", str(body))
        p = Path(out["inbox_file"])
        self.assertEqual(rc, 0)
        self.assertEqual(p.name, "o__my_repo__7.abcdef12.md")  # same key as state/
        self.assertNotIn("q" * 36, p.read_text(encoding="utf-8"))
        self.assertEqual(list(p.parent.glob("*.tmp")), [])

    def test_taken_and_resend_clears_it(self):
        p = handoff.send("o/r", 7, "abcdef1234", "fix it")
        self.assertEqual(self.cli("taken", "o/r", "7", "--sha", "abcdef1234")[1], {"taken": False})
        Path(str(p) + ".taken").write_text("", encoding="utf-8")  # what the mod writes
        self.assertEqual(self.cli("taken", "o/r", "7", "--sha", "abcdef1234")[1], {"taken": True})
        handoff.send("o/r", 7, "abcdef1234", "fix it again")
        self.assertFalse(handoff.taken("o/r", 7, "abcdef1234"))

    def test_rejects_bad_input(self):
        with self.assertRaises(SystemExit):
            handoff.main(["send", "o/r;rm", "7", "--sha", "abcdef12", "--body-file", "x"])
        with self.assertRaises(SystemExit):
            handoff.main(["taken", "o/r", "7", "--sha", "../../x"])


if __name__ == "__main__":
    unittest.main()
