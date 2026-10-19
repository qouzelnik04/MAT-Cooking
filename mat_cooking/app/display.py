"""
Recept na displeji (Google Nest Hub přes Google Cast).

Appka si v HA sama udržuje:
  - dashboard "mat-cooking-displej" (vytvoří ho a přepíše při každé změně
    receptů nebo plánu -- mřížka receptů s fotkami se tak nemusí ručně
    vkládat),
  - pomocníky input_select.mat_cooking_recept (vybraný recept, nebo
    PICKER = mřížka receptů), input_number.mat_cooking_porce a
    input_select.mat_cooking_pismo (velikost písma),
  - entity sensor.mat_cooking_recept (hotové HTML receptu pro karty
    markdown) a sensor.mat_cooking_recept_foto (náhled fotky jako data: URL).

Klepnutí na displeji mění pomocníky, appka to přes websocket hned ví
a recept překreslí. Po výběru receptu nastaví porce z plánu, jinak výchozí.
Recept poslaný z appky (tlačítko Na displej) jde stejnou cestou.

Karty jsou jen ty vestavěné v HA (žádné doplňky z HACS). Karta markdown
propustí z HTML jen bezpečné značky -- velikost písma tedy přes <font size>,
barvy přes <font color>, ikony přes <ha-icon>, poznámky v <ha-alert>.
"""

from __future__ import annotations

import base64
import html
import threading
import time
import traceback
import urllib.parse
from decimal import Decimal

from . import ha
from .db import InputError
from .units import fmt, parse_amount
from .ws import WebSocket

ENTITY = "sensor.mat_cooking_recept"
PHOTO_ENTITY = "sensor.mat_cooking_recept_foto"
SELECT = "input_select.mat_cooking_recept"
NUMBER = "input_number.mat_cooking_porce"
SIZE = "input_select.mat_cooking_pismo"
DASHBOARD_PATH = "mat-cooking-displej"
VIEW_PATH = "recept"
PICKER = "📖 Výběr receptu"
# Dočasné zkušební pohledy: který se na displeji ukáže, prozradí kartu,
# kterou promítání na Nest Hubu neumí. Po zjištění se odstraní.
TEST_VIEWS = ("test-1", "test-2", "test-3", "test-4", "test-5")
MAX_PORTIONS = 50

SIZES = {"normalni": "A", "velke": "A+", "obri": "A++"}   # klíč z appky -> volba
FONT = {"A": 4, "A+": 5, "A++": 6}                        # <font size> 4 = 18 px, 6 = 32 px
ACCENT = "#e07a52"
# Barvy obrázku místo fotky podle kategorie (přechod ze světlé do tmavé)
CATEGORY_COLORS = {
    "snidane": ("#f2b45a", "#c9772b"), "hlavni": ("#e98a63", "#a8442a"),
    "polevka": ("#8cc27a", "#3f7f4d"), "svacina": ("#6fc0c0", "#2d7d86"),
    "dezert": ("#ee8fb0", "#a84a76"), "jine": ("#9aa5b5", "#566173"),
}


def _portions_text(n: int) -> str:
    return f"{n} {'porce' if n < 5 else 'porcí'}"


def _qty(it: dict, factor: Decimal) -> str:
    amount = parse_amount(it["amount"])
    if amount is None:
        return ""
    return f'{fmt(amount * factor).replace(".", ",")} {it["unit"]}'.strip()


