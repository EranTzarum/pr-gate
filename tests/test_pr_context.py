import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import findings  # noqa: E402
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

    def test_risk_fix(self):
        self.assertEqual(pr_context.risk(["supabase/functions/a.ts"])["level"], "normal")
        self.assertEqual(pr_context.risk(["supabase/tests/001.test.sql"])["level"], "normal")
        self.assertEqual(pr_context.risk(["supabase/migrations/2026_x.sql"])["level"], "high")
        self.assertEqual(pr_context.risk(["db/migrations/1.py"])["level"], "high")
        self.assertEqual(pr_context.risk(["schema.sql"])["level"], "high")
        self.assertEqual(pr_context.risk(["Tests/seed.sql"])["level"], "normal")
        self.assertEqual(pr_context.risk(["TEST/x.sql"])["level"], "normal")
        self.assertEqual(pr_context.risk(["db/Migrations/1.sql"])["level"], "high")


class DeltaTest(unittest.TestCase):
    def test_first_round_when_no_state(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "checkout"
            work = Path(d) / "work"
            path, note = pr_context.delta_diff(root, None, "bbb", work, "o__r", 1)
            self.assertIsNone(path)
            self.assertEqual(note, "first round")

    def test_cat_file_failing_twice(self):
        calls = []

        def fake_run(args, cwd=None, check=True):
            calls.append(args)
            if "cat-file" in args:
                raise RuntimeError("fatal: not a valid object name")
            return ""

        orig = pr_context.run
        pr_context.run = fake_run
        try:
            with tempfile.TemporaryDirectory() as d:
                root = Path(d) / "checkout"
                work = Path(d) / "work"
                path, note = pr_context.delta_diff(root, "aaa", "bbb", work, "o__r", 1)
                self.assertIsNone(path)
                self.assertEqual(note, "last reviewed commit not found (force-push?)")
        finally:
            pr_context.run = orig

        self.assertEqual(len([c for c in calls if "cat-file" in c]), 2)
        self.assertEqual(len([c for c in calls if "fetch" in c]), 1)

    def test_delta_diff_with_prior_state(self):
        calls = []

        def fake_run(args, cwd=None, check=True):
            calls.append(args)
            if "diff" in args:
                return "diff --git a/x b/x\n+token=ghp_" + "q" * 36 + "\n"
            return ""

        orig = pr_context.run
        pr_context.run = fake_run
        try:
            with tempfile.TemporaryDirectory() as d:
                root = Path(d) / "checkout"
                work = Path(d) / "work"
                path, note = pr_context.delta_diff(root, "aaa", "bbb", work, "o__r", 1)
                self.assertIsNone(note)
                self.assertIsNotNone(path)
                self.assertTrue(Path(path).is_file())
                content = Path(path).read_text(encoding="utf-8")
                self.assertIn("diff --git", content)
                self.assertNotIn("q" * 36, content)
                self.assertTrue(any(a[-2:] == ["aaa", "bbb"] for a in calls if "diff" in a))
        finally:
            pr_context.run = orig


class DigestTest(unittest.TestCase):
    def test_digest(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "checkout"
            work = Path(d) / "work"
            root.mkdir()
            work.mkdir()
            (root / "CLAUDE.md").write_text(("c" * 19 + "\n") * 1000, encoding="utf-8")
            (root / "AGENTS.md").write_text(("a" * 19 + "\n") * 1000, encoding="utf-8")
            (root / "ARCHITECTURE.md").write_text(("b" * 19 + "\n") * 1000, encoding="utf-8")
            docs = ["CLAUDE.md", "ARCHITECTURE.md", "AGENTS.md"]
            path = pr_context.docs_digest(root, docs, work, "o__r", 1)
            text = Path(path).read_text(encoding="utf-8")

            # 1. AGENTS.md comes first
            self.assertTrue(text.startswith("### AGENTS.md\n"))

            # Parse sections:
            sections = {s.split("\n", 1)[0]: s.split("\n", 1)[1] for s in text.split("### ") if s.strip()}

            # 2. A 20000-char doc is cut to 15000 with the truncation line
            agents_body = sections["AGENTS.md"]
            agents_doc, agents_trunc = agents_body.split("[truncated: ")
            self.assertEqual(len(agents_doc), 15000)
            self.assertEqual(agents_trunc.strip(), "AGENTS.md has 5000 more chars; open it only if a finding depends on it]")

            # 3. Total never exceeds 40000 plus the truncation lines
            claude_doc, claude_trunc = sections["CLAUDE.md"].split("[truncated: ")
            arch_doc, arch_trunc = sections["ARCHITECTURE.md"].split("[truncated: ")
            self.assertEqual(len(claude_doc), 15000)
            self.assertEqual(len(arch_doc), 10000)
            self.assertEqual(len(agents_doc) + len(claude_doc) + len(arch_doc), 40000)
            self.assertEqual(arch_trunc.strip(), "ARCHITECTURE.md has 10000 more chars; open it only if a finding depends on it]")

    def test_digest_redacts(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "checkout"
            work = Path(d) / "work"
            root.mkdir()
            work.mkdir()
            (root / "AGENTS.md").write_text("token=ghp_" + "q" * 36, encoding="utf-8")
            path = pr_context.docs_digest(root, ["AGENTS.md"], work, "o__r", 1)
            text = Path(path).read_text(encoding="utf-8")
            self.assertNotIn("q" * 36, text)


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
                docs_exists = Path(ctx["docs_digest_file"]).is_file()
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
        self.assertIn("tier", ctx)
        self.assertEqual(ctx["tier"]["code_lines"], 0)
        self.assertIsNone(ctx["last_reviewed_sha"])
        self.assertIsNone(ctx["delta_diff_file"])
        self.assertEqual(ctx["delta_note"], "first round")
        self.assertTrue(docs_exists)

    def test_gather_with_prior_round(self):
        view = json.loads((FIX / "pr_view.json").read_text())
        view["headRefOid"] = "bbb"
        calls = []

        def fake_run(args, cwd=None, check=True):
            calls.append(args)
            if args[:3] == ["gh", "pr", "view"]:
                return json.dumps(view)
            if args[:3] == ["gh", "pr", "checks"]:
                return "[]"
            if args[:3] == ["gh", "pr", "diff"]:
                return "diff --git a/x b/x\n+new"
            if "diff" in args and "aaa" in args:
                return "diff --git a/x b/x\n+delta"
            return ""

        with tempfile.TemporaryDirectory() as td:
            gate_home = Path(td) / "gate_home"
            state_dir = gate_home / "state"
            state_dir.mkdir(parents=True)
            (state_dir / "o__r__5.json").write_text(
                json.dumps({"repo": "o/r", "pr": 5, "rounds": [{"sha": "aaa", "verdict": "fix"}]}),
                encoding="utf-8"
            )
            old_home = os.environ.get("PR_GATE_HOME")
            os.environ["PR_GATE_HOME"] = str(gate_home)
            orig = pr_context.run
            pr_context.run = fake_run
            try:
                work = Path(td) / "work"
                ctx = pr_context.gather("o/r", 5, work)
                delta_exists = Path(ctx["delta_diff_file"]).is_file()
                delta_content = Path(ctx["delta_diff_file"]).read_text(encoding="utf-8")
            finally:
                pr_context.run = orig
                if old_home is None:
                    os.environ.pop("PR_GATE_HOME", None)
                else:
                    os.environ["PR_GATE_HOME"] = old_home

        self.assertEqual(ctx["last_reviewed_sha"], "aaa")
        self.assertIsNotNone(ctx["delta_diff_file"])
        self.assertIsNone(ctx["delta_note"])
        self.assertTrue(delta_exists)
        self.assertIn("+delta", delta_content)
        self.assertIn("tier", ctx)


class BranchModeTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.home = Path(self.td.name) / "home"
        os.environ["PR_GATE_HOME"] = str(self.home)
        self.calls = []
        self.orig = pr_context.run
        pr_context.run = self.fake_run

    def tearDown(self):
        pr_context.run = self.orig
        os.environ.pop("PR_GATE_HOME", None)
        self.td.cleanup()

    def fake_run(self, args, cwd=None, check=True):
        self.calls.append(args)
        if args[:3] == ["gh", "pr", "view"]:
            return (FIX / "pr_view.json").read_text()
        if args[:3] == ["gh", "pr", "checks"]:
            return "[]"
        if args[:3] == ["gh", "pr", "diff"]:
            return "diff --git a/x b/x\n+new"
        if args[:1] != ["git"]:
            return ""
        rest = args[3:]
        if rest[:1] == ["rev-parse"]:
            return "c0ffee1234\n"
        if rest[:1] == ["merge-base"]:
            return "base999\n"
        if rest[:2] == ["diff", "--name-only"]:
            return "src/app.py\ndocs/a.md\n"
        if rest[:2] == ["diff", "--numstat"]:
            return "10\t2\tsrc/app.py\n-\t-\timg.png\n"
        if rest[:1] == ["diff"]:
            return "diff --git a/src/app.py b/src/app.py\n+key=ghp_" + "q" * 36 + "\n"
        if rest[:1] == ["log"]:
            return "Add export\nFix total\n"
        if rest[:1] == ["rev-list"]:
            return "2\n"
        return ""

    def test_branch_context_from_local_repo(self):
        ctx = pr_context.gather_branch("o/r", "feat/x", Path(self.td.name) / "work", src="C:/src/repo")
        self.assertEqual((ctx["mode"], ctx["pr"], ctx["branch"], ctx["ci"]), ("branch", "branch:feat/x", "feat/x", "none"))
        self.assertEqual(ctx["head_sha"], "c0ffee1234")
        self.assertEqual(ctx["files"], ["src/app.py", "docs/a.md"])
        self.assertEqual(ctx["size"], {"files": 2, "additions": 10, "deletions": 2})
        self.assertEqual(ctx["title"], "Add export")
        self.assertEqual(ctx["behind_base"], 2)
        self.assertNotIn("q" * 36, Path(ctx["diff_file"]).read_text(encoding="utf-8"))
        self.assertIn(["git", "-C", ctx["checkout"], "fetch", "--quiet", "C:/src/repo", "refs/heads/feat/x"], self.calls)
        self.assertFalse(any(a[:2] == ["gh", "pr"] for a in self.calls))
        self.assertTrue(ctx["context_file"].endswith("o__r__b-feat_x.json"))

    def test_bad_names_refused(self):
        for name in ("../x", "-x", "", "a b"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                pr_context.gather_branch("o/r", name, Path(self.td.name) / "work")
        with self.assertRaises(SystemExit):
            pr_context.main(["o/r", "0"])

    def test_pr_carries_over_branch_rounds(self):
        findings = pr_context.findings
        findings.record("o/r", "branch:feat/invoice-export", "aaa", "none", "fix", {},
                        [{"severity": "high", "file": "x", "line": 1, "title": "t"}])
        ctx = pr_context.gather("o/r", 5, Path(self.td.name) / "work")
        self.assertEqual(ctx["last_reviewed_sha"], "aaa")
        self.assertEqual([r["sha"] for r in ctx["branch_rounds"]], ["aaa"])
        self.assertEqual(findings.load("o/r", 5)["rounds"], [])  # the PR's own cap starts at 0


class TierTest(unittest.TestCase):
    def test_docs_and_tests_only(self):
        diff = ("+++ b/docs/a.md\n+doc line\n"
                "+++ b/tests/test_tool.py\n+test line\n"
                "+++ b/package-lock.json\n+lock line\n")
        t = pr_context.tier(["docs/a.md", "tests/test_tool.py", "package-lock.json"], diff)
        self.assertTrue(t["docs_only"])
        self.assertEqual(t["score"], 0)
        self.assertEqual(t["code_lines"], 0)
        self.assertEqual(t["why"], [])

    def test_added_code_categories(self):
        diff = ("+++ b/bin/tool.py\n"
                "+os.chmod(p, 0o600)\n"
                "+subprocess.run(cmd)\n")
        t = pr_context.tier(["bin/tool.py"], diff, {})
        self.assertFalse(t["docs_only"])
        self.assertEqual(t["score"], 4)
        self.assertEqual(t["code_lines"], 2)
        self.assertIn("secrets/perms+2", t["why"])
        self.assertIn("exec/shell+2", t["why"])

    def test_race_needs_word_boundary(self):
        quiet = pr_context.tier(["bin/tool.py"], "+++ b/bin/tool.py\n+print_traceback(err)\n", {})
        self.assertNotIn("concurrency+1", quiet["why"])
        loud = pr_context.tier(["bin/tool.py"], "+++ b/bin/tool.py\n+# avoid a race on save\n", {})
        self.assertIn("concurrency+1", loud["why"])

    def test_code_in_tests_ignored(self):
        diff = ("+++ b/tests/test_tool.py\n"
                "+os.chmod(p, 0o600)\n"
                "+subprocess.run(cmd)\n")
        t = pr_context.tier(["tests/test_tool.py"], diff, {})
        self.assertTrue(t["docs_only"])
        self.assertEqual(t["score"], 0)
        self.assertEqual(t["code_lines"], 0)
        self.assertEqual(t["why"], [])

    def test_migration_path_adds_risk(self):
        t = pr_context.tier(["supabase/migrations/001.sql"], "")
        self.assertFalse(t["docs_only"])
        self.assertIn("risk-path+3", t["why"])
        self.assertEqual(t["score"], 3)

    def test_big_code_lines_over_1500(self):
        diff = "+++ b/bin/tool.py\n" + "\n".join("+code_line" for _ in range(1600)) + "\n"
        t = pr_context.tier(["bin/tool.py"], diff, {})
        self.assertFalse(t["docs_only"])
        self.assertEqual(t["code_lines"], 1600)
        self.assertIn("big+1", t["why"])
        self.assertEqual(t["score"], 1)

    def test_hotspots(self):
        with tempfile.TemporaryDirectory() as td:
            old_home = os.environ.get("PR_GATE_HOME")
            os.environ["PR_GATE_HOME"] = td
            try:
                state_dir = Path(td) / "state"
                state_dir.mkdir(parents=True)
                # Two state files of the same repo with high findings in bin/tool.py
                (state_dir / "o__r__1.json").write_text(json.dumps({
                    "repo": "o/r", "pr": 1,
                    "rounds": [{"findings": [{"severity": "high", "file": "bin/tool.py"}]}]
                }), encoding="utf-8")
                (state_dir / "o__r__2.json").write_text(json.dumps({
                    "repo": "o/r", "pr": 2,
                    "rounds": [{"findings": [{"severity": "high", "file": "bin/tool.py"}]}]
                }), encoding="utf-8")
                # Excluded current PR
                (state_dir / "o__r__5.json").write_text(json.dumps({
                    "repo": "o/r", "pr": 5,
                    "rounds": [{"findings": [{"severity": "high", "file": "bin/tool.py"}]}]
                }), encoding="utf-8")
                # Another repo's state file
                (state_dir / "other__r__1.json").write_text(json.dumps({
                    "repo": "other/r", "pr": 1,
                    "rounds": [{"findings": [{"severity": "high", "file": "bin/tool.py"}]}]
                }), encoding="utf-8")
                # Corrupt file should be skipped without error
                (state_dir / "o__r__9.json").write_text("{corrupt", encoding="utf-8")

                hot = pr_context.hotspots("o/r", 5)
                self.assertEqual(hot.get("bin/tool.py"), 2)

                t = pr_context.tier(["bin/tool.py"], "", hot)
                self.assertIn("hotspot+1", t["why"])
                self.assertEqual(t["score"], 1)
            finally:
                if old_home is None:
                    os.environ.pop("PR_GATE_HOME", None)
                else:
                    os.environ["PR_GATE_HOME"] = old_home


if __name__ == "__main__":
    unittest.main()
