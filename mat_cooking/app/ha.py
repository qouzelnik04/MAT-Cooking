"""
Napojení na Home Assistant -- seznamy úkolů (nákupní seznamy).

Add-on má v config.yaml `homeassistant_api: true`, takže od HA dostane
token v proměnné SUPERVISOR_TOKEN a API HA je na http://supervisor/core/api.
Nic se nenastavuje ručně. Lokálně (START.bat) token není a appka nákupní
seznam místo odeslání nabídne ke zkopírování.

MAT_HA_URL a MAT_HA_TOKEN jsou jen pro testy (falešné HA).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

BASE = os.environ.get("MAT_HA_URL") or "http://supervisor/core/api"
TOKEN = os.environ.get("MAT_HA_TOKEN") or os.environ.get("SUPERVISOR_TOKEN") or ""


class HAError(Exception):
    pass


def available() -> bool:
    return bool(TOKEN)


def _call(method: str, path: str, body=None, raw: bytes | None = None,
          content_type: str = "application/json"):
    data = json.dumps(body).encode("utf-8") if body is not None else raw
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": content_type})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raise HAError(f"Home Assistant odpověděl chybou {exc.code} ({path}).") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise HAError(f"Home Assistant není dostupný: {exc}") from exc


def todo_lists() -> list[dict]:
    """Všechny seznamy úkolů v HA: [{entity_id, name}]."""
    states = _call("GET", "/states") or []
    out = [{"entity_id": s["entity_id"],
            "name": (s.get("attributes") or {}).get("friendly_name") or s["entity_id"]}
           for s in states if s.get("entity_id", "").startswith("todo.")]
    return sorted(out, key=lambda x: x["name"].lower())


def open_items(entity_id: str) -> list[dict] | None:
    """Nevyřízené položky seznamu [{summary, uid, description}]; None = nejde zjistit."""
    if not entity_id.startswith("todo."):
        raise HAError("Neplatný seznam.")
    try:
        res = _call("POST", "/services/todo/get_items?return_response",
                    {"entity_id": entity_id, "status": ["needs_action"]})
    except HAError:
        return None
    return ((res or {}).get("service_response") or {}).get(entity_id, {}).get("items", [])


def add_item(entity_id: str, text: str, description: str) -> None:
    body = {"entity_id": entity_id, "item": text}
    try:
        _call("POST", "/services/todo/add_item",
              {**body, "description": description} if description else body)
    except HAError:
        if not description:
            raise
        # Ne každý seznam umí popis položky -- pak aspoň bez něj
        _call("POST", "/services/todo/add_item", body)


def update_item(entity_id: str, item: dict, text: str, description: str) -> None:
    """Přepíše existující položku (navýšení množství)."""
    body = {"entity_id": entity_id, "item": item.get("uid") or item.get("summary"),
            "rename": text}
    try:
        _call("POST", "/services/todo/update_item",
              {**body, "description": description} if description else body)
    except HAError:
        if not description:
            raise
        _call("POST", "/services/todo/update_item", body)


# ----------------------------------------------------------------------
# Recept na displeji (viz display.py)
# ----------------------------------------------------------------------
def media_players() -> list[dict]:
    """Přehrávače v HA, na které jde promítat: [{entity_id, name}].

    Bere i skryté entity -- skrytí je jen o zobrazení v UI HA.
    """
    states = _call("GET", "/states") or []
    out = [{"entity_id": s["entity_id"],
            "name": (s.get("attributes") or {}).get("friendly_name") or s["entity_id"]}
           for s in states if s.get("entity_id", "").startswith("media_player.")]
    return sorted(out, key=lambda x: x["name"].lower())


def get_state(entity_id: str) -> dict | None:
    """Stav entity; None, když entita (zatím) neexistuje."""
    try:
        return _call("GET", f"/states/{entity_id}")
    except HAError as exc:
        if isinstance(exc.__cause__, urllib.error.HTTPError) and exc.__cause__.code == 404:
            return None
        raise


def set_state(entity_id: str, state: str, attributes: dict) -> None:
    _call("POST", f"/states/{entity_id}", {"state": state, "attributes": attributes})


def call_service(domain: str, service: str, data: dict) -> None:
    _call("POST", f"/services/{domain}/{service}", data)


def upload_image(data: bytes, filename: str) -> str:
    """Nahraje JPEG do úložiště obrázků HA (to, kam se dávají fotky osob).

    Vrací id; obrázek je pak na /api/image/serve/<id>/512x512 -- bez
    přihlášení (jako fotky osob), takže ho načte i displej.
    """
    boundary = "matcooking" + os.urandom(12).hex()
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{filename}"\r\nContent-Type: image/jpeg\r\n\r\n').encode() \
        + data + f"\r\n--{boundary}--\r\n".encode()
    res = _call("POST", "/image/upload", raw=body,
                content_type=f"multipart/form-data; boundary={boundary}")
    if not isinstance(res, dict) or not res.get("id"):
        raise HAError("HA obrázek nepřijal.")
    return str(res["id"])


def external_url() -> str:
    """Adresa HA z internetu (Nastavení -> Síť); prázdná, když není nastavená."""
    try:
        cfg = _call("GET", "/config") or {}
    except HAError:
        return ""
    return str(cfg.get("external_url") or cfg.get("internal_url") or "").rstrip("/")


def ws_url() -> str:
    """Websocket API HA: přes Supervisor /core/websocket, jinak <url>/websocket."""
    if BASE.endswith("/core/api"):
        return BASE[:-len("/api")] + "/websocket"
    return BASE + "/websocket"
