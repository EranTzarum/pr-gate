import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

WORKTREE = Path(__file__).resolve().parents[1]

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
        with tempfile.TemporaryDirectory(dir=WORKTREE) as d:
            os.environ["PR_GATE_HOME"] = d
            try:
                _, ctx = make_ctx(d)
                text = review.build_prompt(ctx)
            finally:
                del os.environ["PR_GATE_HOME"]
        self.assertNotIn("{{", text)
        self.assertIn("none (first round)", text)
        # live: factory#37 burned 3 rounds on siblings of one root cause
        self.assertIn("One root cause, one finding", text)
        for s in ("Add export", "- CLAUDE.md", "CI FAILED", "TypeError at export.ts:2", "+const x = 1"):
            self.assertIn(s, text)

    def test_base_ci_told_to_reviewer(self):
        with tempfile.TemporaryDirectory(dir=WORKTREE) as d:
            os.environ["PR_GATE_HOME"] = d
            try:
                _, ctx = make_ctx(d)
                ctx.update(base_ci={"CI": {"conclusion": "failure", "sha": "abc12345"}}, behind_base=0)
                text = review.build_prompt(ctx)
            finally:
                del os.environ["PR_GATE_HOME"]
        self.assertIn("CI failure @ abc12345", text)
        self.assertIn("the PR did not cause it", text)


class EngineDefaultsTest(unittest.TestCase):
    def setUp(self):
        home = tempfile.TemporaryDirectory(dir=WORKTREE)
        self.addCleanup(home.cleanup)
        env = patch.dict(os.environ, PR_GATE_HOME=home.name)
        env.start()
        self.addCleanup(env.stop)

    def test_defaults_are_explicit_mid_tier(self):
        seen = {}

        def fake_run(engine, model, prompt, cwd, effort=None, timeout_s=None, meta=None):
            if meta is not None:
                meta.update(seconds=1.5, engine=engine, model=model, effort=effort)
            seen[engine] = (model, effort)
            return 0, "{}"

        orig = review.lean_run.run
        review.lean_run.run = fake_run
        try:
            with tempfile.TemporaryDirectory(dir=WORKTREE) as d:
                _, ctx = make_ctx(d)
                for engine in ("codex", "claude", "cursor"):
                    review.run_engine(engine, ctx, "p", Path(d) / f"{engine}.out")
                review.run_engine("codex", ctx, "p", Path(d) / "x.out", model="gpt-6-luna", effort="medium")
        finally:
            review.lean_run.run = orig
        self.assertEqual(seen["claude"], ("sonnet", None))
        self.assertEqual(seen["cursor"], ("composer-2.5", None))
        self.assertEqual(seen["codex"], ("gpt-6-luna", "medium"))  # override wins

    def test_timeout_is_reported(self):
        orig = review.lean_run.run
        review.lean_run.run = lambda *a, **k: (3, "")
        try:
            with tempfile.TemporaryDirectory(dir=WORKTREE) as d:
                _, ctx = make_ctx(d)
                with self.assertRaises(RuntimeError) as cm:
                    review.run_engine("cursor", ctx, "p", Path(d) / "o.out")
        finally:
            review.lean_run.run = orig
        self.assertIn("killed", str(cm.exception))


def finding(line=2, severity="medium", title="t"):
    return {"severity": severity, "file": "src/export.ts", "line": line, "title": title,
            "scenario": "An empty invoice list makes the export write a header-only file and report success.",
            "fix": "return 204"}


