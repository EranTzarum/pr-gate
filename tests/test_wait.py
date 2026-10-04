import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import pr_context  # noqa: E402
import wait  # noqa: E402


class WaitTest(unittest.TestCase):
    def setUp(self):
        self.orig = pr_context.run

    def tearDown(self):
        pr_context.run = self.orig

    def feed(self, outputs):
        it = iter(outputs)
        pr_context.run = lambda args, cwd=None, check=True: next(it)

    def test_ci_waits_until_not_pending(self):
        self.feed([json.dumps([{"bucket": "pending"}]), "garbage", json.dumps([{"bucket": "fail"}])])
        self.assertEqual(wait.wait("ci", "o/r", 1, sleep=lambda s: None), {"ci": "fail"})

    def test_push_returns_new_sha(self):
        self.feed([json.dumps({"headRefOid": "a", "state": "OPEN"}),
                   json.dumps({"headRefOid": "b", "state": "OPEN"})])
        self.assertEqual(wait.wait("push", "o/r", 1, since="a", sleep=lambda s: None), {"sha": "b"})

    def test_push_stops_when_pr_closed(self):
        self.feed([json.dumps({"headRefOid": "a", "state": "MERGED"})])
        self.assertEqual(wait.wait("push", "o/r", 1, since="a", sleep=lambda s: None)["state"], "MERGED")

    def test_timeout(self):
        pr_context.run = lambda args, cwd=None, check=True: json.dumps([{"bucket": "pending"}])
        self.assertIsNone(wait.wait("ci", "o/r", 1, timeout_s=0, sleep=lambda s: None))


if __name__ == "__main__":
    unittest.main()
