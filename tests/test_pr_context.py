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

    def write_wf(self, d, name, text):
        wf = Path(d) / ".github" / "workflows"
        wf.mkdir(parents=True, exist_ok=True)
        (wf / name).write_text(text, encoding="utf-8")

    def test_paths_filter_skips_docs_only_pr(self):
        wf = ("on:\n  push:\n    branches: [main]\n    paths: ['supabase/migrations/**', 'supabase/functions/**']\n"
              "jobs:\n  d:\n    steps:\n      - run: supabase db push\n")
        with tempfile.TemporaryDirectory() as d:
            self.write_wf(d, "deploy.yml", wf)
            self.assertEqual(pr_context.merge_triggers(Path(d), "main", ["docs/LOG.md", "package.json"]), [])
            hit = pr_context.merge_triggers(Path(d), "main", ["supabase/migrations/2026_x.sql"])
        self.assertEqual([t["file"] for t in hit], ["deploy.yml"])

    def test_paths_ignore_and_dash_lists(self):
        wf = "on:\n  push:\n    branches:\n      - main\n    paths-ignore:\n      - 'docs/**'\n      - '*.md'\n"
        with tempfile.TemporaryDirectory() as d:
            self.write_wf(d, "ci.yml", wf)
            self.assertEqual(pr_context.merge_triggers(Path(d), "main", ["docs/a.md", "README.md"]), [])
            self.assertEqual(len(pr_context.merge_triggers(Path(d), "main", ["src/app.ts"])), 1)

    def test_local_test_stack_is_not_notable(self):
        wf = ("on:\n  push:\n    branches: [main]\njobs:\n  t:\n    steps:\n"
              "      - run: supabase db start\n      - run: supabase test db\n      - run: supabase functions deploy x\n")
        with tempfile.TemporaryDirectory() as d:
            self.write_wf(d, "ci.yml", wf)
            notable = pr_context.merge_triggers(Path(d), "main", ["src/a.ts"])[0]["notable"]
        self.assertEqual(notable, ["- run: supabase functions deploy x"])

    def test_glob(self):
        self.assertTrue(pr_context.glob_match("supabase/migrations/a/b.sql", "supabase/migrations/**"))
        self.assertFalse(pr_context.glob_match("docs/a/b.md", "docs/*.md"))
        self.assertTrue(pr_context.glob_match("README.md", "*.md"))


class FailureExcerptTest(unittest.TestCase):
    def test_excerpt_finds_error_not_cleanup_tail(self):
        lines = ["job\tstep\t2026-10-06T16:21:14.0837219Z setup line %d" % i for i in range(50)]
        lines += ["job\tstep\t2026-10-06T16:21:15.0Z FAIL: test_identity (TestWorktree)",
                  "job\tstep\t2026-10-06T16:21:15.1Z AssertionError: False is not true"]
        lines += ["job\tstep\t2026-10-06T16:21:16.0Z Cleaning up orphan processes %d" % i for i in range(150)]
        out = pr_context.extract_failure("\n".join(lines))
        self.assertIn("FAIL: test_identity", out)
        self.assertIn("AssertionError", out)
        self.assertNotIn("2026-10-06T", out)  # timestamps stripped
        self.assertLess(out.count("Cleaning up"), 6)

    def test_no_hits_falls_back_to_tail(self):
        self.assertEqual(pr_context.extract_failure("a\nb\nc"), "a\nb\nc")


class DocsTest(unittest.TestCase):
    def test_finds_repo_docs(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name in ["CLAUDE.md", "AGENTS.md", "docs/ARCHITECTURE.md", "DOMAIN_MODEL.md", "notes.md"]:
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text(name, encoding="utf-8")
            docs = pr_context.repo_docs(root)
        self.assertEqual(docs, ["AGENTS.md", "CLAUDE.md", "DOMAIN_MODEL.md", "docs/ARCHITECTURE.md"])

    def test_identical_copies_read_once(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "docs").mkdir()
            (root / "ARCHITECTURE.md").write_text("same", encoding="utf-8")
            (root / "docs" / "ARCHITECTURE.md").write_text("same", encoding="utf-8")
            self.assertEqual(pr_context.repo_docs(root), ["ARCHITECTURE.md"])

    def test_merge_rules_found(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "CLAUDE.md").write_text(
                "# Repo\n\nIf your session did not start here you are READ-ONLY.\n"
                "- No commits, merges, branches\nOther text\n"
                "- `../references/` is **read-only**. Never modify it.\n"
                "- `review.py`: runs a read-only engine or gates a subagent's output.\n", encoding="utf-8")
            rules = pr_context.merge_rules(root, ["CLAUDE.md"])
        # neither the folder note nor "read-only ... subagent" (live run 6) is a merge rule
        self.assertEqual([r.split(":")[1] for r in rules], ["3", "4"])


class RiskTest(unittest.TestCase):
    def test_categories(self):
        cases = {"supabase/migrations/0007_add_col.sql": "migration", "db/schema.sql": "migration",
                 "src/auth/session.ts": "auth", "policies/rls_orders.sql": "auth",
                 ".github/workflows/deploy.yml": "workflow", "src/billing/charge.py": "payment"}
        for path, cat in cases.items():
            r = pr_context.risk([path])
            self.assertEqual(r["level"], "high", path)
            self.assertIn(f"{cat}: {path}", r["why"])

    def test_normal_and_cap(self):
        self.assertEqual(pr_context.risk(["src/export.ts", "README.md", "docs/author.md"]),
                         {"level": "normal", "why": []})
        self.assertEqual(len(pr_context.risk([f"m/migrations/{i}.sql" for i in range(9)])["why"]), 6)


class GatherTest(unittest.TestCase):
    """gather() end to end with gh/git mocked: shape of the context JSON."""

    def test_gather_with_failing_ci(self):
        view = json.loads((FIX / "pr_view.json").read_text())
        checks = [{"name": "test", "bucket": "fail", "state": "FAILURE", "workflow": "CI",
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
            if args[:3] == ["gh", "run", "list"]:
                return json.dumps([{"conclusion": "failure", "headSha": "abcdef123456", "createdAt": "t"}])
            if args[:2] == ["gh", "api"] and "/compare/" in args[2]:
                return "3\n"
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
        self.assertEqual(ctx["base_ci"], {"CI": {"conclusion": "failure", "sha": "abcdef12", "at": "t"}})
        self.assertEqual(ctx["behind_base"], 3)
        self.assertEqual(ctx["merge_rules"], [])
        self.assertEqual(ctx["risk"], {"level": "normal", "why": []})


if __name__ == "__main__":
    unittest.main()
