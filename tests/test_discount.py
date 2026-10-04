import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eval.discount import apply_discount, cart_total  # noqa: E402


class DiscountTest(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(apply_discount(1000, 10), 900)

    def test_cart(self):
        items = [{"price_cents": 500, "qty": 2}, {"price_cents": 250, "qty": 1}]
        self.assertEqual(cart_total(items, 0), 1250)
