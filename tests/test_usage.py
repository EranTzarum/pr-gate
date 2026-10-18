import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import usage  # noqa: E402


class UsageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._prev_home = os.environ.get("PR_GATE_HOME")
        os.environ["PR_GATE_HOME"] = self.tmp.name

    def tearDown(self):
        if self._prev_home is None:
            os.environ.pop("PR_GATE_HOME", None)
        else:
            os.environ["PR_GATE_HOME"] = self._prev_home
        self.tmp.cleanup()

    def test_log_read_adds_ts(self):
        usage.log({"engine": "codex", "repo": "o/r", "pr": 1})
        usage.log({"engine": "claude", "ts": "2026-01-02T12:00:00Z"})
        rows = usage.read()
        self.assertEqual(len(rows), 2)
        self.assertIn("ts", rows[0])
        self.assertEqual(rows[1]["ts"], "2026-01-02T12:00:00Z")

    def test_log_appends_shared_spend_and_server_rows(self):
        usage.log({"engine": "codex", "model": "gpt-6-sol", "repo": "o/r", "pr": 4, "sha": "abc",
                   "ts": "2026-10-18T09:00:00Z",
                   "usage": {"input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 5},
                   "rate_limits": {"5h": 23.0, "5h_resets_at": 1792328911, "week": 4.0}})
        rows = [json.loads(x) for x in usage.SHARED().read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["kind"] for r in rows], ["spend", "server", "server"])
        spend = rows[0]
        self.assertEqual((spend["source"], spend["pool"], spend["model"], spend["ticket"]),
                         ("pr-gate", "codex:chatgpt", "gpt-6-sol", "o/r#4"))
        self.assertEqual(spend["usage"], {"input": 100, "cached": 60, "cache_creation": 0, "output": 5})
        self.assertEqual((rows[1]["window"], rows[1]["used_pct"], rows[1]["resets_at"]), ("5h", 23.0, 1792328911))
        self.assertNotIn("resets_at", rows[2])
        self.assertNotIn("model", rows[1])  # a server row speaks for the whole pool

    def test_pools(self):
        self.assertEqual(usage.pool_of("cursor", "composer-2.5"), "cursor:included")
        self.assertEqual(usage.pool_of("cursor", "gpt-5.6-sol-high"), "cursor:other")
        self.assertEqual(usage.pool_of("agy", "gemini-3.8-flash-high"), "agy:gemini")
        self.assertEqual(usage.pool_of("agy", "claude-sonnet-5-5-medium"), "agy:claude-gpt")
        self.assertEqual(usage.pool_of("claude", "opus"), "claude:max")

    def test_last_limits_takes_newest_shared_reading_per_window(self):
        usage.log({"engine": "codex", "ts": "2026-10-18T08:00:00Z", "rate_limits": {"5h": 10.0, "week": 3.0}})
        other = [  # another tool's readings: newer 5h, older week, one model-only row, one unknown pool
            {"ts": "2026-10-18T09:00:00Z", "source": "crew", "kind": "server", "engine": "codex",
             "pool": "codex:chatgpt", "window": "5h", "used_pct": 85.0, "resets_at": 1},
            {"ts": "2026-10-17T09:00:00Z", "source": "crew", "kind": "server", "engine": "codex",
             "pool": "codex:chatgpt", "window": "week", "used_pct": 99.0},
            {"ts": "2026-10-18T10:00:00Z", "source": "crew", "kind": "server", "engine": "codex",
             "pool": "codex:chatgpt", "model": "gpt-6-luna", "window": "5h", "used_pct": 100.0},
        ]
        with open(usage.SHARED(), "a", encoding="utf-8") as f:
            f.write("".join(json.dumps(r) + "\n" for r in other) + '{"partial')
        self.assertEqual(usage.last_limits("codex"), {"5h": 85.0, "5h_resets_at": 1, "week": 3.0})
        self.assertIsNone(usage.last_limits("cursor"))

    def test_read_skips_bad_line(self):
        p = usage.PATH()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"engine":"codex"}\nnot json\n{"engine":"claude"}\n', encoding="utf-8")
        self.assertEqual(len(usage.read()), 2)

    def test_read_since_seconds(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(microsecond=0)
        usage.log({"engine": "codex", "ts": old.isoformat().replace("+00:00", "Z")})
        usage.log({"engine": "codex"})
        self.assertEqual(len(usage.read(since_seconds=3600)), 1)

    def test_last_limits(self):
        usage.log(
            {
                "engine": "codex",
                "ts": "2026-01-01T00:00:00Z",
                "rate_limits": {"5h": 10, "week": 20},
            }
        )
        usage.log({"engine": "claude", "ts": "2026-01-02T00:00:00Z", "rate_limits": {"5h": 99}})
        usage.log({"engine": "codex", "ts": "2026-01-03T00:00:00Z"})
        usage.log(
            {
                "engine": "codex",
                "ts": "2026-01-04T00:00:00Z",
                "rate_limits": {"5h": 34, "week": 61},
            }
        )
        self.assertEqual(usage.last_limits("codex"), {"5h": 34, "week": 61})
        self.assertIsNone(usage.last_limits("cursor"))

    def test_summary_totals_per_engine(self):
        records = [
            {
                "engine": "codex",
                "seconds": 10,
                "usage": {
                    "input_tokens": 1000,
                    "cached_input_tokens": 500,
                    "output_tokens": 100,
                    "reasoning_output_tokens": 5,
                },
                "rate_limits": {"5h": 10, "week": 20},
                "ts": "2026-01-01T00:00:00Z",
            },
            {
                "engine": "codex",
                "seconds": 20,
                "usage": {"input_tokens": 2000, "output_tokens": 50},
                "rate_limits": {"5h": 34, "week": 61},
                "ts": "2026-01-02T00:00:00Z",
            },
            {"engine": "claude", "seconds": 5, "usage": {"input_tokens": 300}},
        ]
        s = usage.summary(records)
        self.assertEqual(s["codex"]["runs"], 2)
        self.assertEqual(s["codex"]["seconds"], 30)
        self.assertEqual(s["codex"]["input"], 3000)
        self.assertEqual(s["codex"]["cached"], 500)
        self.assertEqual(s["codex"]["output"], 150)
        self.assertEqual(s["codex"]["reasoning"], 5)
        self.assertEqual(s["codex"]["latest_5h"], 34)
        self.assertEqual(s["codex"]["latest_week"], 61)
        self.assertEqual(s["claude"]["runs"], 1)
        self.assertEqual(s["claude"]["input"], 300)

    def test_cli_json_and_empty(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = usage.main(["--since", "all", "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue().strip(), "no reviews logged yet")

        usage.log(
            {
                "engine": "codex",
                "seconds": 1,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = usage.main(["--since", "all", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertIn("codex", data)
        self.assertEqual(data["codex"]["runs"], 1)

    def test_fmt_pct(self):
        self.assertEqual(usage._fmt_pct(1.0), "1%")
        self.assertEqual(usage._fmt_pct(34.0), "34%")
        self.assertIsNone(usage._fmt_pct(None))


if __name__ == "__main__":
    unittest.main()
