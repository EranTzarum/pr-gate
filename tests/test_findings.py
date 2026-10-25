import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import findings  # noqa: E402

SCENARIO = "A user from tenant B calls the endpoint with tenant A's id and reads A's rows."


def f(**kw):
    base = {"severity": "high", "file": "src/api.py", "line": 3, "title": "t",
            "scenario": SCENARIO, "fix": "check tenant"}
    base.update(kw)
    return base


class ExtractTest(unittest.TestCase):
    def test_plain_json(self):
        out = findings.extract('{"summary": "s", "findings": []}')
        self.assertEqual(out["summary"], "s")

    def test_fenced_json_after_prose(self):
        text = 'Here you go:\n```json\n{"summary": "s", "findings": [{"a": 1}]}\n```\n'
        self.assertEqual(findings.extract(text)["findings"], [{"a": 1}])

    def test_claude_json_envelope(self):
        inner = json.dumps({"summary": "s", "findings": []})
        env = json.dumps({"type": "result", "result": "```json\n" + inner + "\n```"})
        self.assertEqual(findings.extract(env)["summary"], "s")

    def test_codex_jsonl_last_agent_message(self):
        lines = [
            json.dumps({"type": "item.completed", "item": {"type": "reasoning", "text": "x"}}),
            json.dumps({"type": "item.completed", "item": {"type": "agent_message",
                        "text": json.dumps({"summary": "c", "findings": []})}}),
        ]
        self.assertEqual(findings.extract("\n".join(lines))["summary"], "c")

    def test_garbage_raises(self):
        with self.assertRaises(ValueError):
            findings.extract("I could not review this.")


class VerifyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "src").mkdir()
        (root / "src" / "api.py").write_text("a\nb\nc\nd\n", encoding="utf-8")
        self.root = root

    def tearDown(self):
        self.tmp.cleanup()

    def test_keeps_valid(self):
        kept, dropped = findings.verify([f()], self.root)
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, [])

    def test_drops_missing_file_bad_line_vague_bad_severity(self):
        bad = [f(file="src/nope.py"), f(line=99), f(line=0), f(scenario="may be an issue"),
               f(severity="critical"), f(file="../outside.py"), {"severity": "low"}]
        kept, dropped = findings.verify(bad, self.root)
        self.assertEqual(kept, [])
        self.assertEqual(len(dropped), len(bad))
        self.assertTrue(all(d["reason"] for d in dropped))

    def test_accepts_file_colon_line_form(self):
        item = f()
        del item["line"]
        item["file"] = "src/api.py:2"
        kept, _ = findings.verify([item], self.root)
        self.assertEqual((kept[0]["file"], kept[0]["line"]), ("src/api.py", 2))

    def test_dedupes(self):
        kept, dropped = findings.verify([f(), f()], self.root)
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped[0]["reason"], "duplicate")


class GateTest(unittest.TestCase):
    def test_verdicts(self):
        low = [f(severity="low")]
        self.assertEqual(findings.gate("pending", []), "wait")
        self.assertEqual(findings.gate("fail", low), "fix")
        self.assertEqual(findings.gate("pass", [f(severity="medium")]), "fix")
        self.assertEqual(findings.gate("pass", [f(severity="blocker")]), "fix")
        self.assertEqual(findings.gate("pass", low), "green")
        self.assertEqual(findings.gate("none", []), "green")


class StateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["PR_GATE_HOME"] = self.tmp.name

    def tearDown(self):
        del os.environ["PR_GATE_HOME"]
        self.tmp.cleanup()

    def test_same_sha_replaces_new_sha_appends(self):
        findings.record("o/r", 7, "sha1", "fail", "fix", {})
        findings.record("o/r", 7, "sha1", "pass", "fix", {})
        st = findings.record("o/r", 7, "sha2", "pass", "fix", {})
        self.assertEqual([r["sha"] for r in st["rounds"]], ["sha1", "sha2"])
        self.assertEqual(st["rounds"][0]["ci"], "pass")

    def test_cap_escalates_on_third_fix(self):
        for i in range(1, 3):
            st = findings.record("o/r", 8, f"s{i}", "pass", "fix", {})
            self.assertEqual(findings.next_action(st), "send-fix")
        st = findings.record("o/r", 8, "s3", "pass", "fix", {})
        self.assertEqual(findings.next_action(st), "escalate")

    def test_green_asks_merge(self):
        st = findings.record("o/r", 9, "s1", "pass", "green", {})
        self.assertEqual(findings.next_action(st), "ask-merge")

    def check(self, pr, sha, items, ci="pass", resolved=()):
        root = Path(self.tmp.name) / "co"
        (root / "src").mkdir(parents=True, exist_ok=True)
        (root / "src" / "api.py").write_text("a\nb\nc\nd\n", encoding="utf-8")
        raw = Path(self.tmp.name) / f"{sha}.out"
        raw.write_text(json.dumps({"summary": "s", "findings": items, "resolved": list(resolved)}), encoding="utf-8")
        from contextlib import redirect_stdout
        import io
        buf = io.StringIO()
        with redirect_stdout(buf):
            findings.main(["check", "--raw", str(raw), "--root", str(root), "--ci", ci,
                           "--repo", "o/r", "--pr", str(pr), "--sha", sha])
        return json.loads(buf.getvalue())

    def test_mediums_at_cap_go_green_as_followups(self):
        med = [f(severity="medium", title="edge path")]
        self.assertEqual(self.check(10, "s1", med)["action"], "send-fix")
        self.assertEqual(self.check(10, "s2", med)["action"], "send-fix")
        out = self.check(10, "s3", med)
        self.assertEqual((out["verdict"], out["action"]), ("green", "ask-merge"))
        self.assertEqual([i["id"] for i in out["followups_added"]], ["10-1"])
        self.assertEqual(self.check(10, "s3", med)["followups_added"], [])  # re-check adds nothing
        # a high at the cap still escalates; CI failing too
        self.assertEqual(self.check(11, "a", [f()])["action"], "send-fix")
        self.check(11, "b", [f()])
        self.assertEqual(self.check(11, "c", [f()])["action"], "escalate")

    def test_later_pr_resolves_a_followup(self):
        for sha in ("s1", "s2", "s3"):
            self.check(12, sha, [f(severity="medium")])
        self.assertEqual(len(findings.followups("o/r")), 1)
        out = self.check(13, "n1", [], resolved=["12-1", "bogus"])
        self.assertEqual(out["followups_closed"], ["12-1"])
        self.assertEqual(out["followups_open"], [])
        self.assertEqual(findings.followups("o/r", include_closed=True)[0]["closed_by"], "#13")

    def test_repo_name_is_sanitized(self):
        findings.record("../../evil", 1, "s", "pass", "green", {})
        names = os.listdir(Path(self.tmp.name) / "state")
        self.assertEqual(len(names), 1)
        self.assertNotIn("..", names[0].replace("__", ""))


if __name__ == "__main__":
    unittest.main()
