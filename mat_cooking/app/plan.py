"""
Plán vaření -> nákupní seznam.

Ze všech receptů v plánu se suroviny přepočtou na zvolený počet porcí
a sečtou: stejná surovina ve stejné skupině jednotek je jeden řádek
(rýže 200 g + 0,3 kg = 500 g). Různé skupiny se nesčítají (cibule 2 ks
a cibule 100 g zůstanou dva řádky) -- převod by byl odhad.

Kusy se zaokrouhlují nahoru: půl cibule v obchodě nekoupíš.
Suroviny bez množství (sůl, pepř) jsou v seznamu bez čísla.

Do nákupního seznamu se posílá jen to, co z aktuálního plánu ještě
neodešlo (plan_sent) -- přidaný recept pošle jen svůj přírůstek.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

from .units import UNITS, fmt, from_base, parse_amount, to_base


def shopping_list(recipes: list[dict], items: list[dict], have: set[str],
                  sent: dict[str, str] | None = None) -> list[dict]:
    sent = sent or {}
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
            if row["group"] == "ks":
                row["base"] = row["base"].to_integral_value(rounding=ROUND_CEILING)
            amount, unit = from_base(row["group"], row["base"])
        # Kolik ještě poslat: celé množství minus to, co už z plánu odešlo
        if row["key"] in sent:
            pending = (None if row["base"] is None
                       else max(row["base"] - Decimal(sent[row["key"]] or 0), Decimal(0)))
            todo = bool(pending)
        else:
            pending, todo = row["base"], True
        out.append({
            "key": row["key"], "ingredient": row["ingredient"], "group": row["group"],
            "amount": fmt(amount) if amount is not None else "", "unit": unit,
            "base": str(row["base"]) if row["base"] is not None else "",
            "pending": str(pending) if pending is not None else "",
            "to_send": todo, "recipes": row["recipes"], "have": row["key"] in have,
        })
    out.sort(key=lambda r: r["ingredient"].lower())
    return out


def item_text(ingredient: str, group: str, base: Decimal | None) -> str:
    """Text položky do nákupního seznamu: 'rýže 1,5 kg'."""
    if base is None:
        return ingredient
    amount, unit = from_base(group, base)
    return f'{ingredient} {fmt(amount).replace(".", ",")} {unit}'


def parse_item(text: str, ingredients: set[str]) -> tuple[str, str, Decimal] | None:
    """'rýže 1,5 kg' -> ('rýže', 'hmotnost', 1500). Jen u položek ve tvaru,
    jaký appka sama píše; cizí položky (ručně napsané) vrací None."""
    parts = text.strip().rsplit(" ", 2)
    if len(parts) != 3 or parts[0].lower() not in ingredients:
        return None
    amount = parse_amount(parts[1])
    if amount is None or parts[2] not in UNITS:
        return None
    group, base = to_base(amount, parts[2])
    return parts[0].lower(), group, base
