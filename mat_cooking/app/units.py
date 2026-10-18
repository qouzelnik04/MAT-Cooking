"""
Jednotky a množství surovin.

Převádí se jen to, co jde převést bez odhadu: g <-> kg, ml <-> dl <-> l.
Lžíce, hrnky, kusy a další se nepřevádějí -- "2 lžíce mouky" na gramy by
byl odhad, a odhad v nákupním seznamu je horší než poctivé "2 lžíce".

Množství se drží jako Decimal (žádný float), ať 3 × 0,1 kg je 0,3 kg
a ne 0,30000000000000004.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

# jednotka -> (skupina, kolik základních jednotek je jedna)
CONVERTIBLE = {
    "g": ("hmotnost", Decimal(1)),
    "kg": ("hmotnost", Decimal(1000)),
    "ml": ("objem", Decimal(1)),
    "dl": ("objem", Decimal(100)),
    "l": ("objem", Decimal(1000)),
}
OTHER = ["ks", "lžíce", "lžička", "hrnek", "špetka", "stroužek", "plátek",
         "balení", "konzerva", "hrst", "svazek"]
UNITS = list(CONVERTIBLE) + OTHER

_FRACTIONS = {"½": "0.5", "⅓": "0.333", "¼": "0.25", "¾": "0.75", "⅔": "0.667"}


def parse_amount(text) -> Decimal | None:
    """'1,5' / '1.5' / '1/2' / '½' / '1 ½' -> Decimal. Prázdné = None."""
    s = str(text or "").strip().replace(",", ".")
    if not s:
        return None
    for ch, val in _FRACTIONS.items():
        s = s.replace(ch, f" {val}")
    total = Decimal(0)
    try:
        for part in s.split():
            if re.fullmatch(r"\d+/\d+", part):
                a, b = part.split("/")
                total += Decimal(a) / Decimal(b)
            else:
                total += Decimal(part)
    except (InvalidOperation, ZeroDivisionError):
        return None
    return total if total > 0 else None


def fmt(amount: Decimal | None) -> str:
    """Hezký zápis: 500, 1.5, 0.25 -- bez zbytečných nul."""
    if amount is None:
        return ""
    q = amount.quantize(Decimal("0.01")) if amount < 10 else amount.quantize(Decimal("0.1"))
    s = format(q.normalize(), "f")
    return s


def to_base(amount: Decimal, unit: str) -> tuple[str, Decimal]:
    """(skupina, množství v základní jednotce). Nepřevoditelné: skupina = jednotka."""
    if unit in CONVERTIBLE:
        group, factor = CONVERTIBLE[unit]
        return group, amount * factor
    return unit, amount


def from_base(group: str, amount: Decimal) -> tuple[Decimal, str]:
    """Zpět na čitelnou jednotku: 1500 g -> 1.5 kg, 250 ml -> 250 ml."""
    if group == "hmotnost":
        return (amount / 1000, "kg") if amount >= 1000 else (amount, "g")
    if group == "objem":
        return (amount / 1000, "l") if amount >= 1000 else (amount, "ml")
    return amount, group
