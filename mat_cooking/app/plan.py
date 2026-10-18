"""
Plán vaření -> nákupní seznam.

Ze všech receptů v plánu se suroviny přepočtou na zvolený počet porcí
a sečtou: stejná surovina ve stejné skupině jednotek je jeden řádek
(rýže 200 g + 0,3 kg = 500 g). Různé skupiny se nesčítají (cibule 2 ks
a cibule 100 g zůstanou dva řádky) -- převod by byl odhad.

Kusy se zaokrouhlují nahoru: půl cibule v obchodě nekoupíš.
Suroviny bez množství (sůl, pepř) jsou v seznamu bez čísla.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

from .units import fmt, from_base, parse_amount, to_base


def shopping_list(recipes: list[dict], items: list[dict], have: set[str]) -> list[dict]:
    portions = {r["id"]: Decimal(r["portions"]) / Decimal(r["servings"] or 1) for r in recipes}
    names = {r["id"]: r["name"] for r in recipes}
    rows: dict[str, dict] = {}
    for it in items:
        factor = portions.get(it["recipe_id"])
        if factor is None:
            continue
        amount = parse_amount(it["amount"])
        unit = it["unit"] or ""
        if amount is not None and unit:
            group, base = to_base(amount * factor, unit)
        elif amount is not None:
            group, base = "ks", amount * factor      # číslo bez jednotky = kusy
        else:
            group, base = "", None                   # bez množství
        key = f'{it["ingredient_id"]}|{group}'
        row = rows.setdefault(key, {"key": key, "ingredient": it["ingredient"],
                                    "group": group, "base": None, "recipes": []})
        if base is not None:
            row["base"] = (row["base"] or Decimal(0)) + base
        if names[it["recipe_id"]] not in row["recipes"]:
            row["recipes"].append(names[it["recipe_id"]])

    out = []
    for row in rows.values():
        amount, unit = (None, "")
        if row["base"] is not None:
            amount, unit = from_base(row["group"], row["base"])
            if unit == "ks":
                amount = amount.to_integral_value(rounding=ROUND_CEILING)
        out.append({
            "key": row["key"], "ingredient": row["ingredient"],
            "amount": fmt(amount) if amount is not None else "", "unit": unit,
            "recipes": row["recipes"], "have": row["key"] in have,
        })
    out.sort(key=lambda r: r["ingredient"].lower())
    return out


def item_text(row: dict) -> str:
    """Text položky do nákupního seznamu: 'rýže 1,5 kg'."""
    qty = f' {row["amount"].replace(".", ",")} {row["unit"]}'.rstrip() if row["amount"] else ""
    return f'{row["ingredient"]}{qty}'
