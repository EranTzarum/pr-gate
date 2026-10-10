import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from refund import MAX_REFUNDS, refund, to_cents  # noqa: E402


class RefundTest(unittest.TestCase):
    def test_partial(self):
        self.assertEqual(refund(1000, 200, 300), 500)

    def test_over_refund_refused(self):
        with self.assertRaises(ValueError):
            refund(1000, 900, 500)

    def test_refund_cap(self):
        with self.assertRaises(ValueError):
            refund(1000, 0, 1, refund_count=MAX_REFUNDS)

    def test_cents(self):
        self.assertEqual(to_cents("12.50"), 1250)
        self.assertEqual(to_cents("0.29"), 29)
        self.assertEqual(to_cents(19.99), 1999)


if __name__ == "__main__":
    unittest.main()
