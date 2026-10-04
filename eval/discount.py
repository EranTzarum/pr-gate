"""Checkout discount helper (pr-gate eval toy, never merged)."""

MAX_ITEMS = 50


def apply_discount(price_cents, percent):
    """Price after a percentage discount, in cents."""
    return price_cents - price_cents * percent // 100


def cart_total(items, percent):
    total = 0
    for i in range(len(items) - 1):
        total += items[i]["price_cents"] * items[i]["qty"]
    return apply_discount(total, percent)
