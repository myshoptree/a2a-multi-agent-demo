"""
demo/client.py
==============
Cliente A2A reusable con handoff peer-to-peer.

Características:
  - Mantiene el estado de a qué agente le hablas (current_url).
  - Detecta handoffs en Message.metadata y cambia de endpoint automáticamente.
  - Detecta ciclos de routing y aborta.
  - Expone un contexto async para uso con `async with`.

Uso:
    async with A2AClient(entry_url="http://localhost:9000/general/") as client:
        reply = await client.send("hola")
        print(reply.text, reply.agent_name)
"""
from __future__ import annotations

import uuid
import logging
from dataclasses import dataclass, field
from typing import Optional

import httpx
from a2a.utils.constants import VERSION_HEADER, PROTOCOL_VERSION_1_0

HANDOFF_METADATA_KEY = "a2a_handoff"
MAX_HANDOFFS_PER_TURN = 5

log = logging.getLogger("demo.client")


@dataclass
class Handoff:
    agent_name: str
    url: str
    reason: str


@dataclass
class Reply:
    text: str
    agent_name: str
    agent_url: str
    handoff_path: list[Handoff] = field(default_factory=list)


class RoutingLoopError(RuntimeError):
    def __init__(self, visited: list[str]):
        super().__init__(f"Routing loop detected: {' → '.join(visited)}")
        self.visited = visited


class A2AClient:

    def __init__(
        self,
        entry_url: str,
        entry_name: str = "General",
        session_id: Optional[str] = None,
        timeout: float = 60.0,
    ):
        if not entry_url.endswith("/"):
            entry_url += "/"
        self._entry_url   = entry_url
        self._entry_name  = entry_name
        self._current_url = entry_url
        self._current_name = entry_name
        self._session_id  = session_id or uuid.uuid4().hex[:12]
        self._http: Optional[httpx.AsyncClient] = None
        self._timeout = timeout

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def __aenter__(self) -> "A2AClient":
        self._http = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # ── Introspection ────────────────────────────────────────────────────────

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def current_url(self) -> str:
        return self._current_url

    @property
    def current_name(self) -> str:
        return self._current_name

    def reset_to_entry(self) -> None:
        """Vuelve a hablar con el agente inicial."""
        self._current_url = self._entry_url
        self._current_name = self._entry_name

    # ── Envío ────────────────────────────────────────────────────────────────

    async def send(self, text: str) -> Reply:
        """
        Envía un mensaje al agente actual.
        Si el agente responde con handoff, sigue la cadena hasta obtener
        una respuesta de texto o detectar un ciclo.
        """
        if self._http is None:
            raise RuntimeError("A2AClient must be used inside `async with`.")

        visited: list[str] = [self._current_url]
        path: list[Handoff] = []

        for _ in range(MAX_HANDOFFS_PER_TURN):
            raw_text, handoff = await self._post(self._current_url, text)

            if handoff is None:
                return Reply(
                    text=raw_text,
                    agent_name=self._current_name,
                    agent_url=self._current_url,
                    handoff_path=path,
                )

            # El agente nos pasa a otro — detectar ciclo
            target_url = handoff.url if handoff.url.endswith("/") else handoff.url + "/"
            if target_url in visited:
                raise RoutingLoopError(visited + [target_url])

            path.append(handoff)
            visited.append(target_url)
            self._current_url = target_url
            self._current_name = handoff.agent_name
            log.info(f"[client] handoff → {handoff.agent_name} ({target_url})")

        raise RoutingLoopError(visited)

    # ── Internals ────────────────────────────────────────────────────────────

    async def _post(self, url: str, text: str) -> tuple[str, Optional[Handoff]]:
        """Envía un SendMessage y devuelve (texto, handoff|None)."""
        payload = {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": "SendMessage",
            "params": {
                "message": {
                    "role":       "ROLE_USER",
                    "message_id": uuid.uuid4().hex,
                    "context_id": self._session_id,
                    "parts": [{"text": text}],
                }
            },
        }
        headers = {VERSION_HEADER: PROTOCOL_VERSION_1_0}

        assert self._http is not None
        r = await self._http.post(url, json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()

        if "error" in data:
            raise RuntimeError(
                f"Agent error: {data['error'].get('message', str(data['error']))}"
            )

        msg = data.get("result", {}).get("message", {})
        parts = msg.get("parts", [])
        text_out = "\n".join(p["text"] for p in parts if "text" in p)

        handoff = self._extract_handoff(msg.get("metadata"))
        return text_out, handoff

    @staticmethod
    def _extract_handoff(metadata: Optional[dict]) -> Optional[Handoff]:
        if not metadata:
            return None
        h = metadata.get(HANDOFF_METADATA_KEY)
        if not isinstance(h, dict):
            return None
        try:
            return Handoff(
                agent_name=h["agent_name"],
                url=h["url"],
                reason=h.get("reason", ""),
            )
        except KeyError:
            return None
