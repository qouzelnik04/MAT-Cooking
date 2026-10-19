"""
Minimální websocket klient (RFC 6455) jen ze standardní knihovny.

Appka ho potřebuje na dvě věci, které REST API HA neumí: vytvořit pomocníky
(input_select, input_number) a dozvědět se hned, když je někdo na displeji
přepne. Posílá jen malé textové zprávy (JSON), přijímá i rozdělené rámce.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import select
import socket
import ssl
import struct
import threading
import urllib.parse

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WebSocket:
    def __init__(self, url: str, timeout: float = 30):
        u = urllib.parse.urlparse(url)
        secure = u.scheme in ("https", "wss")
        sock = socket.create_connection((u.hostname, u.port or (443 if secure else 80)),
                                        timeout=timeout)
        if secure:
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=u.hostname)
        self.sock = sock
        self.wlock = threading.Lock()
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall((f"GET {u.path or '/'} HTTP/1.1\r\nHost: {u.netloc}\r\n"
                      "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while not head.endswith(b"\r\n\r\n"):
            # Po bajtu, aby se nepřečetl začátek první zprávy za hlavičkou
            b = sock.recv(1)
            if not b or len(head) > 16384:
                raise ConnectionError("Websocket: server neodpověděl.")
            head += b
        lines = head.decode("latin-1").split("\r\n")
        if " 101 " not in lines[0] + " ":
            raise ConnectionError(f"Websocket: {lines[0]}")
        accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        if not any(ln.lower().startswith("sec-websocket-accept:") and ln.split(":", 1)[1].strip()
                   == accept for ln in lines):
            raise ConnectionError("Websocket: neplatná odpověď serveru.")

    # ------------------------------------------------------------------
    def send(self, obj) -> None:
        self._frame(0x1, json.dumps(obj).encode("utf-8"))

    def _frame(self, opcode: int, data: bytes) -> None:
        n = len(data)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        with self.wlock:
            self.sock.sendall(head + mask + body)

    def _read(self, n: int) -> bytes:
        out = b""
        while len(out) < n:
            chunk = self.sock.recv(n - len(out))
            if not chunk:
                raise ConnectionError("Websocket: spojení ukončeno.")
            out += chunk
        return out

    def readable(self, timeout: float) -> bool:
        """Čeká na další zprávu nejvýš timeout sekund."""
        if isinstance(self.sock, ssl.SSLSocket) and self.sock.pending():
            return True
        return bool(select.select([self.sock], [], [], timeout)[0])

    def recv(self):
        parts = []
        while True:
            b1, b2 = self._read(2)
            opcode, n = b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b2 & 0x80 else None
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if opcode == 0x8:
                raise ConnectionError("Websocket: server spojení zavřel.")
            if opcode == 0x9:
                self._frame(0xA, data)
                continue
            if opcode == 0xA:
                continue
            parts.append(data)
            if b1 & 0x80:
                return json.loads(b"".join(parts))

    def close(self) -> None:
        try:
            self._frame(0x8, b"")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
