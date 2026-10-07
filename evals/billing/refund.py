"""Partial refunds for a toy checkout (eval fixture, never merged)."""

MAX_REFUNDS = 5


def refund(charge_cents, already_refunded_cents, amount_cents):
    """Return the new refunded total after refunding `amount_cents` of a charge."""
    if amount_cents <= 0:
        raise ValueError("amount must be positive")
    return already_refunded_cents + amount_cents


def to_cents(amount):
    """'12.34' or 12.34 -> 1234."""
    return int(float(amount) * 100)
