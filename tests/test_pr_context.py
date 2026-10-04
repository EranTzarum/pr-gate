import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import pr_context  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


class MarkerTest(unittest.TestCase):
    def test_parses_marker(self):
        body = "Adds X.\n\n<!-- pr-gate:session=local_abc-123 host=claude-desktop -->\n"
        self.assertEqual(pr_context.parse_marker(body),
                         {"session": "local_abc-123", "host": "claude-desktop"})

    def test_no_marker_or_unsafe_id(self):
        self.assertIsNone(pr_context.parse_marker("plain body"))
        self.assertIsNone(pr_context.parse_marker(None))
        self.assertIsNone(pr_context.parse_marker("<!-- pr-gate:session=a;rm host=x -->"))


class CiStateTest(unittest.TestCase):
    def test_states(self):
        self.assertEqual(pr_context.ci_state([]), "none")
        self.assertEqual(pr_context.ci_state([{"bucket": "pass"}, {"bucket": "skipping"}]), "pass")
        self.assertEqual(pr_context.ci_state([{"bucket": "pass"}, {"bucket": "pending"}]), "pending")
        self.assertEqual(pr_context.ci_state([{"bucket": "fail"}, {"bucket": "pending"}]), "fail")
        self.assertEqual(pr_context.ci_state([{"bucket": "cancel"}]), "fail")

    def test_run_ids_from_links(self):
        checks = [{"bucket": "fail", "link": "https://github.com/o/r/actions/runs/123/job/9"},
                  {"bucket": "fail", "link": "https://github.com/o/r/actions/runs/123/job/10"},
                  {"bucket": "pass", "link": "https://github.com/o/r/actions/runs/456/job/1"},
                  {"bucket": "fail", "link": "https://vercel.com/x"}]
        self.assertEqual(pr_context.failed_run_ids(checks), ["123"])


class TriggerTest(unittest.TestCase):
    def test_deploy_on_main_detected(self):
        with tempfile.TemporaryDirectory() as d:
            wf = Path(d) / ".github" / "workflows"
            wf.mkdir(parents=True)
            (wf / "deploy.yml").write_text((FIX / "deploy.yml").read_text(), encoding="utf-8")
            (wf / "ci.yml").write_text((FIX / "ci.yml").read_text(), encoding="utf-8")
            out = pr_context.merge_triggers(Path(d), "main")
        self.assertEqual([t["file"] for t in out], ["deploy.yml"])
        self.assertTrue(any("db push" in s for s in out[0]["notable"]))

    def test_no_workflows(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(pr_context.merge_triggers(Path(d), "main"), [])


class DocsTest(unittest.TestCase):
    def test_finds_repo_docs(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name in ["CLAUDE.md", "AGENTS.md", "docs/ARCHITECTURE.md", "DOMAIN_MODEL.md", "notes.md"]:
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text("x", encoding="utf-8")
            docs = pr_context.repo_docs(root)
        self.assertEqual(docs, ["AGENTS.md", "CLAUDE.md", "DOMAIN_MODEL.md", "docs/ARCHITECTURE.md"])


class GatherTest(unittest.TestCase):
    """gather() end to end with gh/git mocked: shape of the context JSON."""

    def test_gather_with_failing_ci(self):
        view = json.loads((FIX / "pr_view.json").read_text())
        checks = [{"name": "test", "bucket": "fail", "state": "FAILURE",
                   "link": "https://github.com/o/r/actions/runs/77/job/1"}]
        calls = []

        def fake_run(args, cwd=None, check=True):
            calls.append(args)
            if args[:3] == ["gh", "pr", "view"]:
                return json.dumps(view)
            if args[:3] == ["gh", "pr", "checks"]:
                return json.dumps(checks)
            if args[:3] == ["gh", "pr", "diff"]:
                return "diff --git a/x b/x\n+token=ghp_" + "q" * 36 + "\n"
            if args[:3] == ["gh", "run", "view"]:
                return "step 1\nERROR password=supersecret1\n"
            return ""

        orig = pr_context.run
        pr_context.run = fake_run
        try:
            with tempfile.TemporaryDirectory() as d:
                ctx = pr_context.gather("o/r", 5, Path(d))
                diff = Path(ctx["diff_file"]).read_text(encoding="utf-8")
        finally:
            pr_context.run = orig
        self.assertEqual(ctx["ci"], "fail")
        self.assertEqual(ctx["head_sha"], view["headRefOid"])
        self.assertEqual(ctx["owner"], {"session": "local_s1", "host": "claude-desktop"})
        self.assertNotIn("supersecret1", ctx["failed_logs"])
        self.assertNotIn("q" * 36, diff)
        self.assertFalse(any("push" in a for a in calls if a[0] == "git"))


if __name__ == "__main__":
    unittest.main()