class RiskTest(unittest.TestCase):
    def test_high_risk_escalates_unless_flags_given(self):
        hi = {"risk": {"level": "high", "why": ["migration: a.sql"]}}
        self.assertEqual(review.pick_model("codex", hi), ("gpt-6.1-sol", "medium"))
        self.assertEqual(review.pick_model("claude", hi), ("opus", None))
        self.assertEqual(review.pick_model("codex", {}), ("gpt-6.1-sol", "low"))
        self.assertEqual(review.pick_model("codex", hi, effort="low"), ("gpt-6.1-sol", "low"))
        self.assertEqual(review.pick_model("claude", hi, model="sonnet"), ("sonnet", None))

    def test_user_config_wins_over_defaults(self):
        cfg = {"models": {"cursor": "auto"}, "effort": {"codex": "medium"}}
        self.assertEqual(review.pick_model("cursor", {}, cfg=cfg), ("auto", None))
        self.assertEqual(review.pick_model("codex", {}, cfg=cfg), ("gpt-6.1-sol", "medium"))
        hi = {"risk": {"level": "high", "why": ["auth: a"]}}
        self.assertEqual(review.pick_model("cursor", hi, cfg=cfg), ("auto", None))  # no stronger cursor tier: keep theirs
        self.assertEqual(review.pick_model("claude", hi, cfg=cfg), ("opus", None))
        self.assertEqual(review.pick_model("cursor", {}, model="composer-2.5", cfg=cfg), ("composer-2.5", None))

    def test_config_file_sets_default_engine(self):
        seen = {}

        def fake_run(engine, model, prompt, cwd, effort=None, timeout_s=None, meta=None):
            if meta is not None:
                meta.update(seconds=1.5, engine=engine, model=model, effort=effort)
            seen["call"] = (engine, model)
            return 0, json.dumps({"summary": "s", "findings": []})

        with tempfile.TemporaryDirectory(dir=WORKTREE) as d:
            ctx_file, _ = make_ctx(d, ci="pass")
            Path(d, "config.json").write_text('{"reviewer": "cursor", "models": {"cursor": "auto"}}', encoding="utf-8")
            orig = review.lean_run.run
            review.lean_run.run = fake_run
            os.environ["PR_GATE_HOME"] = d
            try:
                with redirect_stdout(io.StringIO()):
                    rc = review.main([str(ctx_file)])
                Path(d, "config.json").write_text("{not json", encoding="utf-8")
                self.assertEqual(review.load_config(), {})  # broken file: defaults, never fatal
            finally:
                review.lean_run.run = orig
                del os.environ["PR_GATE_HOME"]
        self.assertEqual((rc, seen["call"]), (0, ("cursor", "auto")))

    def test_risk_in_prompt(self):
        with tempfile.TemporaryDirectory(dir=WORKTREE) as d:
            os.environ["PR_GATE_HOME"] = d
            try:
                _, ctx = make_ctx(d)
                ctx["risk"] = {"level": "high", "why": ["auth: src/auth/x.ts"]}
                text = review.build_prompt(ctx)
            finally:
                del os.environ["PR_GATE_HOME"]
        self.assertIn("High-risk PR. Touches: auth: src/auth/x.ts", text)


