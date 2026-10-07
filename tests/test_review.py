import io
import json
import os
import sys
import tempfile
import time
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
            os.environ["PR_GATE_HOME"] = d
            try:
                _, ctx = make_ctx(d)
                text = review.build_prompt(ctx)
            finally:
                del os.environ["PR_GATE_HOME"]
        self.assertNotIn("{{", text)
        self.assertIn("none (first round)", text)
        for s in ("Add export", "- CLAUDE.md", "CI FAILED", "TypeError at export.ts:2", "+const x = 1"):
            self.assertIn(s, text)

    def test_base_ci_told_to_reviewer(self):
        with tempfile.TemporaryDirectory() as d:
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
    def test_defaults_are_explicit_mid_tier(self):
        seen = {}

        def fake_run(engine, model, prompt, cwd, effort=None, timeout_s=None):
            seen[engine] = (model, effort)
            return 0, "{}"

        orig = review.lean_run.run
        review.lean_run.run = fake_run
        try:
            with tempfile.TemporaryDirectory() as d:
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
            with tempfile.TemporaryDirectory() as d:
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

    def test_risk_in_prompt(self):
        with tempfile.TemporaryDirectory() as d:
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
        def fake_run(engine, model, prompt, cwd, effort=None, timeout_s=None):
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
        with tempfile.TemporaryDirectory() as d:
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
        with tempfile.TemporaryDirectory() as d:
            rc, out = self.run_main(d, {"codex": (0, codex), "claude": (1, "")})
        self.assertEqual(len(out["kept"]), 2)
        self.assertIn("claude", out["partial"])

    def test_both_fail_is_exit_3(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out = self.run_main(d, {"codex": (3, ""), "claude": (1, "")})
        self.assertEqual(rc, 3)
        self.assertIn("both engines failed", out["error"])


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


if __name__ == "__main__":
    unittest.main()
