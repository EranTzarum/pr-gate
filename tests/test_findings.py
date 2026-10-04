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

    def test_repo_name_is_sanitized(self):
        findings.record("../../evil", 1, "s", "pass", "green", {})
        names = os.listdir(Path(self.tmp.name) / "state")
        self.assertEqual(len(names), 1)
        self.assertNotIn("..", names[0].replace("__", ""))


if __name__ == "__main__":
    unittest.main()