class BothTest(unittest.TestCase):
    def run_main(self, d, outputs, extra_ctx=None):
        def fake_run(engine, model, prompt, cwd, effort=None, timeout_s=None, meta=None):
            if meta is not None:
                meta.update(seconds=1.5, engine=engine, model=model, effort=effort)
            return outputs[engine]

        ctx_file, ctx = make_ctx(d, ci="pass")
        if extra_ctx:
            ctx_file.write_text(json.dumps(dict(ctx, **extra_ctx)), encoding="utf-8")
        orig = review.lean_run.run
        review.lean_run.run = fake_run
        os.environ["PR_GATE_HOME"] = d
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = review.main([str(ctx_file), "--engine", "both"])
        finally:
            review.lean_run.run = orig
            del os.environ["PR_GATE_HOME"]
        return rc, json.loads(buf.getvalue())

    def test_merges_and_drops_cross_engine_duplicate(self):
        codex = json.dumps({"summary": "s", "findings": [finding(2, "medium", "empty list")]})
        claude = json.dumps({"summary": "longer summary", "findings": [
            finding(3, "high", "header-only export"), finding(1, "low", "other")]})
        with tempfile.TemporaryDirectory(dir=WORKTREE) as d:
            rc, out = self.run_main(d, {"codex": (0, codex), "claude": (0, claude)},
                                    {"risk": {"level": "high", "why": ["payment: x"]}})
        self.assertEqual(rc, 0)
        # line 2 (codex) and line 3 (claude) are one issue: the high one stays
        self.assertEqual([(k["line"], k["severity"], k["engine"]) for k in out["kept"]],
                         [(3, "high", "claude"), (1, "low", "claude")])  # same-engine neighbour stays
        self.assertEqual(out["dropped"][0]["reason"], "duplicate")
        self.assertEqual(out["summary"], "longer summary")
        self.assertEqual(out["escalated"], ["payment: x"])

    def test_same_engine_neighbours_both_kept(self):
        codex = json.dumps({"summary": "s", "findings": [finding(1, "medium", "a"), finding(2, "medium", "b")]})
        with tempfile.TemporaryDirectory(dir=WORKTREE) as d:
            rc, out = self.run_main(d, {"codex": (0, codex), "claude": (1, "")})
        self.assertEqual(len(out["kept"]), 2)
        self.assertIn("claude", out["partial"])

    def test_no_json_shows_engine_output(self):
        def fake_run(engine, model, prompt, cwd, effort=None, timeout_s=None, meta=None):
            if meta is not None:
                meta.update(seconds=1.5, engine=engine, model=model, effort=effort)
            return 1, "Error: Authentication required. Please run 'agent login' first."

        with tempfile.TemporaryDirectory(dir=WORKTREE) as d:
            ctx_file, _ = make_ctx(d, ci="pass")
            orig = review.lean_run.run
            review.lean_run.run = fake_run
            os.environ["PR_GATE_HOME"] = d
            try:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = review.main([str(ctx_file), "--engine", "cursor"])
            finally:
                review.lean_run.run = orig
                del os.environ["PR_GATE_HOME"]
        self.assertEqual(rc, 2)
        self.assertIn("Authentication required", json.loads(buf.getvalue())["engine_output"])

    def test_both_fail_is_exit_3(self):
        with tempfile.TemporaryDirectory(dir=WORKTREE) as d:
            rc, out = self.run_main(d, {"codex": (3, ""), "claude": (1, "")})
        self.assertEqual(rc, 3)
        self.assertIn("both engines failed", out["error"])


class RawGateTest(unittest.TestCase):
    def test_raw_output_is_gated(self):
        with tempfile.TemporaryDirectory(dir=WORKTREE) as d:
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
            os.environ["PR_GATE_HOME"] = d
            try:
                ctx2 = dict(json.loads(ctx_file.read_text()), head_sha="newsha")
                prompt = review.build_prompt(ctx2)
            finally:
                del os.environ["PR_GATE_HOME"]
        self.assertIn("round 1 @ abcdef12: [medium] src/export.ts:2 t", prompt)
        out = json.loads(buf.getvalue())
        self.assertEqual(rc, 0)
        self.assertEqual((out["verdict"], out["action"], out["round"]), ("fix", "send-fix", 1))


