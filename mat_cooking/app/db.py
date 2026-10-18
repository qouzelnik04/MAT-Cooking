"""
Databáze (SQLite, součást Pythonu) -- recepty a katalog surovin.

Katalog surovin je společný pro všechny recepty: "cibule" je jedna surovina,
ať je v kolika receptech chce. Jen tak jde později sečíst, kolik cibule je
potřeba na celý týden. Přejmenování suroviny na název, který už existuje,
obě sloučí -- tak se uklidí "cibule" vs. "cibulka".

Množství se ukládá jako text (Decimal), ne jako float.
Schéma se verzuje přes PRAGMA user_version -- nová verze = nová migrace
v MIGRATIONS, stará data zůstanou.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone

from .units import UNITS, fmt, parse_amount

CATEGORIES = {
    "snidane": "Snídaně",
    "hlavni": "Hlavní jídlo",
    "polevka": "Polévka",
    "svacina": "Svačina",
    "dezert": "Dezert",
    "jine": "Jiné",
}

MIGRATIONS = [
    # 1: recepty, suroviny
    """
    CREATE TABLE ingredients (
        id    INTEGER PRIMARY KEY,
        name  TEXT NOT NULL UNIQUE COLLATE NOCASE,
        unit  TEXT NOT NULL DEFAULT 'g'
    );
    CREATE TABLE recipes (
        id        INTEGER PRIMARY KEY,
        name      TEXT NOT NULL,
        category  TEXT NOT NULL DEFAULT 'hlavni',
        servings  INTEGER NOT NULL DEFAULT 1,
        steps     TEXT NOT NULL DEFAULT '',
        notes     TEXT NOT NULL DEFAULT '',
        created   TEXT NOT NULL,
        updated   TEXT NOT NULL
    );
    CREATE TABLE recipe_items (
        id            INTEGER PRIMARY KEY,
        recipe_id     INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
        ingredient_id INTEGER NOT NULL REFERENCES ingredients(id),
        amount        TEXT NOT NULL DEFAULT '',
        unit          TEXT NOT NULL DEFAULT '',
        note          TEXT NOT NULL DEFAULT '',
        pos           INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX recipe_items_recipe ON recipe_items(recipe_id);
    """,
    # 2: plán vaření (společný pro domácnost) a co už je doma
    """
    CREATE TABLE plan (
        recipe_id  INTEGER PRIMARY KEY REFERENCES recipes(id) ON DELETE CASCADE,
        portions   INTEGER NOT NULL
    );
    CREATE TABLE plan_have (
        key  TEXT PRIMARY KEY          -- surovina|skupina jednotek, viz plan.py
    );
    """,
    # 3: kolik čeho už z aktuálního plánu odešlo do nákupního seznamu --
    # znovu se posílá jen rozdíl, takže dvojí "Poslat" nic nezdvojí
    """
    CREATE TABLE plan_sent (
        key   TEXT PRIMARY KEY,
        base  TEXT NOT NULL            -- množství v základní jednotce; '' = bez množství
    );
    """,
    # 4: doba přípravy a fotka (fotka je soubor v photos/, tady jen její verze
    # -- mění se s každou novou fotkou, ať prohlížeč nevezme starou z mezipaměti)
    """
    ALTER TABLE recipes ADD COLUMN minutes INTEGER;
    ALTER TABLE recipes ADD COLUMN photo INTEGER NOT NULL DEFAULT 0;
    """,
]


class InputError(Exception):
    """Chyba ve vstupu od uživatele -- jde do UI jako srozumitelná hláška."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, data_dir: str):
        os.makedirs(data_dir, exist_ok=True)
        self.path = os.path.join(data_dir, "cooking.db")
        self.photo_dir = os.path.join(data_dir, "photos")
        os.makedirs(self.photo_dir, exist_ok=True)
        self._con = sqlite3.connect(self.path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA foreign_keys = ON")
        # Zápisy jdou přes jeden zámek -- appku používají dva lidé, ne tisíce
        self.lock = threading.Lock()
        self._migrate()

    def _migrate(self) -> None:
        ver = self._con.execute("PRAGMA user_version").fetchone()[0]
        for i, sql in enumerate(MIGRATIONS[ver:], start=ver + 1):
            with self._con:
                self._con.executescript(sql)
                self._con.execute(f"PRAGMA user_version = {i}")

    # ------------------------------------------------------------------
    # Recepty
    # ------------------------------------------------------------------
    def list_recipes(self) -> list[dict]:
        with self.lock:
            rows = self._con.execute(
                "SELECT r.id, r.name, r.category, r.servings, r.minutes, r.photo, "
                "(SELECT COUNT(*) FROM recipe_items i WHERE i.recipe_id = r.id) AS items "
                "FROM recipes r ORDER BY r.name COLLATE NOCASE").fetchall()
        return [dict(r) for r in rows]

    def get_recipe(self, rid: int) -> dict | None:
        with self.lock:
            r = self._con.execute("SELECT * FROM recipes WHERE id = ?", (rid,)).fetchone()
            if not r:
                return None
            items = self._con.execute(
                "SELECT i.amount, i.unit, i.note, g.name AS ingredient, "
                "g.id AS ingredient_id FROM recipe_items i "
                "JOIN ingredients g ON g.id = i.ingredient_id "
                "WHERE i.recipe_id = ? ORDER BY i.pos", (rid,)).fetchall()
        return {**dict(r), "items": [dict(i) for i in items]}

    def save_recipe(self, data: dict) -> int:
        """Nový recept (bez id) nebo úprava existujícího. Vrací id."""
        name = str(data.get("name") or "").strip()
        if not name:
            raise InputError("Recept musí mít název.")
        category = data.get("category") if data.get("category") in CATEGORIES else "jine"
        try:
            servings = max(1, int(data.get("servings") or 1))
        except (TypeError, ValueError):
            raise InputError("Počet porcí musí být celé číslo.") from None
        try:
            minutes = int(data["minutes"]) if str(data.get("minutes") or "").strip() else None
        except (TypeError, ValueError):
            raise InputError("Doba přípravy musí být v celých minutách.") from None
        if minutes is not None and not 0 < minutes < 10000:
            raise InputError("Doba přípravy musí být kladný počet minut.")

        items = []
        for pos, it in enumerate(data.get("items") or []):
            ing = str(it.get("ingredient") or "").strip()
            if not ing:
                continue
            raw = str(it.get("amount") or "").strip()
            amount = parse_amount(raw)
            if raw and amount is None:
                raise InputError(f"'{raw}' u suroviny {ing} není množství "
                                 f"(např. 200, 1,5 nebo 1/2).")
            unit = str(it.get("unit") or "").strip()
            if unit and unit not in UNITS:
                raise InputError(f"Neznámá jednotka '{unit}' u suroviny {ing}.")
            items.append((ing, fmt(amount) if amount else "", unit,
                          str(it.get("note") or "").strip(), pos))

        with self.lock, self._con:
            rid = data.get("id")
            fields = (name, category, servings, str(data.get("steps") or "").strip(),
                      str(data.get("notes") or "").strip(), minutes)
            if rid:
                cur = self._con.execute(
                    "UPDATE recipes SET name=?, category=?, servings=?, steps=?, notes=?, "
                    "minutes=?, updated=? WHERE id=?", (*fields, _now(), int(rid)))
                if not cur.rowcount:
                    raise InputError("Tenhle recept už neexistuje.")
                rid = int(rid)
                self._con.execute("DELETE FROM recipe_items WHERE recipe_id=?", (rid,))
            else:
                rid = self._con.execute(
                    "INSERT INTO recipes (name, category, servings, steps, notes, "
                    "minutes, created, updated) VALUES (?,?,?,?,?,?,?,?)",
                    (*fields, _now(), _now())).lastrowid
            for ing, amount, unit, note, pos in items:
                iid = self._ingredient_id(ing, unit)
                self._con.execute(
                    "INSERT INTO recipe_items (recipe_id, ingredient_id, amount, unit, "
                    "note, pos) VALUES (?,?,?,?,?,?)", (rid, iid, amount, unit, note, pos))
        return rid

    def delete_recipe(self, rid: int) -> None:
        with self.lock, self._con:
            self._con.execute("DELETE FROM recipes WHERE id=?", (rid,))
        self._remove_photo_files(rid)

    # ------------------------------------------------------------------
    # Fotky -- soubory v photos/, prohlížeč je posílá už zmenšené (JPEG)
    # ------------------------------------------------------------------
    def photo_path(self, rid: int, small: bool) -> str:
        return os.path.join(self.photo_dir, f"{rid}{'-maly' if small else ''}.jpg")

    def set_photo(self, rid: int, full: bytes, small: bytes) -> int:
        for data in (full, small):
            if not data.startswith(b"\xff\xd8"):
                raise InputError("Fotka musí být JPEG (appka ji posílá sama, zkus to znovu).")
        with self.lock, self._con:
            if not self._con.execute("SELECT 1 FROM recipes WHERE id=?", (rid,)).fetchone():
                raise InputError("Tenhle recept už neexistuje.")
            for small_, data in ((False, full), (True, small)):
                tmp = self.photo_path(rid, small_) + ".tmp"
                with open(tmp, "wb") as fh:
                    fh.write(data)
                os.replace(tmp, self.photo_path(rid, small_))
            self._con.execute("UPDATE recipes SET photo = photo + 1 WHERE id=?", (rid,))
            return self._con.execute("SELECT photo FROM recipes WHERE id=?", (rid,)).fetchone()[0]

    def delete_photo(self, rid: int) -> None:
        with self.lock, self._con:
            self._con.execute("UPDATE recipes SET photo = 0 WHERE id=?", (rid,))
        self._remove_photo_files(rid)

    def _remove_photo_files(self, rid: int) -> None:
        for small in (False, True):
            try:
                os.remove(self.photo_path(rid, small))
            except FileNotFoundError:
                pass

    # ------------------------------------------------------------------
    # Export / import -- sdílení receptů mezi instalacemi HA
    # ------------------------------------------------------------------
    def export_recipes(self, rid: int | None = None) -> list[dict]:
        """Všechny recepty, nebo jen jeden (rid)."""
        out = []
        for r in self.list_recipes():
            if rid is not None and r["id"] != rid:
                continue
            full = self.get_recipe(r["id"])
            out.append({k: full[k] for k in ("name", "category", "servings", "minutes",
                                             "steps", "notes")}
                       | {"items": [{k: it[k] for k in ("amount", "unit", "ingredient", "note")}
                                    for it in full["items"]],
                          "_id": r["id"], "_photo": bool(full["photo"])})
        return out

    def import_recipes(self, recipes: list[dict]) -> tuple[list[tuple[int, dict]], list[str]]:
        """Uloží recepty, které tu ještě nejsou (podle názvu). Vrací
        ([(nové id, importovaný recept)], [přeskočené názvy])."""
        with self.lock:
            existing = {r[0].lower() for r in self._con.execute("SELECT name FROM recipes")}
        added, skipped = [], []
        for rec in recipes:
            name = str(rec.get("name") or "").strip()
            if not name or name.lower() in existing:
                skipped.append(name or "(bez názvu)")
                continue
            data = {k: rec.get(k) for k in ("name", "category", "servings", "minutes",
                                            "steps", "notes", "items")}
            added.append((self.save_recipe(data), rec))
            existing.add(name.lower())
        return added, skipped

    # ------------------------------------------------------------------
    # Suroviny
    # ------------------------------------------------------------------
    def _ingredient_id(self, name: str, unit: str) -> int:
        row = self._con.execute("SELECT id FROM ingredients WHERE name = ?",
                                (name,)).fetchone()
        if row:
            return row["id"]
        return self._con.execute("INSERT INTO ingredients (name, unit) VALUES (?, ?)",
                                 (name, unit or "g")).lastrowid

    def list_ingredients(self) -> list[dict]:
        with self.lock:
            rows = self._con.execute(
                "SELECT g.id, g.name, g.unit, COUNT(DISTINCT i.recipe_id) AS used "
                "FROM ingredients g LEFT JOIN recipe_items i ON i.ingredient_id = g.id "
                "GROUP BY g.id ORDER BY g.name COLLATE NOCASE").fetchall()
        return [dict(r) for r in rows]

    def rename_ingredient(self, iid: int, new_name: str) -> str:
        """Přejmenuje surovinu. Když nový název už existuje, sloučí je.

        Vrací 'renamed' nebo 'merged'.
        """
        new_name = new_name.strip()
        if not new_name:
            raise InputError("Surovina musí mít název.")
        with self.lock, self._con:
            other = self._con.execute(
                "SELECT id FROM ingredients WHERE name = ? AND id != ?",
                (new_name, iid)).fetchone()
            if other:
                self._con.execute("UPDATE recipe_items SET ingredient_id=? "
                                  "WHERE ingredient_id=?", (other["id"], iid))
                self._con.execute("DELETE FROM ingredients WHERE id=?", (iid,))
                return "merged"
            self._con.execute("UPDATE ingredients SET name=? WHERE id=?", (new_name, iid))
            return "renamed"

    # ------------------------------------------------------------------
    # Plán vaření
    # ------------------------------------------------------------------
    def plan_rows(self) -> tuple[list[dict], list[dict], set[str], dict[str, str]]:
        """(recepty v plánu, jejich suroviny, klíče 'mám doma', co už odešlo)."""
        with self.lock:
            recipes = self._con.execute(
                "SELECT r.id, r.name, r.category, r.servings, p.portions "
                "FROM plan p JOIN recipes r ON r.id = p.recipe_id "
                "ORDER BY r.name COLLATE NOCASE").fetchall()
            items = self._con.execute(
                "SELECT i.recipe_id, i.amount, i.unit, g.id AS ingredient_id, "
                "g.name AS ingredient FROM recipe_items i "
                "JOIN ingredients g ON g.id = i.ingredient_id "
                "WHERE i.recipe_id IN (SELECT recipe_id FROM plan)").fetchall()
            have = {r["key"] for r in self._con.execute("SELECT key FROM plan_have")}
            sent = {r["key"]: r["base"] for r in self._con.execute("SELECT * FROM plan_sent")}
        return [dict(r) for r in recipes], [dict(i) for i in items], have, sent

    def mark_sent(self, sent: dict[str, str]) -> None:
        with self.lock, self._con:
            self._con.executemany(
                "INSERT INTO plan_sent (key, base) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET base = excluded.base", sent.items())

    def set_portions(self, rid: int, portions: int) -> None:
        with self.lock, self._con:
            if portions > 0:
                self._con.execute(
                    "INSERT INTO plan (recipe_id, portions) VALUES (?, ?) "
                    "ON CONFLICT(recipe_id) DO UPDATE SET portions = excluded.portions",
                    (rid, portions))
            else:
                self._con.execute("DELETE FROM plan WHERE recipe_id = ?", (rid,))

    def set_have(self, key: str, have: bool) -> None:
        with self.lock, self._con:
            if have:
                self._con.execute("INSERT OR IGNORE INTO plan_have (key) VALUES (?)", (key,))
            else:
                self._con.execute("DELETE FROM plan_have WHERE key = ?", (key,))

    def clear_plan(self) -> None:
        with self.lock, self._con:
            self._con.execute("DELETE FROM plan")
            self._con.execute("DELETE FROM plan_have")
            self._con.execute("DELETE FROM plan_sent")

    def delete_ingredient(self, iid: int) -> None:
        with self.lock, self._con:
            used = self._con.execute("SELECT COUNT(*) FROM recipe_items "
                                     "WHERE ingredient_id=?", (iid,)).fetchone()[0]
            if used:
                raise InputError("Surovina je v receptech, smazat ji nejde. "
                                 "Můžeš ji přejmenovat a tím sloučit s jinou.")
            self._con.execute("DELETE FROM ingredients WHERE id=?", (iid,))
