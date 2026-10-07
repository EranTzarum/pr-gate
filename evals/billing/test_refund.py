import unittest

from refund import refund, to_cents


class RefundTest(unittest.TestCase):
    def test_partial(self):
        self.assertEqual(refund(1000, 200, 300), 500)

    def test_cents(self):
        self.assertEqual(to_cents("12.50"), 1250)


if __name__ == "__main__":
    unittest.main()
