"""
Recept na displeji (Google Nest Hub přes Google Cast).

Appka zapíše recept do entit sensor.mat_cooking_recept (text) a
sensor.mat_cooking_recept_foto (zmenšená fotka) a HA promítne na displej
dashboard "mat-cooking-displej", který je ukazuje.

Ovládání přímo z displeje: appka si v HA vytvoří dva pomocníky,
input_select.mat_cooking_recept (výběr receptu, naplánované první) a
input_number.mat_cooking_porce. Tlačítka na dashboardu je přepínají a appka
přes websocket hned překreslí recept. Po přepnutí receptu nastaví porce
z plánu, jinak výchozí počet z receptu.
"""

from __future__ import annotations

import base64
import threading
import time
import traceback
from decimal import Decimal

from . import ha
from .db import InputError
from .units import fmt, parse_amount
from .ws import WebSocket

ENTITY = "sensor.mat_cooking_recept"
PHOTO_ENTITY = "sensor.mat_cooking_recept_foto"
SELECT = "input_select.mat_cooking_recept"
NUMBER = "input_number.mat_cooking_porce"
DASHBOARD_PATH = "mat-cooking-displej"
NO_RECIPE = "(žádný recept)"
MAX_PORTIONS = 50

# klíč z appky -> (cesta pohledu, tlačítko, předpona řádku)
# Větší písmo = řádky jako nadpisy markdownu (jiný způsob HA bez doplňků nemá).
SIZES = {
    "normalni": ("recept", "A", "- "),
    "velke": ("recept-velke", "A+", "### "),
    "obri": ("recept-obri", "A++", "## "),
}


# ----------------------------------------------------------------------
# Dashboard -- uživatel ho jednou vloží do HA, appka ho ukazuje ke zkopírování
# ----------------------------------------------------------------------
def _button(name: str, tap: list[str]) -> list[str]:
    return ["              - type: button",
            f'                name: "{name}"',
            "                show_icon: false",
            "                tap_action:",
            *[" " * 18 + t for t in tap]]


def _action(service: str, entity: str) -> list[str]:
    return ["action: perform-action", f"perform_action: {service}",
            "target:", f"  entity_id: {entity}"]


def dashboard_yaml() -> str:
    attr = "state_attr('" + ENTITY + "', '{}')"
    out = ["title: MAT-Cooking displej", "views:"]
    for key, (path, label, prefix) in SIZES.items():
        buttons = (_button("◀ Recept", _action("input_select.select_previous", SELECT))
                   + _button("Recept ▶", _action("input_select.select_next", SELECT))
                   + _button("− Porce", _action("input_number.decrement", NUMBER))
                   + _button("+ Porce", _action("input_number.increment", NUMBER)))
        for path2, label2, _ in SIZES.values():
            buttons += _button(("● " if path2 == path else "") + label2,
                               ["action: navigate",
                                f"navigation_path: /{DASHBOARD_PATH}/{path2}"])
        step = "{{ loop.index }}. " if key == "normalni" else prefix + "{{ loop.index }}. "
        out += [
            f"  - title: Recept {label}",
            f"    path: {path}",
            "    type: panel",
            "    cards:",
            "      - type: vertical-stack",
            "        cards:",
            "          - type: horizontal-stack",
            "            cards:",
            *buttons,
            "          - type: markdown",
            "            content: |",
            f"              # {{{{ states('{ENTITY}') }}}}",
            f"              **{{{{ {attr.format('info')} }}}}**",
            "",
            f"              {{% set f = state_attr('{PHOTO_ENTITY}', 'src') %}}"
            '{% if f %}<img src="{{ f }}" width="360">{% endif %}',
            "",
            f"              {{% for s in {attr.format('suroviny')} or [] -%}}",
            f"              {prefix}{{{{ s }}}}",
            "              {% endfor %}",
            "          - type: markdown",
            "            content: |",
            "              ## Postup",
            "",
            f"              {{% for s in {attr.format('postup')} or [] -%}}",
            f"              {step}{{{{ s }}}}",
            "              {% endfor %}",
            f"              {{% set p = {attr.format('poznamky')} or [] %}}{{% if p %}}",
            "",
            "              ## Poznámky",
            "",
            "              {% for s in p -%}",
            f"              {prefix}{{{{ s }}}}",
            "              {% endfor %}{% endif %}",
        ]
    return "\n".join(out) + "\n"


