"""Partial refunds for a toy checkout (eval fixture, never merged)."""
from decimal import ROUND_HALF_UP, Decimal

MAX_REFUNDS = 5


def refund(charge_cents, already_refunded_cents, amount_cents, refund_count=0):
    """Return the new refunded total after refunding `amount_cents` of a charge.

    `refund_count` is how many refunds the charge already had."""
    if amount_cents <= 0:
        raise ValueError("amount must be positive")
    if refund_count >= MAX_REFUNDS:
        raise ValueError("too many refunds for this charge")
    if already_refunded_cents + amount_cents > charge_cents:
        raise ValueError("refund exceeds charge")
    return already_refunded_cents + amount_cents


def to_cents(amount):
    """'12.34' or 12.34 -> 1234."""
    return int((Decimal(str(amount)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
