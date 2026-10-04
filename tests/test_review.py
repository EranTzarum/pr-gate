import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import review  # noqa: E402


def make_ctx(d, ci="fail"):
    d = Path(d)
    co = d / "checkout"
    (co / "src").mkdir(parents=True)
    (co / "src" / "export.ts").write_text("a\nb\nc\n", encoding="utf-8")
    (d / "pr.diff").write_text("+const x = 1\n", encoding="utf-8")
    ctx = {"repo": "o/r", "pr": 5, "title": "Add export", "body": "Adds export.", "head": "feat/x",
           "base": "main", "head_sha": "abcdef1234", "size": {"files": 1, "additions": 1, "deletions": 0},
           "files": ["src/export.ts"], "ci": ci, "failed_logs": "TypeError at export.ts:2",
           "docs": ["CLAUDE.md"], "diff_file": str(d / "pr.diff"), "checkout": str(co)}
    p = d / "o__r__5.json"
    p.write_text(json.dumps(ctx), encoding="utf-8")
    return p, ctx


class PromptTest(unittest.TestCase):
    def test_all_placeholders_filled(self):
        with tempfile.TemporaryDirectory() as d:
            _, ctx = make_ctx(d)
            text = review.build_prompt(ctx)
        self.assertNotIn("{{", text)
        for s in ("Add export", "- CLAUDE.md", "CI FAILED", "TypeError at export.ts:2", "+const x = 1"):
            self.assertIn(s, text)


class ArgvTest(unittest.TestCase):
    def test_engines_are_read_only(self):
        codex = review.engine_argv("codex", "C:/co")
        self.assertIn("read-only", codex)
        claude = review.engine_argv("claude", "C:/co")
        self.assertEqual(claude[claude.index("--permission-mode") + 1], "plan")
        self.assertIn("--max-budget-usd", claude)
        cursor = review.engine_argv("cursor", "C:/co")
        self.assertEqual(cursor[cursor.index("--mode") + 1], "ask")
        for argv in (codex, claude, cursor):
            self.assertFalse({"--force", "--yolo", "acceptEdits", "--dangerously-bypass-approvals-and-sandbox"}
                             & set(argv))


class RawGateTest(unittest.TestCase):
    def test_raw_output_is_gated(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["PR_GATE_HOME"] = d
            try:
                ctx_file, _ = make_ctx(d, ci="pass")
                raw = Path(d) / "raw.out"
                raw.write_text("prose\n```json\n" + json.dumps({"summary": "s", "findings": [
                    {"severity": "medium", "file": "src/export.ts", "line": 2, "title": "t",
                     "scenario": "An empty invoice list makes the export write a header-only file and report success.",
                     "fix": "return 204"}]}) + "\n```", encoding="utf-8")
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = review.main([str(ctx_file), "--raw", str(raw)])
            finally:
                del os.environ["PR_GATE_HOME"]
        out = json.loads(buf.getvalue())
        self.assertEqual(rc, 0)
        self.assertEqual((out["verdict"], out["action"], out["round"]), ("fix", "send-fix", 1))


if __name__ == "__main__":
    unittest.main()
