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