class ReviewCostTest(unittest.TestCase):
    def setUp(self):
        home = tempfile.TemporaryDirectory(dir=WORKTREE)
        self.addCleanup(home.cleanup)
        self.home = Path(home.name)
        env = patch.dict(os.environ, PR_GATE_HOME=home.name)
        env.start()
        self.addCleanup(env.stop)
        self.context_file, self.ctx = make_ctx(home.name, ci="pass")
        self.calls = []

        def fake_run(engine, model, prompt, cwd, effort=None, timeout_s=None, meta=None):
            self.calls.append((engine, model, effort, timeout_s))
            meta.update(seconds=2.5, engine=engine, model=model, effort=effort,
                        usage={"input_tokens": 100})
            return 0, json.dumps({"summary": "s", "findings": []})

        runner = patch.object(review.lean_run, "run", side_effect=fake_run)
        self.runner = runner.start()
        self.addCleanup(runner.stop)

    def run_main(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = review.main([str(self.context_file), *args])
        return rc, json.loads(buf.getvalue())

    def config_budget(self, fallback="cursor"):
        (self.home / "config.json").write_text(json.dumps({
            "budget": {"codex_5h_max_pct": 80, "codex_week_max_pct": 90, "fallback": fallback},
            "models": {"cursor": "auto"}}), encoding="utf-8")

    def seed_rounds(self, shas):
        for sha in shas:
            review.findings.record(self.ctx["repo"], self.ctx["pr"], sha, "pass", "green", {})

    def test_delta_and_empty_delta_fallback(self):
        delta = self.home / "delta.diff"
        delta.write_text("+const fixed = 2\n", encoding="utf-8")
        self.ctx.update(delta_diff_file=str(delta), last_reviewed_sha="1234567890")
        prompt = review.build_prompt(self.ctx)
        self.assertIn("Changes since the round you last reviewed (12345678..abcdef12)", prompt)
        self.assertIn("### All files this PR changes\n\n- src/export.ts", prompt)
        self.assertIn("+const fixed = 2", prompt)
        self.assertNotIn("+const x = 1", prompt)
        delta.write_text("", encoding="utf-8")
        self.assertIn("+const x = 1", review.build_prompt(self.ctx))
        delta.unlink()
        self.assertIn("+const x = 1", review.build_prompt(self.ctx))

    def test_delta_is_capped(self):
        delta = self.home / "delta.diff"
        delta.write_text("+x\n" * (review.DIFF_MAX_CHARS // 3 + 1) + "TAIL", encoding="utf-8")
        self.ctx.update(delta_diff_file=str(delta), last_reviewed_sha="1234567890")
        prompt = review.build_prompt(self.ctx)
        self.assertIn("diff truncated", prompt)
        self.assertNotIn("TAIL", prompt)
        self.assertIn("### All files this PR changes", prompt)

    def test_digest_inlined_and_missing_digest_fallback(self):
        digest = self.home / "docs.md"
        digest.write_text("### CLAUDE.md\nKeep UTF-8: café.\n", encoding="utf-8")
        self.ctx["docs_digest_file"] = str(digest)
        prompt = review.build_prompt(self.ctx)
        self.assertIn("Keep UTF-8: café.", prompt)
        self.assertNotIn("- CLAUDE.md", prompt)
        self.assertIn("Get-Content -Raw -Encoding utf8", prompt)
        self.assertNotIn("Read these before judging anything", prompt)
        digest.unlink()
        self.assertIn("- CLAUDE.md", review.build_prompt(self.ctx))

    def test_usage_output_log_and_timeouts(self):
        rc, out = self.run_main("--engine", "codex")
        self.assertEqual(rc, 0)
        self.assertEqual(out["usage"]["codex"]["seconds"], 2.5)
        records = [json.loads(line) for line in (self.home / "usage.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(records), 1)
        self.assertEqual((records[0]["repo"], records[0]["pr"], records[0]["sha"], records[0]["round"]),
                         ("o/r", 5, self.ctx["head_sha"], 1))
        self.assertEqual(records[0]["usage"], {"input_tokens": 100})
        self.run_main("--engine", "cursor")
        self.assertEqual(self.calls[-1][3], review.lean_run.ENGINE_TIMEOUT_S["cursor"])

    def test_budget_switch_uses_fallback_model_even_with_explicit_model(self):
        self.config_budget()
        with patch.object(review.usage, "last_limits", return_value={"5h": 85, "week": 20}):
            rc, out = self.run_main("--engine", "codex", "--model", "gpt-6-luna", "--effort", "high")
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls[0][:3], ("cursor", "auto", None))
        self.assertEqual(out["budget_switch"], {"from": "codex", "to": "cursor", "5h": 85, "week": 20})
        self.assertEqual(set(out["usage"]), {"cursor"})

    def test_ignore_budget(self):
        self.config_budget()
        with patch.object(review.usage, "last_limits", return_value={"5h": 85}) as limits:
            _, out = self.run_main("--engine", "codex", "--ignore-budget")
        self.assertEqual(self.calls[0][0], "codex")
        limits.assert_not_called()
        self.assertNotIn("budget_switch", out)

    def test_budget_week_and_missing_limits(self):
        self.config_budget()
        with patch.object(review.usage, "last_limits", return_value={"week": 90}):
            _, out = self.run_main("--engine", "codex")
        self.assertEqual(out["budget_switch"]["to"], "cursor")
        with patch.object(review.usage, "last_limits", return_value=None):
            _, out = self.run_main("--engine", "codex")
        self.assertEqual(self.calls[-1][0], "codex")
        self.assertNotIn("budget_switch", out)

    def test_budget_resets_at(self):
        self.config_budget()
        now = time.time()
        with patch.object(review.usage, "last_limits", return_value={"5h": 95, "5h_resets_at": now - 60, "week": 10}):
            _, out = self.run_main("--engine", "codex")
        self.assertEqual(self.calls[-1][0], "codex")
        self.assertNotIn("budget_switch", out)

        with patch.object(review.usage, "last_limits", return_value={"5h": 95, "5h_resets_at": now + 60, "week": 10}):
            _, out = self.run_main("--engine", "codex")
        self.assertEqual(self.calls[-1][0], "cursor")
        self.assertIn("budget_switch", out)

        with patch.object(review.usage, "last_limits", return_value={"5h": 95, "5h_resets_at": now - 60, "week": 95, "week_resets_at": now + 60}):
            _, out = self.run_main("--engine", "codex")
        self.assertEqual(self.calls[-1][0], "cursor")
        self.assertIn("budget_switch", out)

    def test_both_usage_and_budget_switch(self):
        _, out = self.run_main("--engine", "both")
        self.assertEqual(set(out["usage"]), {"codex", "claude"})
        self.calls.clear()
        self.config_budget()
        with patch.object(review.usage, "last_limits", return_value={"5h": 85}):
            _, out = self.run_main("--engine", "both")
        self.assertEqual([call[0] for call in self.calls], ["cursor", "claude"])
        self.assertEqual(set(out["usage"]), {"cursor", "claude"})
        self.assertIn("budget_switch", out)

    def test_both_claude_fallback_runs_once(self):
        self.config_budget(fallback="claude")
        with patch.object(review.usage, "last_limits", return_value={"5h": 85}):
            rc, out = self.run_main("--engine", "both")
        self.assertEqual(rc, 0)
        self.assertEqual([call[0] for call in self.calls], ["claude"])
        self.assertEqual(set(out["usage"]), {"claude"})

    def test_round_cap_and_extra_round(self):
        self.seed_rounds(["old1", "old2", "old3"])
        rc, out = self.run_main("--engine", "codex")
        self.assertEqual(rc, 3)
        self.assertIn("round cap reached (3)", out["error"])
        self.assertEqual(self.calls, [])
        rc, _ = self.run_main("--engine", "codex", "--extra-round")
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.calls), 1)

    def test_same_head_runs_at_cap(self):
        self.seed_rounds(["old1", "old2", self.ctx["head_sha"]])
        rc, out = self.run_main("--engine", "codex")
        self.assertEqual(rc, 0)
        self.assertEqual(out["round"], 3)

    def test_raw_gate_bypasses_round_cap(self):
        self.seed_rounds(["old1", "old2", "old3"])
        raw = self.home / "raw.out"
        raw.write_text(json.dumps({"summary": "s", "findings": []}), encoding="utf-8")
        rc, _ = self.run_main("--raw", str(raw))
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
