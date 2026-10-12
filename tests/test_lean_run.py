import json
import os
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import lean_run  # noqa: E402

UNSAFE = {"--force", "--yolo", "acceptEdits", "--dangerously-bypass-approvals-and-sandbox",
          "workspace-write", "danger-full-access"}


class FakeProfile(unittest.TestCase):
    """A fake HOME with a codex login, skills, MCPs and plugins the run must NOT see."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.codex = root / "codex"
        (self.codex / "skills" / "vault-recall").mkdir(parents=True)
        (self.codex / "skills" / "vault-recall" / "SKILL.md").write_text("x", encoding="utf-8")
        (self.codex / "plugins" / "cache").mkdir(parents=True)
        (self.codex / "auth.json").write_text("{}", encoding="utf-8")
        (self.codex / "config.toml").write_text(
            '[mcp_servers.chrome]\ncommand = "npx"\nargs = ["-y", "chrome-mcp"]\n'
            '[mcp_servers.chrome.env]\nDEBUG = "1"\n', encoding="utf-8")
        self.run_dir = root / "run"
        self.run_dir.mkdir()
        os.environ["CODEX_HOME"] = str(self.codex)

    def tearDown(self):
        del os.environ["CODEX_HOME"]
        self.tmp.cleanup()


class CodexTest(FakeProfile):
    def test_lean_home_has_login_only(self):
        argv, env = lean_run.build("codex", "gpt-6.1-sol", Path("."), self.run_dir, effort="low")
        home = Path(env["CODEX_HOME"])
        self.assertEqual(sorted(p.name for p in home.iterdir()), ["auth.json"])
        self.assertIn("--ignore-user-config", argv)
        self.assertIn("read-only", argv)
        self.assertIn("model_reasoning_effort=low", argv)
        self.assertEqual(argv[argv.index("-m") + 1], "gpt-6.1-sol")
        if lean_run.WIN:  # without it, read-only codex can't run a single command
            self.assertIn("windows.sandbox=unelevated", argv)

    def test_skill_grant_copies_only_that_skill(self):
        _, env = lean_run.build("codex", "m", Path("."), self.run_dir, skills=["vault-recall"])
        self.assertTrue((Path(env["CODEX_HOME"]) / "skills" / "vault-recall" / "SKILL.md").is_file())

    def test_mcp_grant_is_valid_toml(self):
        argv, _ = lean_run.build("codex", "m", Path("."), self.run_dir, mcp=["chrome"])
        overrides = [argv[i + 1] for i, a in enumerate(argv) if a == "-c" and argv[i + 1].startswith("mcp_servers.")]
        for o in overrides:
            key, val = o.split("=", 1)
            tomllib.loads(f"v = {val}")
        self.assertIn('mcp_servers.chrome.env={DEBUG = "1"}', overrides)

    def test_unknown_grant_refused(self):
        with self.assertRaises(RuntimeError):
            lean_run.build("codex", "m", Path("."), self.run_dir, mcp=["nope"])

    def test_no_login_refuses(self):
        (self.codex / "auth.json").unlink()
        with self.assertRaises(RuntimeError):
            lean_run.build("codex", "m", Path("."), self.run_dir)


class ClaudeCursorTest(FakeProfile):
    def test_claude_drops_user_settings_skills_mcp(self):
        argv, _ = lean_run.build("claude", "sonnet", Path("."), self.run_dir)
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "project,local")
        self.assertIn("--strict-mcp-config", argv)
        self.assertIn("--disable-slash-commands", argv)
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "plan")

    def test_claude_mcp_grant(self):
        claude_json = Path(self.tmp.name) / ".claude.json"
        claude_json.write_text(json.dumps({"mcpServers": {"db": {"command": "x"}, "other": {}}}), encoding="utf-8")
        with mock.patch.object(lean_run.Path, "home", return_value=Path(self.tmp.name)):
            argv, _ = lean_run.build("claude", "sonnet", Path("."), self.run_dir, mcp=["db"])
        cfg = json.loads(Path(argv[argv.index("--mcp-config") + 1]).read_text(encoding="utf-8"))
        self.assertEqual(list(cfg["mcpServers"]), ["db"])

    def test_cursor_read_only_and_refuses_grants(self):
        argv, _ = lean_run.build("cursor", "composer-2.5", Path("."), self.run_dir)
        self.assertEqual(argv[argv.index("--mode") + 1], "ask")
        # live run 6: a bare `agent` resolved to another vendor's CLI first on PATH
        self.assertTrue(any(Path(a).name.lower().startswith("cursor-agent") for a in argv), argv)
        with self.assertRaises(RuntimeError):
            lean_run.build("cursor", "composer-2.5", Path("."), self.run_dir, mcp=["x"])

    def test_read_only_flags_everywhere(self):
        for engine in lean_run.ENGINES:
            argv, _ = lean_run.build(engine, "m", Path("."), self.run_dir)
            self.assertFalse(UNSAFE & set(argv), engine)


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["LEAN_RUN_HOME"] = self.tmp.name

    def tearDown(self):
        del os.environ["LEAN_RUN_HOME"]
        self.tmp.cleanup()

    def fake(self, code):
        return lambda *a, **k: ([sys.executable, "-c", code], dict(os.environ))

    def test_model_required(self):
        with self.assertRaises(ValueError):
            lean_run.run("codex", "", "x", Path("."))

    def test_prompt_via_file_and_run_dir_removed(self):
        with mock.patch.object(lean_run, "build", self.fake("import sys; print(sys.stdin.read().upper())")):
            code, out = lean_run.run("codex", "m", "hello", Path("."))
        self.assertEqual((code, out.strip()), (0, "HELLO"))
        self.assertEqual(os.listdir(self.tmp.name), [])  # credential copy cleaned up

    def test_hung_tree_is_killed(self):
        child = ("import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']);"
                 " time.sleep(60)")
        with mock.patch.object(lean_run, "build", self.fake(child)):
            start = time.monotonic()
            code, _ = lean_run.run("cursor", "m", "x", Path("."), timeout_s=2)
        self.assertEqual(code, 3)
        self.assertLess(time.monotonic() - start, 20)

    def test_run_without_meta(self):
        with mock.patch.object(lean_run, "build", self.fake("print('ok')")):
            got = lean_run.run("codex", "m", "x", Path("."))
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0], 0)
        self.assertIn("ok", got[1])

    def test_codex_meta_usage_and_limits(self):
        script = (
            "import json,os\n"
            "from pathlib import Path\n"
            "p = Path(os.environ['LEAN_TEST_RUN_DIR'])/'codex-home'/'sessions'/'2026'/'10'/'12'\n"
            "p.mkdir(parents=True)\n"
            "row = {'type':'event_msg','payload':{'type':'token_count',"
            "'info':{'total_token_usage':{'input_tokens':1,'output_tokens':1,'total_tokens':2}},"
            "'rate_limits':{'primary':{'used_percent':2.0,'window_minutes':300,'resets_at':1791756925},"
            "'secondary':{'used_percent':21.0,'window_minutes':10080,'resets_at':1792307083}}}}\n"
            "(p/'rollout-x.jsonl').write_text(json.dumps(row)+'\\n', encoding='utf-8')\n"
            "print('noise')\n"
            "print(json.dumps({'type':'turn.completed','usage':{'input_tokens':3,'output_tokens':4}}))\n"
        )

        def fake_build(engine, model, cwd, run_dir, *a, **k):
            env = dict(os.environ)
            env["LEAN_TEST_RUN_DIR"] = str(run_dir)
            return [sys.executable, "-c", script], env

        meta = {}
        with mock.patch.object(lean_run, "build", fake_build):
            code, _ = lean_run.run("codex", "m", "hi", Path("."), effort="low", meta=meta)
        self.assertEqual(code, 0)
        self.assertEqual(meta["usage"], {"input_tokens": 3, "output_tokens": 4})
        self.assertEqual(meta["rate_limits"], {
            "5h": 2.0, "week": 21.0,
            "5h_resets_at": 1791756925, "week_resets_at": 1792307083,
        })
        self.assertEqual(meta["engine"], "codex")
        self.assertEqual(meta["model"], "m")
        self.assertEqual(meta["effort"], "low")
        self.assertIsInstance(meta["seconds"], float)
        self.assertEqual(os.listdir(self.tmp.name), [])

    def test_cursor_meta_copies_result(self):
        line = '{"type":"result","duration_ms":1234,"usage":{"inputTokens":5}}'
        meta = {}
        with mock.patch.object(lean_run, "build", self.fake(f"print({line!r})")):
            lean_run.run("cursor", "composer-2.5", "x", Path("."), meta=meta)
        self.assertEqual(meta["duration_ms"], 1234)
        self.assertEqual(meta["usage"], {"inputTokens": 5})
        self.assertNotIn("duration_api_ms", meta)

    def test_garbage_stdout_records_seconds(self):
        meta = {}
        with mock.patch.object(lean_run, "build", self.fake("print('this is not json')")):
            code, _ = lean_run.run("codex", "m", "x", Path("."), meta=meta)
        self.assertEqual(code, 0)
        self.assertIsInstance(meta["seconds"], float)
        self.assertEqual(meta["seconds"], round(meta["seconds"], 1))
        self.assertNotIn("usage", meta)
        self.assertNotIn("rate_limits", meta)

    def test_cursor_default_timeout(self):
        self.assertEqual(lean_run.ENGINE_TIMEOUT_S["cursor"], 1500)
        seen = {}

        class Proc:
            pid = 1

            def wait(self, timeout=None):
                seen["timeout"] = timeout
                return 0

        with mock.patch.object(lean_run, "build", self.fake("pass")), \
             mock.patch.object(lean_run.subprocess, "Popen", return_value=Proc()):
            code, _ = lean_run.run("cursor", "m", "x", Path("."), timeout_s=None)
        self.assertEqual(code, 0)
        self.assertEqual(seen["timeout"], 1500)


if __name__ == "__main__":
    unittest.main()
