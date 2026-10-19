"""
Hlavní nákupní seznam v HA <-> plány.

Plán po "Nakoupit" posílá své suroviny do jednoho hlavního seznamu v HA
(seznam úkolů, např. "Nákupní seznam"). Stejná surovina z víc plánů je tam
jedna položka se součtem: "mrkev 800 g", v popisu "Týden30: 400 g ·
Spolubydlící: 400 g". Ručně přidané položky (jar...) appka nechává být --
vede si jen svoje (shop_items: klíč suroviny -> uid položky v HA).

Odškrtávání oběma směry:
  - v plánu "koupeno" -> položka v HA se zmenší o tenhle plán, a když je
    koupeno ve všech plánech, odškrtne se,
  - odškrtnutá (nebo smazaná) položka v HA -> koupeno ve všech plánech,
    vrácená mezi nevyřízené -> v plánech zase chybí.

Synchronizace běží na pozadí: po každé změně plánu, když se v HA změní
seznam (websocket, viz display.py) a pro jistotu každých 5 minut.
"""

from __future__ import annotations

import threading
import time
import traceback
from decimal import Decimal

from . import ha
from .plan import item_text, shopping_list
from .units import fmt, from_base

SETTING = "shopping_list"       # entita hlavního seznamu (todo.*)


def _qty(group: str, base: Decimal | None) -> str:
    if base is None:
        return ""
    amount, unit = from_base(group, base)
    return f'{fmt(amount).replace(".", ",")} {unit}'


class Shopping:
    def __init__(self, data):
        self.data = data
        self.lock = threading.Lock()
        self.error = ""
        self._wake = threading.Event()

    def entity(self) -> str:
        return self.data.get_setting(SETTING)

    def set_entity(self, entity_id: str) -> None:
        """Jiný hlavní seznam: položky ve starém zůstanou, appka je přestane vést."""
        if entity_id and not entity_id.startswith("todo."):
            raise ha.HAError("Neplatný seznam.")
        with self.lock:
            if entity_id != self.entity():
                self.data.set_setting(SETTING, entity_id)
                self.data.clear_shop_items()
        self.request()

    def start(self) -> None:
        if ha.available():
            threading.Thread(target=self._run, name="nakup", daemon=True).start()

    def request(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        while True:
            self._wake.wait(timeout=300)
            time.sleep(0.5)                 # víc změn najednou = jedna synchronizace
            self._wake.clear()
            try:
                self.sync()
            except Exception:               # noqa: BLE001 -- vlákno nesmí umřít
                traceback.print_exc()

    def sync(self) -> None:
        entity = self.entity()
        if not ha.available() or not entity:
            return
        with self.lock:
            try:
                self._sync(entity)
                self.error = ""
            except ha.HAError as exc:
                if str(exc) != self.error:
                    print(f"  Nákup: {exc}", flush=True)
                self.error = str(exc)

    # ------------------------------------------------------------------
    def _contributions(self) -> dict[str, dict]:
        """Suroviny nakupujících plánů: {klíč: {ingredient, group, parts: [...]}}."""
        out: dict[str, dict] = {}
        for plan in self.data.shopping_plans():
            for r in shopping_list(*self.data.plan_rows(plan["id"])):
                c = out.setdefault(r["key"], {"ingredient": r["ingredient"],
                                              "group": r["group"], "parts": []})
                c["parts"].append({"plan": plan["id"], "name": plan["name"],
                                   "base": Decimal(r["base"]) if r["base"] else None,
                                   "have": r["have"]})
        return out

    def _bought_everywhere(self, key: str, parts: list[dict], bought: bool) -> None:
        for pt in parts:
            self.data.set_bought(pt["plan"], key, bought)

    def _sync(self, entity: str) -> None:
        remote = {it["uid"]: it for it in ha.items(entity) if it.get("uid")}

        # 1) Co se mezitím stalo v HA
        contrib = self._contributions()
        for key, row in self.data.shop_items().items():
            parts = contrib.get(key, {}).get("parts", [])
            it = remote.get(row["uid"])
            if it is None:                                  # smazaná = už ji nepotřebuju
                self._bought_everywhere(key, parts, True)
                self.data.delete_shop_item(key)
            elif it.get("status") == "completed" and not row["done"]:
                self._bought_everywhere(key, parts, True)
                self.data.save_shop_item(key, row["uid"], row["text"], row["description"], True)
            elif it.get("status") == "needs_action" and row["done"]:
                self._bought_everywhere(key, parts, False)
                self.data.save_shop_item(key, row["uid"], row["text"], row["description"], False)

        # 2) Jak má seznam vypadat podle plánů
        contrib = self._contributions()
        known = self.data.shop_items()
        for key in sorted(set(known) | set(contrib)):
            row, c = known.get(key), contrib.get(key)
            if not c:
                # Žádný nakupující plán ji už nemá (archiv, zrušený nákup, recept pryč)
                if row and not row["done"]:
                    ha.remove_item(entity, row["uid"])
                if row:
                    self.data.delete_shop_item(key)
                continue
            todo = [pt for pt in c["parts"] if not pt["have"]]
            if not todo:                                    # koupeno ve všech plánech
                if row and not row["done"]:
                    ha.update_item(entity, row["uid"], row["text"], row["description"], True)
                    self.data.save_shop_item(key, row["uid"], row["text"], row["description"], True)
                continue
            total = (sum((pt["base"] for pt in todo), Decimal(0)) if c["group"] else None)
            text = item_text(c["ingredient"], c["group"], total)
            desc = "MAT-Cooking · " + " · ".join(
                f'{pt["name"]}: {_qty(c["group"], pt["base"])}' if pt["base"] is not None
                else pt["name"] for pt in todo)
            if row:
                if row["text"] != text or row["description"] != desc or row["done"]:
                    ha.update_item(entity, row["uid"], text, desc, False)
                    self.data.save_shop_item(key, row["uid"], text, desc, False)
                continue
            ha.add_item(entity, text, desc)
            # add_item uid nevrací -- nová položka je ta s tímhle textem, kterou appka ještě nevede
            taken = {r["uid"] for r in self.data.shop_items().values()}
            new = [it for it in ha.items(entity) if it.get("summary") == text
                   and it.get("status") == "needs_action" and it.get("uid") not in taken]
            if new:
                self.data.save_shop_item(key, new[-1]["uid"], text, desc, False)