def recipe_attributes(r: dict, portions: int) -> dict:
    """Suroviny přepočtené na porce (česky), postup a poznámky po řádcích."""
    factor = Decimal(portions) / Decimal(r["servings"] or 1)
    items = []
    for it in r["items"]:
        amount = parse_amount(it["amount"])
        qty = (f'{fmt(amount * factor).replace(".", ",")} {it["unit"]}'.strip()
               if amount is not None else "")
        items.append(" ".join(x for x in (qty, it["ingredient"],
                                          f'({it["note"]})' if it["note"] else "") if x))
    info = f"{portions} {'porce' if portions < 5 else 'porcí'}"
    if r["minutes"]:
        info += f' · {r["minutes"]} min'
    lines = lambda text: [s.strip() for s in (text or "").splitlines() if s.strip()]  # noqa: E731
    return {"porce": portions, "info": info, "suroviny": items,
            "postup": lines(r["steps"]), "poznamky": lines(r["notes"])}


# ----------------------------------------------------------------------
class Display:
    def __init__(self, data):
        self.data = data
        self.lock = threading.RLock()
        # Změny pomocníků, které udělala appka sama -- na ty se nereaguje
        self.expect_select: str | None = None
        self.expect_number: int | None = None
        self.controls = False       # pomocníci v HA jsou a appka je sleduje
        self.error = ""
        self._photo_key = None      # jaká fotka je v HA (aby se neposílala znovu)
        self._last_labels: dict[str, int] = {}  # popisky při poslední synchronizaci
        self._sync = threading.Event()

    def start(self) -> None:
        if ha.available():
            threading.Thread(target=self._run, name="displej", daemon=True).start()

    def status(self) -> dict:
        return {"controls": self.controls, "error": self.error}

    def request_sync(self) -> None:
        """Recepty nebo plán se změnily -- obnoví výběr receptů na displeji."""
        self._sync.set()

    # ------------------------------------------------------------------
    # Z appky: recept na displej
    # ------------------------------------------------------------------
    def show(self, entity_id: str, rid: int, portions: int | None, size: str) -> None:
        if not ha.available():
            raise InputError("Appka neběží v Home Assistantu -- na displej nejde poslat.")
        if not entity_id.startswith("media_player."):
            raise InputError("Vyber displej.")
        r = self.data.get_recipe(rid)
        if not r:
            raise InputError("Recept neexistuje.")
        portions = min(MAX_PORTIONS, max(1, int(portions or r["servings"])))
        with self.lock:
            if self.controls:
                try:
                    self._set_helpers(rid, portions)
                except ha.HAError as exc:
                    print(f"  Displej: pomocníky nejde nastavit: {exc}", flush=True)
            self._render(rid, portions)
        view = SIZES.get(size, SIZES["normalni"])[0]
        ha.call_service("cast", "show_lovelace_view", {
            "entity_id": entity_id, "dashboard_path": DASHBOARD_PATH, "view_path": view})

    # ------------------------------------------------------------------
    def _options(self) -> list[tuple[str, int]]:
        """[(popisek, id receptu)] -- naplánované první, popisky jedinečné."""
        planned = {r["id"] for r in self.data.plan_rows()[0]}
        recipes = sorted(self.data.list_recipes(),
                         key=lambda r: (r["id"] not in planned, r["name"].lower()))
        out, seen = [], set()
        for r in recipes:
            base = r["name"].strip()[:200]
            label, n = base, 2
            while label.lower() in seen:
                label, n = f"{base} ({n})", n + 1
            seen.add(label.lower())
            out.append((label, r["id"]))
        return out

    def _default_portions(self, rid: int) -> int:
        for r in self.data.plan_rows()[0]:
            if r["id"] == rid:
                return min(MAX_PORTIONS, max(1, int(r["portions"])))
        r = self.data.get_recipe(rid)
        return min(MAX_PORTIONS, max(1, int(r["servings"] if r else 1)))

    @staticmethod
    def _number(state: dict | None) -> int | None:
        try:
            return int(float(state["state"]))
        except (TypeError, KeyError, ValueError):
            return None

    def _sync_options(self) -> None:
        options = self._options()
        labels = [label for label, _ in options] or [NO_RECIPE]
        st = ha.get_state(SELECT)
        last, self._last_labels = self._last_labels, dict(options)
        if st and list((st.get("attributes") or {}).get("options") or []) == labels:
            return
        cur = (st or {}).get("state")
        renamed = next((lb for lb, i in options if i == last.get(cur)), None)
        if cur and cur not in labels and renamed:
            # Recept na displeji se přejmenoval: přepnout na nový název tak,
            # aby HA mezitím nevybralo jiný recept a nezměnily se porce
            ha.call_service("input_select", "set_options",
                            {"entity_id": SELECT, "options": labels + [cur]})
            self.expect_select = renamed
            ha.call_service("input_select", "select_option", {"entity_id": SELECT, "option": renamed})
        # Když aktuální recept v novém seznamu není (smazaný), HA vybere první
        # -- appka pak dostane změnu a ukáže ho
        ha.call_service("input_select", "set_options", {"entity_id": SELECT, "options": labels})

    def _set_helpers(self, rid: int, portions: int) -> None:
        self._sync_options()
        label = next((lb for lb, i in self._options() if i == rid), None)
        if label and (ha.get_state(SELECT) or {}).get("state") != label:
            self.expect_select = label
            ha.call_service("input_select", "select_option", {"entity_id": SELECT, "option": label})
        if self._number(ha.get_state(NUMBER)) != portions:
            self.expect_number = portions
            ha.call_service("input_number", "set_value", {"entity_id": NUMBER, "value": portions})

    def _refresh(self) -> None:
        """Překreslí recept podle pomocníků (po startu, po restartu HA, po úpravě receptu)."""
        rid = dict(self._options()).get((ha.get_state(SELECT) or {}).get("state"))
        portions = self._number(ha.get_state(NUMBER))
        self._render(rid, portions or (self._default_portions(rid) if rid else 1))

    def _on_change(self, entity: str, state: str) -> None:
        with self.lock:
            if entity == SELECT:
                if state == self.expect_select:
                    self.expect_select = None
                    return
                self.expect_select = None
                rid = dict(self._options()).get(state)
                portions = self._default_portions(rid) if rid else 1
                if self._number(ha.get_state(NUMBER)) != portions:
                    self.expect_number = portions
                    ha.call_service("input_number", "set_value",
                                    {"entity_id": NUMBER, "value": portions})
                self._render(rid, portions)
            elif entity == NUMBER:
                value = self._number({"state": state})
                if value is None:
                    return
                if value == self.expect_number:
                    self.expect_number = None
                    return
                self.expect_number = None
                rid = dict(self._options()).get((ha.get_state(SELECT) or {}).get("state"))
                self._render(rid, value)

    def _render(self, rid: int | None, portions: int) -> None:
        r = self.data.get_recipe(rid) if rid else None
        base = {"friendly_name": "MAT-Cooking recept", "icon": "mdi:chef-hat"}
        if not r:
            ha.set_state(ENTITY, "Žádný recept", {**base, "porce": 0, "info": "", "suroviny": [],
                                                  "postup": [], "poznamky": []})
        else:
            ha.set_state(ENTITY, r["name"][:255], {**base, **recipe_attributes(r, portions)})
        key = (r["id"], r["photo"]) if r and r["photo"] else None
        if key == self._photo_key:
            return
        src = ""
        if key:
            try:
                with open(self.data.photo_path(r["id"], True), "rb") as fh:
                    src = "data:image/jpeg;base64," + base64.b64encode(fh.read()).decode()
            except OSError:
                key = None
        # Fotka je v atributu jako data: URL -- displej ji tak dostane bez
        # zvláštního přístupu k souborům. HA ji do historie neukládá (je velká).
        ha.set_state(PHOTO_ENTITY, "ano" if src else "ne",
                     {"friendly_name": "MAT-Cooking fotka receptu", "icon": "mdi:image",
                      "src": src})
        self._photo_key = key

    # ------------------------------------------------------------------
    # Websocket: pomocníci a sledování změn z displeje
    # ------------------------------------------------------------------
    def _run(self) -> None:
        delay = 5
        while True:
            started = time.monotonic()
            try:
                self._session()
            except Exception as exc:        # noqa: BLE001
                msg = f"{type(exc).__name__}: {exc}"
                if msg != self.error:
                    print(f"  Displej: ovládání z displeje nejede ({msg}), zkusím znovu.",
                          flush=True)
                    if not isinstance(exc, (OSError, ConnectionError, ha.HAError)):
                        traceback.print_exc()
                self.error = msg
            self.controls = False
            self._photo_key = None
            delay = 5 if time.monotonic() - started > 120 else min(delay * 2, 120)
            time.sleep(delay)

    def _session(self) -> None:
        ws = WebSocket(ha.ws_url())
        next_id = iter(range(1, 10**9))

        def cmd(payload: dict):
            i = next(next_id)
            ws.send({"id": i, **payload})
            while True:
                msg = ws.recv()
                if msg.get("id") == i and msg.get("type") == "result":
                    if not msg.get("success"):
                        raise ha.HAError((msg.get("error") or {}).get("message") or str(msg))
                    return msg.get("result")

        try:
            if ws.recv().get("type") != "auth_required":
                raise ha.HAError("Neočekávaná odpověď HA.")
            ws.send({"type": "auth", "access_token": ha.TOKEN})
            if ws.recv().get("type") != "auth_ok":
                raise ha.HAError("HA nepřijal přihlášení add-onu.")

            if "mat_cooking_porce" not in {x.get("id") for x in cmd({"type": "input_number/list"})}:
                cmd({"type": "input_number/create", "name": "MAT-Cooking porce", "min": 1,
                     "max": MAX_PORTIONS, "step": 1, "mode": "box", "icon": "mdi:account-group"})
            if "mat_cooking_recept" not in {x.get("id") for x in cmd({"type": "input_select/list"})}:
                with self.lock:
                    options = [label for label, _ in self._options()] or [NO_RECIPE]
                cmd({"type": "input_select/create", "name": "MAT-Cooking recept",
                     "options": options, "icon": "mdi:chef-hat"})

            sub = next(next_id)
            ws.send({"id": sub, "type": "subscribe_trigger",
                     "trigger": {"platform": "state", "entity_id": [SELECT, NUMBER]}})
            while True:
                msg = ws.recv()
                if msg.get("id") == sub and msg.get("type") == "result":
                    if not msg.get("success"):
                        raise ha.HAError(str(msg.get("error")))
                    break

            with self.lock:
                self._sync.clear()
                self._sync_options()
                self._refresh()
                self.controls, self.error = True, ""
            print("  Displej: ovládání z displeje připojeno.", flush=True)

            last_ping = time.monotonic()
            while True:
                if self._sync.is_set():
                    time.sleep(0.5)             # víc změn najednou = jedno obnovení
                    self._sync.clear()
                    with self.lock:
                        self._sync_options()
                        self._refresh()
                if time.monotonic() - last_ping > 50:
                    ws.send({"id": next(next_id), "type": "ping"})
                    last_ping = time.monotonic()
                if not ws.readable(1.0):
                    continue
                msg = ws.recv()
                if msg.get("id") != sub or msg.get("type") != "event":
                    continue
                trig = ((msg.get("event") or {}).get("variables") or {}).get("trigger") or {}
                to, fr = trig.get("to_state"), trig.get("from_state")
                # Změna jen atributů (např. nový seznam receptů) není přepnutí
                if not to or (fr and fr.get("state") == to.get("state")):
                    continue
                try:
                    self._on_change(trig.get("entity_id"), to.get("state"))
                except ha.HAError as exc:
                    print(f"  Displej: {exc}", flush=True)
        finally:
            ws.close()