def recipe_html(r: dict, portions: int, size: str) -> dict:
    """Hotové HTML karet receptu (atributy sensor.mat_cooking_recept).

    Každý blok začíná <div> a nemá prázdný řádek, takže ho markdown nechá
    jako HTML a nic v textu receptu se nebere jako formátování.
    """
    e = html.escape
    f = FONT.get(size, 4)
    factor = Decimal(portions) / Decimal(r["servings"] or 1)
    lines = lambda text: [s.strip() for s in (text or "").splitlines() if s.strip()]  # noqa: E731

    info = (f'<ha-icon icon="mdi:account-group"></ha-icon> {_portions_text(portions)}')
    if r["minutes"]:
        info += f' &nbsp; <ha-icon icon="mdi:timer-outline"></ha-icon> {r["minutes"]} min'
    head = (f'<div><h1>{e(r["name"])}</h1>'
            f'<font size="{max(3, f - 1)}" color="gray">{info}</font></div>')

    rows = []
    for it in r["items"]:
        qty = e(_qty(it, factor)).replace(" ", "&nbsp;")
        name = e(it["ingredient"])
        if it["note"]:
            name += f' <font color="gray">({e(it["note"])})</font>'
        rows.append(f'<tr><td align="right" valign="top"><font size="{f}" color="{ACCENT}">'
                    f'<b>{qty}</b></font></td><td valign="top"><font size="{f}">&nbsp; {name}</font></td></tr>')
    items = (f'<div><h2><ha-icon icon="mdi:basket-outline"></ha-icon> Suroviny</h2>'
             f'<table>{"".join(rows)}</table></div>' if rows else "")

    steps = [f'<tr><td align="right" valign="top"><font size="{f}" color="{ACCENT}"><b>{i}</b>'
             f'</font></td><td valign="top"><font size="{f}">&nbsp; {e(s)}</font></td></tr>'
             for i, s in enumerate(lines(r["steps"]), 1)]
    method = (f'<div><h2><ha-icon icon="mdi:chef-hat"></ha-icon> Postup</h2>'
              f'<table>{"".join(steps)}</table>' if steps else "<div>")
    notes = lines(r["notes"])
    if notes:
        method += (f'<ha-alert alert-type="info" title="Poznámky"><font size="{f}">'
                   + "<br>".join(e(s) for s in notes) + "</font></ha-alert>")
    method += "</div>"
    return {"porce": portions, "pismo": size, "html_hlavicka": head,
            "html_suroviny": items, "html_postup": method}


def placeholder(name: str, category: str) -> str:
    """Obrázek místo fotky: barevný přechod podle kategorie a první písmeno."""
    c1, c2 = CATEGORY_COLORS.get(category, CATEGORY_COLORS["jine"])
    letter = html.escape((name.strip()[:1] or "?").upper())
    svg = ("<svg xmlns='http://www.w3.org/2000/svg' width='480' height='360' viewBox='0 0 480 360'>"
           "<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
           f"<stop offset='0' stop-color='{c1}'/><stop offset='1' stop-color='{c2}'/>"
           "</linearGradient></defs><rect width='480' height='360' fill='url(#g)'/>"
           "<text x='240' y='175' font-family='Georgia,serif' font-size='150' fill='#fff' "
           f"fill-opacity='.9' text-anchor='middle' dominant-baseline='middle'>{letter}</text></svg>")
    return "data:image/svg+xml," + urllib.parse.quote(svg)


# ----------------------------------------------------------------------
# Dashboard -- jeden pohled: mřížka receptů, nebo recept (podle SELECT)
# ----------------------------------------------------------------------
def _act(service: str, entity: str, **data) -> dict:
    out = {"action": "perform-action", "perform_action": service, "target": {"entity_id": entity}}
    if data:
        out["data"] = data
    return out


def _md(content: str, text_only: bool = False) -> dict:
    out = {"type": "markdown", "content": content}
    if text_only:
        out["text_only"] = True      # bez rámečku karty -- nadpis nad mřížkou
    return out


def _button(name: str, icon: str, tap: dict) -> dict:
    return {"type": "button", "name": name, "icon": icon, "icon_height": "32px",
            "show_state": False, "tap_action": tap, "hold_action": {"action": "none"}}


def _tile(label: str, image: str, title: str) -> dict:
    return {"type": "picture-glance", "title": title, "image": image, "entities": [],
            "aspect_ratio": "4:3",
            "tap_action": _act("input_select.select_option", SELECT, option=label)}


