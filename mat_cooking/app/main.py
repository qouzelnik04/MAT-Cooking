"""
Webový server MAT-Cooking.

V Home Assistantu běží jako add-on za Ingressem: přístup jen přes přihlášení
do HA, žádný port ven, a server přijme jen požadavky od Ingress proxy
(MAT_ALLOW_ONLY). Lokálně (START.bat) běží na 127.0.0.1 a přijme jen
požadavky adresované přímo jemu -- ochrana proti cizím webovým stránkám
(DNS rebinding, CSRF).

Proměnné prostředí (nastavuje je Dockerfile add-onu):
    MAT_HOST, MAT_PORT    kde server poslouchá
    MAT_DATA              složka s databází
    MAT_ALLOW_ONLY        jediné IP, ze kterých se smí přijít (HA Ingress)
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import socket
import threading
import traceback
import unicodedata
import urllib.parse
import webbrowser
from decimal import Decimal
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import APP_NAME, APP_VERSION, ha
from .db import CATEGORIES, DB, InputError
from .plan import item_text, parse_item, shopping_list
from .units import UNITS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(ROOT, "web")
VERSIONED = re.compile(r"v[0-9][0-9.]*/[^/]+$")
ALLOW_ONLY = {ip.strip() for ip in (os.environ.get("MAT_ALLOW_ONLY") or "").split(",")
              if ip.strip()}

DATA: DB | None = None
# Odeslání do nákupního seznamu najednou jen jedno -- dva lidé ve stejnou
# chvíli by jinak poslali stejný přírůstek dvakrát.
SEND_LOCK = threading.Lock()
HOST = os.environ.get("MAT_HOST") or "127.0.0.1"
PORT = int(os.environ.get("MAT_PORT") or 8720)


class Handler(BaseHTTPRequestHandler):
    server_version = f"{APP_NAME}/{APP_VERSION}"

    def log_message(self, fmt, *args):
        if str(args[1] if len(args) > 1 else "").startswith(("4", "5")):
            super().log_message(fmt, *args)

    def _allowed(self, write: bool) -> bool:
        if ALLOW_ONLY:
            # Add-on: smí jen Ingress proxy HA. Ta přihlášení ověřila sama.
            if self.client_address[0] in ALLOW_ONLY:
                return True
            self._err(403, "Přístup jen přes Home Assistant.")
            return False
        ok_hosts = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
        if self.headers.get("Host", "") not in ok_hosts:
            self._err(403, "Neplatná adresa.")
            return False
        origin = self.headers.get("Origin")
        if write and origin and origin not in {f"http://{h}" for h in ok_hosts}:
            self._err(403, "Zápis je dovolený jen z vlastní stránky appky.")
            return False
        return True

    # ------------------------------------------------------------------
    def do_GET(self):
        if not self._allowed(write=False):
            return
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/":
                return self._index()
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if path == "/api/meta":
                return self._json({"app": APP_NAME, "version": APP_VERSION,
                                   "units": UNITS, "categories": CATEGORIES})
            if path == "/api/recipes":
                return self._json(DATA.list_recipes())
            m = re.fullmatch(r"/api/recipes/(\d+)", path)
            if m:
                r = DATA.get_recipe(int(m.group(1)))
                return self._json(r) if r else self._err(404, "Recept neexistuje.")
            m = re.fullmatch(r"/api/recipes/(\d+)/photo", path)
            if m:
                return self._photo(int(m.group(1)), "small" in self.path)
            if path == "/api/export":
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                rid = q.get("id", [""])[0]
                return self._export(int(rid) if rid.isdigit() else None)
            if path == "/api/ingredients":
                return self._json(DATA.list_ingredients())
            if path == "/api/plan":
                return self._json(_plan())
            if path == "/api/lists":
                if not ha.available():
                    return self._json({"available": False, "lists": []})
                return self._json({"available": True, "lists": ha.todo_lists()})
            return self._err(404, "Taková adresa tu není.")
        except ha.HAError as exc:
            return self._err(502, str(exc))
        except Exception as exc:            # noqa: BLE001
            traceback.print_exc()
            return self._err(500, f"Chyba na serveru: {exc}")

    def do_POST(self):
        if not self._allowed(write=True):
            return
        path = urllib.parse.urlparse(self.path).path
        try:
            body = self._body_json()
            if path == "/api/recipes":
                return self._json({"id": DATA.save_recipe(body)})
            m = re.fullmatch(r"/api/recipes/(\d+)/delete", path)
            if m:
                DATA.delete_recipe(int(m.group(1)))
                return self._json({"ok": True})
            m = re.fullmatch(r"/api/recipes/(\d+)/photo", path)
            if m:
                return self._json({"photo": DATA.set_photo(
                    int(m.group(1)), _b64(body.get("full")), _b64(body.get("small")))})
            m = re.fullmatch(r"/api/recipes/(\d+)/photo/delete", path)
            if m:
                DATA.delete_photo(int(m.group(1)))
                return self._json({"ok": True})
            if path == "/api/import":
                return self._json(_import(body))
            m = re.fullmatch(r"/api/ingredients/(\d+)/rename", path)
            if m:
                return self._json({"result": DATA.rename_ingredient(
                    int(m.group(1)), str(body.get("name") or ""))})
            m = re.fullmatch(r"/api/ingredients/(\d+)/delete", path)
            if m:
                DATA.delete_ingredient(int(m.group(1)))
                return self._json({"ok": True})
            if path == "/api/plan/portions":
                DATA.set_portions(int(body.get("recipe_id")), max(0, int(body.get("portions") or 0)))
                return self._json(_plan())
            if path == "/api/plan/have":
                DATA.set_have(str(body.get("key") or ""), bool(body.get("have")))
                return self._json({"ok": True})
            if path == "/api/plan/clear":
                DATA.clear_plan()
                return self._json(_plan())
            if path == "/api/plan/send":
                with SEND_LOCK:
                    return self._json(_send(str(body.get("entity_id") or "")))
            return self._err(404, "Taková adresa tu není.")
        except InputError as exc:
            return self._err(400, str(exc))
        except ha.HAError as exc:
            return self._err(502, str(exc))
        except Exception as exc:            # noqa: BLE001
            traceback.print_exc()
            return self._err(500, f"Chyba na serveru: {exc}")

    # ------------------------------------------------------------------
    def _index(self) -> None:
        """index.html s verzí v cestě k app.js a style.css.

        Mezipaměť po cestě (service worker HA, Cloudflare) ignoruje ?v=...,
        takže verze je přímo v cestě: static/v0.1.0/app.js je pro ni jiný
        soubor a po aktualizaci se nikdy nenačte starý skript.
        """
        with open(os.path.join(WEB_DIR, "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        for name in ("app.js", "style.css"):
            html = html.replace(f'"static/{name}"', f'"static/v{APP_VERSION}/{name}"')
        self._raw(html.encode("utf-8"), "text/html; charset=utf-8")

    def _static(self, rel: str) -> None:
        rel = urllib.parse.unquote(rel)
        if VERSIONED.match(rel):
            rel = rel.split("/", 1)[1]
        full = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not full.startswith(os.path.normpath(WEB_DIR) + os.sep) or not os.path.isfile(full):
            return self._err(404, "Soubor neexistuje.")
        ctype = {".js": "application/javascript", ".css": "text/css",
                 ".svg": "image/svg+xml"}.get(os.path.splitext(full)[1], "text/plain")
        with open(full, "rb") as fh:
            self._raw(fh.read(), ctype + "; charset=utf-8")

    def _photo(self, rid: int, small: bool) -> None:
        try:
            with open(DATA.photo_path(rid, small), "rb") as fh:
                data = fh.read()
        except FileNotFoundError:
            return self._err(404, "Recept nemá fotku.")
        # Adresa obsahuje verzi fotky (?v=N), takže ji prohlížeč smí držet dlouho
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "private, max-age=31536000")
        self.end_headers()
        self.wfile.write(data)

    def _export(self, rid: int | None) -> None:
        """Soubor s recepty (všemi, nebo jedním) pro import na jiném HA."""
        recipes = DATA.export_recipes(rid)
        if rid is not None and not recipes:
            return self._err(404, "Recept neexistuje.")
        for r in recipes:
            rid, has = r.pop("_id"), r.pop("_photo")
            if has:
                with open(DATA.photo_path(rid, False), "rb") as fh:
                    r["photo"] = base64.b64encode(fh.read()).decode()
                with open(DATA.photo_path(rid, True), "rb") as fh:
                    r["photo_small"] = base64.b64encode(fh.read()).decode()
        body = json.dumps({"format": "mat-cooking", "version": 1, "recipes": recipes},
                          ensure_ascii=False, indent=1).encode("utf-8")
        name = (f"mat-cooking-{_slug(recipes[0]['name'])}.json" if rid is not None
                else f"mat-cooking-recepty-{date.today().isoformat()}.json")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body_json(self) -> dict:
        # Import receptů s fotkami a fotka samotná jsou velké, zbytek malý
        big = self.path.split("?")[0].endswith(("/import", "/photo"))
        limit = (100 if big else 2) * 1024 * 1024
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            # Dočíst a zahodit -- jinak se spojení zavře dřív, než prohlížeč
            # dostane hlášku, a uvidí jen obecnou chybu sítě
            left = min(length, 300 * 1024 * 1024)
            while left > 0:
                chunk = self.rfile.read(min(left, 1024 * 1024))
                if not chunk:
                    break
                left -= len(chunk)
            raise InputError(f"Data jsou příliš velká (limit {limit // 1024 // 1024} MB).")
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8")) or {}
        except (ValueError, UnicodeDecodeError):
            return {}

    def _json(self, obj) -> None:
        self._raw(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                  "application/json; charset=utf-8")

    def _raw(self, body: bytes, ctype: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _err(self, code: int, msg: str) -> None:
        self._raw(json.dumps({"error": msg}, ensure_ascii=False).encode("utf-8"),
                  "application/json; charset=utf-8", code)


def _slug(text: str) -> str:
    """'Pizza žemle' -> 'pizza-zemle'. Název souboru v hlavičce musí být
    bez diakritiky (HTTP hlavička je latin-1)."""
    plain = unicodedata.normalize("NFD", text)
    plain = "".join(c for c in plain if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-") or "recept"


def _b64(value) -> bytes:
    try:
        return base64.b64decode(str(value or ""), validate=True)
    except (ValueError, TypeError):
        raise InputError("Poškozená data fotky.") from None


def _import(body: dict) -> dict:
    """Recepty ze souboru z exportu. Recept se stejným názvem se přeskočí."""
    if body.get("format") != "mat-cooking" or not isinstance(body.get("recipes"), list):
        raise InputError("Tohle není soubor s recepty z MAT-Cooking.")
    added, skipped = DATA.import_recipes(body["recipes"])
    for rid, rec in added:
        if rec.get("photo") and rec.get("photo_small"):
            DATA.set_photo(rid, _b64(rec["photo"]), _b64(rec["photo_small"]))
    return {"added": [rec["name"] for _, rec in added], "skipped": skipped}


def _plan() -> dict:
    recipes, items, have, sent = DATA.plan_rows()
    return {"recipes": recipes, "shopping": shopping_list(recipes, items, have, sent),
            "ha": ha.available()}


def _send(entity_id: str) -> dict:
    """Chybějící suroviny z plánu do nákupního seznamu v HA.

    Posílá se jen to, co z plánu ještě neodešlo. Když v seznamu už je
    nevyřízená položka té suroviny (ve tvaru, jaký appka píše), navýší se
    -- kuřecí 200 g + 200 g = jedna položka 400 g, ne dvě.
    """
    if not ha.available():
        raise InputError("Appka neběží v Home Assistantu -- seznam zkopíruj ručně.")
    rows = [r for r in _plan()["shopping"] if not r["have"] and r["to_send"]]
    if not rows:
        raise InputError("Není co poslat -- všechno chybějící už v seznamu je.")
    existing = ha.open_items(entity_id)
    known = {r["ingredient"].lower() for r in rows}
    open_texts = {str(it.get("summary", "")).strip().lower() for it in existing or []}
    parsed = []
    for it in existing or []:
        p = parse_item(str(it.get("summary", "")), known)
        if p:
            parsed.append((p, it))
    added, updated, sent = [], [], {}
    try:
        for r in rows:
            pending = Decimal(r["pending"]) if r["pending"] else None
            note = "Pro: " + ", ".join(r["recipes"])
            match = next(((p, it) for p, it in parsed
                          if p[0] == r["ingredient"].lower() and p[1] == r["group"]), None)
            if pending is None and r["ingredient"].lower() in open_texts:
                pass                # sůl apod. bez množství už v seznamu je
            elif pending is not None and match:
                (_, group, base), it = match
                text = item_text(r["ingredient"], group, base + pending)
                ha.update_item(entity_id, it, text, note)
                updated.append(text)
            else:
                text = item_text(r["ingredient"], r["group"], pending)
                ha.add_item(entity_id, text, note)
                added.append(text)
            sent[r["key"]] = r["base"]
    finally:
        # I když HA uprostřed selže, zapamatuje se, co už odešlo
        DATA.mark_sent(sent)
    return {"added": added, "updated": updated}


def _free_port(preferred: int) -> int:
    for port in (preferred, 0):
        s = socket.socket()
        try:
            s.bind((HOST, port))
            return s.getsockname()[1]
        except OSError:
            continue
        finally:
            s.close()
    return preferred


def main(argv: list[str] | None = None) -> int:
    global DATA, PORT
    ap = argparse.ArgumentParser(description=f"{APP_NAME} -- recepty a plán vaření")
    ap.add_argument("--data", default=os.environ.get("MAT_DATA") or
                    os.path.join(ROOT, "..", "devdata"),
                    help="složka s databází (lokálně devdata/ vedle add-onu)")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--bez-prohlizece", action="store_true")
    args = ap.parse_args(argv)

    DATA = DB(os.path.abspath(args.data))
    if args.port:
        PORT = args.port
    elif not ALLOW_ONLY:
        PORT = _free_port(PORT)     # lokálně: když je port obsazený, vezme jiný
    url = f"http://127.0.0.1:{PORT}/"

    print(f"\n  {APP_NAME} {APP_VERSION}\n")
    print(f"  Databáze:  {DATA.path}")
    print(f"  Server:    {HOST}:{PORT}" + ("" if ALLOW_ONLY else f"  ->  {url}"))
    print(flush=True)

    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.daemon_threads = True
    if not args.bez_prohlizece:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  Konec.")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
