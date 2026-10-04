"""Checkout discount helper (pr-gate eval toy, never merged)."""

MAX_ITEMS = 50


def apply_discount(price_cents, percent):
    """Price after a percentage discount, in cents."""
    if not 0 <= percent <= 100:
        raise ValueError("percent must be 0..100")
    return price_cents - price_cents * percent // 100


def cart_total(items, percent):
    total = 0
    for item in items:
        if item["qty"] <= 0 or item["price_cents"] < 0:
            raise ValueError("qty must be positive and price non-negative")
        total += item["price_cents"] * item["qty"]
    return apply_discount(total, percent)