def dashboard_config(tiles_planned: list[dict], tiles_rest: list[dict]) -> dict:
    attr = "{{ state_attr('" + ENTITY + "', '%s') or '' }}"
    picker = [_md("# Co budeme vařit?", True)]
    if tiles_planned:
        picker += [_md("## Naplánováno", True),
                   {"type": "grid", "columns": 4, "square": False, "cards": tiles_planned}]
        if tiles_rest:
            picker.append(_md("## Další recepty", True))
    if tiles_rest:
        picker.append({"type": "grid", "columns": 4, "square": False, "cards": tiles_rest})
    if not tiles_planned and not tiles_rest:
        picker.append(_md("Zatím tu nejsou žádné recepty -- přidej je v appce MAT-Cooking."))

    toolbar = {"type": "horizontal-stack", "cards": [
        _button("Recepty", "mdi:view-grid-outline",
                _act("input_select.select_option", SELECT, option=PICKER)),
        {"type": "tile", "entity": NUMBER, "name": "Porce", "icon": "mdi:account-group",
         "hide_state": True, "tap_action": {"action": "none"},
         "features": [{"type": "numeric-input", "style": "buttons"}]},
        _button("Menší písmo", "mdi:format-font-size-decrease",
                _act("input_select.select_previous", SIZE, cycle=False)),
        _button("Větší písmo", "mdi:format-font-size-increase",
                _act("input_select.select_next", SIZE, cycle=False)),
    ]}
    photo = {"type": "conditional",
             "conditions": [{"condition": "state", "entity": PHOTO_ENTITY, "state": "ano"}],
             "card": _md("{% set f = state_attr('" + PHOTO_ENTITY + "', 'src') %}"
                         '{% if f %}<center><img src="{{ f }}" height="230"></center>{% endif %}')}
    recipe = {"type": "vertical-stack", "cards": [
        toolbar,
        {"type": "horizontal-stack", "cards": [
            {"type": "vertical-stack", "cards": [_md(attr % "html_hlavicka"),
                                                 _md(attr % "html_suroviny")]},
            {"type": "vertical-stack", "cards": [photo, _md(attr % "html_postup")]},
        ]},
    ]}
    columns = recipe["cards"][1]
    tests = [
        ("test-1", _md("# Test 1\nObyčejný text.")),
        ("test-2", {"type": "vertical-stack", "cards": [_md("# Test 2"), *recipe["cards"]]}),
        ("test-3", {"type": "vertical-stack", "cards": [_md("# Test 3"), columns]}),
        ("test-4", {"type": "vertical-stack", "cards": [_md("# Test 4"), *picker]}),
        ("test-5", {"type": "vertical-stack", "cards": [_md("# Test 5"), {
            "type": "conditional",
            "conditions": [{"condition": "state", "entity": SELECT, "state_not": "xxx"}],
            "card": _md("Podmíněná karta funguje.")}]}),
    ]
    return {"title": "MAT-Cooking displej", "views": [{
        "title": "Recept", "path": VIEW_PATH, "type": "panel", "cards": [{
            "type": "vertical-stack", "cards": [
                {"type": "conditional",
                 "conditions": [{"condition": "state", "entity": SELECT, "state": PICKER}],
                 "card": {"type": "vertical-stack", "cards": picker}},
                {"type": "conditional",
                 "conditions": [{"condition": "state", "entity": SELECT, "state_not": PICKER}],
                 "card": recipe},
            ]}],
    }] + [{"title": path, "path": path, "type": "panel", "cards": [card]} for path, card in tests]}


