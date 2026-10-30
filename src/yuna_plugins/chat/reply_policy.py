"""Targeted replies respect the configured favor boundary."""
import math


def mention_policy(favor, roll, explicit_at=False):
    """好感不低于 -35 时回应；低于边界时可拒绝，明确 @ 也遵循此规则。"""
    try:
        f = float(favor)
    except (TypeError, ValueError):
        f = 0
    if not math.isfinite(f):
        f = 0
    probability = 0.0 if f >= -35 else min(.75, .25 + (-35 - f) / 130)
    return {
        'force': f >= -35,
        'refuse': probability > 0 and roll < probability,
        'refusal_probability': probability,
    }


def should_notify(event, recent, now):
    try:
        delayed = now - float(event.time) >= 60
    except (AttributeError, TypeError, ValueError):
        return False
    participants = {
        str(r.get('qq'))
        for r in recent[-8:]
        if r.get('role') == 'user' and r.get('qq')
    }
    return delayed and len(participants) >= 3
