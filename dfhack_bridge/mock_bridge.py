from __future__ import annotations

import json
import socket
from typing import Any

from dfhack_bridge.base import BridgeError, FortressBridge
from dfhack_bridge.mock_server import FortressSim, dispatch
from dfhack_bridge.protocol import FortressState


class _CommandBridge(FortressBridge):
    def _call(self, req: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def _ok(self, req: dict[str, Any]) -> dict[str, Any]:
        resp = self._call(req)
        if not resp.get("ok"):
            raise BridgeError(resp.get("error", "unknown error"))
        return resp

    def get_state(self) -> FortressState:
        return FortressState.model_validate(self._ok({"cmd": "get_state"})["state"])

    def create_work_order(self, job: str, quantity: int, *, source: str = "autopilot") -> int:
        return int(self._ok({"cmd": "create_work_order", "job": job, "quantity": quantity, "source": source})["order_id"])

    def cancel_work_order(self, order_id: int) -> None:
        self._ok({"cmd": "cancel_work_order", "order_id": order_id})

    def expand_stockpile(self, stockpile_id: int, cells: int) -> None:
        self._ok({"cmd": "expand_stockpile", "stockpile_id": stockpile_id, "cells": cells})

    def link_stockpile(self, stockpile_id: int, workshop: str) -> None:
        self._ok({"cmd": "link_stockpile", "stockpile_id": stockpile_id, "workshop": workshop})

    def enqueue_blueprint(self, queue_id: str, blueprint: str, zone_id: str) -> None:
        self._ok({"cmd": "enqueue_blueprint", "queue_id": queue_id, "blueprint": blueprint, "zone_id": zone_id})

    def set_paused(self, paused: bool) -> None:
        self._ok({"cmd": "set_paused", "paused": paused})

    def inject_event(self, kind: str) -> None:
        self._ok({"cmd": "inject_event", "kind": kind})


class MockBridge(_CommandBridge):
    """Talks to `mock_server.py` over TCP (one short connection per request)."""

    def __init__(self, host: str = "127.0.0.1", port: int = 5577, timeout: float = 3.0):
        self.host, self.port, self.timeout = host, port, timeout
        self.name = f"mock {host}:{port}"

    def _call(self, req: dict[str, Any]) -> dict[str, Any]:
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
                sock.sendall((json.dumps(req) + "\n").encode())
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
        except OSError as exc:
            raise BridgeError(f"mock server unreachable at {self.host}:{self.port}: {exc}") from exc
        if not buf:
            raise BridgeError("empty response from mock server")
        return json.loads(buf)


class LocalMockBridge(_CommandBridge):
    """In-process simulation (no sockets). Used by tests and `main.py --embedded`."""

    name = "mock (embedded)"

    def __init__(self, sim: FortressSim | None = None):
        self.sim = sim or FortressSim()

    def _call(self, req: dict[str, Any]) -> dict[str, Any]:
        return dispatch(self.sim, req)