# ----------------------------------------------------------------------
class Display:
    def __init__(self, data):
        self.data = data
        self.lock = threading.RLock()
        # Změny pomocníků, které udělala appka sama -- na ty se nereaguje
        self.expect_select: str | None = None
        self.expect_number: int | None = None
        self.size = "A"
        self.controls = False       # pomocníci a dashboard v HA jsou, appka je sleduje
        self.error = ""
        self._photo_key = None      # jaká fotka je v HA (aby se neposílala znovu)
        self._last_labels: dict[str, int] = {}  # popisky při poslední synchronizaci
        self._dashboard = None      # naposledy uložený dashboard
        self._cmd = None            # příkaz websocketu (jen během spojení)
        self._sync = threading.Event()

    def start(self) -> None:
        if ha.available():
            threading.Thread(target=self._run, name="displej", daemon=True).start()

    def status(self) -> dict:
        return {"controls": self.controls, "error": self.error}

    def request_sync(self) -> None:
        """Recepty nebo plán se změnily -- obnoví mřížku a recept na displeji."""
        self._sync.set()

    # ------------------------------------------------------------------
    # Z appky: recept na displej
    # ------------------------------------------------------------------
    def show(self, entity_id: str, rid: int, portions: int | None, size: str,
             view: str = "") -> None:
        if not ha.available():
            raise InputError("Appka neběží v Home Assistantu -- na displej nejde poslat.")
        if not entity_id.startswith("media_player."):
            raise InputError("Vyber displej.")
        r = self.data.get_recipe(rid)
        if not r:
            raise InputError("Recept neexistuje.")
        portions = min(MAX_PORTIONS, max(1, int(portions or r["servings"])))
        with self.lock:
            if size in SIZES:
                self.size = SIZES[size]
            if self.controls:
                try:
                    self._set_helpers(rid, portions)
                except ha.HAError as exc:
                    print(f"  Displej: pomocníky nejde nastavit: {exc}", flush=True)
            self._render(rid, portions)
        ha.call_service("cast", "show_lovelace_view", {
            "entity_id": entity_id, "dashboard_path": DASHBOARD_PATH,
            "view_path": view if view in TEST_VIEWS else VIEW_PATH})

    # ------------------------------------------------------------------
    def _options(self) -> list[tuple[str, int]]:
        """[(popisek, id receptu)] -- naplánované první, popisky jedinečné."""
        planned = {r["id"] for r in self.data.plan_rows()[0]}
        recipes = sorted(self.data.list_recipes(),
                         key=lambda r: (r["id"] not in planned, r["name"].lower()))
        out, seen = [], {PICKER.lower()}
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

    def _image(self, r: dict) -> str:
        if r["photo"]:
            try:
                with open(self.data.photo_path(r["id"], True), "rb") as fh:
                    return "data:image/jpeg;base64," + base64.b64encode(fh.read()).decode()
            except OSError:
                pass
        return placeholder(r["name"], r["category"])

    def _dashboard_config(self) -> dict:
        planned = {r["id"]: r["portions"] for r in self.data.plan_rows()[0]}
        recipes = {r["id"]: r for r in self.data.list_recipes()}
        tiles_planned, tiles_rest = [], []
        for label, rid in self._options():
            r = recipes[rid]
            if rid in planned:
                tiles_planned.append(_tile(label, self._image(r),
                                           f"{label} · {_portions_text(int(planned[rid]))}"))
            else:
                tiles_rest.append(_tile(label, self._image(r), label))
        return dashboard_config(tiles_planned, tiles_rest)

    def _sync_all(self) -> None:
        """Výběr receptů, dashboard a recept na displeji podle aktuálních dat."""
        options = self._options()
        labels = [PICKER] + [label for label, _ in options]
        st = ha.get_state(SELECT)
        last, self._last_labels = self._last_labels, dict(options)
        if not st or list((st.get("attributes") or {}).get("options") or []) != labels:
            cur = (st or {}).get("state")
            renamed = next((lb for lb, i in options if i == last.get(cur)), None)
            if cur and cur not in labels and renamed:
                # Recept na displeji se přejmenoval: přepnout na nový název tak,
                # aby HA mezitím nevybralo jiný recept a nezměnily se porce
                ha.call_service("input_select", "set_options",
                                {"entity_id": SELECT, "options": labels + [cur]})
                self.expect_select = renamed
                ha.call_service("input_select", "select_option",
                                {"entity_id": SELECT, "option": renamed})
            # Když aktuální recept v seznamu není (smazaný), HA vybere první
            # volbu -- mřížku receptů
            ha.call_service("input_select", "set_options", {"entity_id": SELECT, "options": labels})
        if self._cmd:
            config = self._dashboard_config()
            if config != self._dashboard:
                self._cmd({"type": "lovelace/config/save", "url_path": DASHBOARD_PATH,
                           "config": config})
                self._dashboard = config
        self._refresh()

    def _set_helpers(self, rid: int, portions: int) -> None:
        self._sync_all()
        label = next((lb for lb, i in self._options() if i == rid), None)
        if label and (ha.get_state(SELECT) or {}).get("state") != label:
            self.expect_select = label
            ha.call_service("input_select", "select_option", {"entity_id": SELECT, "option": label})
        if self._number(ha.get_state(NUMBER)) != portions:
            self.expect_number = portions
            ha.call_service("input_number", "set_value", {"entity_id": NUMBER, "value": portions})
        if (ha.get_state(SIZE) or {}).get("state") != self.size:
            ha.call_service("input_select", "select_option", {"entity_id": SIZE, "option": self.size})

    def _refresh(self) -> None:
        """Překreslí recept podle pomocníků (po startu, restartu HA, úpravě receptu)."""
        sel = (ha.get_state(SELECT) or {}).get("state")
        if sel == PICKER:
            return
        rid = dict(self._options()).get(sel)
        portions = self._number(ha.get_state(NUMBER))
        self._render(rid, portions or (self._default_portions(rid) if rid else 1))

    def _on_change(self, entity: str, state: str) -> None:
        with self.lock:
            if entity == SELECT:
                if state == self.expect_select:
                    self.expect_select = None
                    return
                self.expect_select = None
                if state == PICKER:
                    return
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
                self._refresh()
            elif entity == SIZE and state in FONT:
                self.size = state
                self._refresh()

    def _render(self, rid: int | None, portions: int) -> None:
        r = self.data.get_recipe(rid) if rid else None
        base = {"friendly_name": "MAT-Cooking recept", "icon": "mdi:chef-hat"}
        if not r:
            ha.set_state(ENTITY, "Žádný recept", {
                **base, "porce": 0, "pismo": self.size, "html_suroviny": "", "html_postup": "",
                "html_hlavicka": "<div><h1>Recept nenalezen</h1></div>"})
        else:
            ha.set_state(ENTITY, r["name"][:255], {**base, **recipe_html(r, portions, self.size)})
        key = (r["id"], r["photo"]) if r and r["photo"] else None
        if key == self._photo_key:
            return
        src = self._image(r) if key else ""
        # Fotka je v atributu jako data: URL -- displej ji tak dostane bez
        # zvláštního přístupu k souborům. HA ji do historie neukládá (je velká).
        ha.set_state(PHOTO_ENTITY, "ano" if src else "ne",
                     {"friendly_name": "MAT-Cooking fotka receptu", "icon": "mdi:image",
                      "src": src})
        self._photo_key = key

    # ------------------------------------------------------------------
    # Websocket: pomocníci, dashboard a sledování změn z displeje
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
            with self.lock:
                self.controls = False
                self._cmd = None
                self._photo_key = None
                self._dashboard = None
            delay = 5 if time.monotonic() - started > 120 else min(delay * 2, 120)
            time.sleep(delay)

    def _session(self) -> None:
        ws = WebSocket(ha.ws_url())
        next_id = iter(range(1, 10**9))
        events: list[dict] = []     # události, které přišly během čekání na odpověď

        def cmd(payload: dict):
            i = next(next_id)
            ws.send({"id": i, **payload})
            while True:
                msg = ws.recv()
                if msg.get("type") == "event":
                    events.append(msg)
                elif msg.get("id") == i and msg.get("type") == "result":
                    if not msg.get("success"):
                        raise ha.HAError(f'{payload["type"]}: '
                                         f'{(msg.get("error") or {}).get("message") or msg}')
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
            selects = {x.get("id") for x in cmd({"type": "input_select/list"})}
            if "mat_cooking_recept" not in selects:
                cmd({"type": "input_select/create", "name": "MAT-Cooking recept",
                     "options": [PICKER], "icon": "mdi:chef-hat"})
            if "mat_cooking_pismo" not in selects:
                cmd({"type": "input_select/create", "name": "MAT-Cooking písmo",
                     "options": list(FONT), "icon": "mdi:format-size"})
            if DASHBOARD_PATH not in {d.get("url_path")
                                      for d in cmd({"type": "lovelace/dashboards/list"})}:
                cmd({"type": "lovelace/dashboards/create", "url_path": DASHBOARD_PATH,
                     "title": "MAT-Cooking displej", "icon": "mdi:chef-hat",
                     "show_in_sidebar": False, "require_admin": False, "mode": "storage"})

            sub = next(next_id)
            ws.send({"id": sub, "type": "subscribe_trigger",
                     "trigger": {"platform": "state", "entity_id": [SELECT, NUMBER, SIZE]}})
            while True:
                msg = ws.recv()
                if msg.get("id") == sub and msg.get("type") == "result":
                    if not msg.get("success"):
                        raise ha.HAError(str(msg.get("error")))
                    break

            with self.lock:
                self._cmd = cmd
                self._sync.clear()
                size = (ha.get_state(SIZE) or {}).get("state")
                if size in FONT:
                    self.size = size
                self._sync_all()
                self.controls, self.error = True, ""
            print("  Displej: ovládání z displeje připojeno.", flush=True)

            last_ping = time.monotonic()
            while True:
                if self._sync.is_set():
                    time.sleep(0.5)             # víc změn najednou = jedno obnovení
                    self._sync.clear()
                    with self.lock:
                        self._sync_all()
                if time.monotonic() - last_ping > 50:
                    ws.send({"id": next(next_id), "type": "ping"})
                    last_ping = time.monotonic()
                if events:
                    msg = events.pop(0)
                elif ws.readable(1.0):
                    msg = ws.recv()
                else:
                    continue
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
