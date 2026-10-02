"""
shared/session_store.py
=======================
Store en memoria compartido por todos los agentes.
Guarda el historial de conversación por session_id.

Estructura:
  store[session_id] = [
    {"role": "user",  "content": "...", "agent": "General"},
    {"role": "agent", "content": "...", "agent": "Ventas"},
    ...
  ]
"""
import logging
from datetime import datetime
from typing import Optional

log = logging.getLogger("shared.session_store")


class SessionStore:

    def __init__(self):
        self._sessions: dict[str, list[dict]] = {}

    # ── Escritura ─────────────────────────────────────────────────────────────

    def add_message(self, session_id: str, role: str, content: str, agent: str):
        """Agrega un mensaje al historial de la sesión."""
        if session_id not in self._sessions:
            self._sessions[session_id] = []
            log.info(f"[SessionStore] nueva sesión: {session_id}")

        entry = {
            "role":    role,
            "content": content,
            "agent":   agent,
            "ts":      datetime.utcnow().isoformat(),
        }
        self._sessions[session_id].append(entry)
        log.info(f"[SessionStore] [{session_id}] {agent} ({role}): \"{content[:60]}\"")

    # ── Lectura ───────────────────────────────────────────────────────────────

    def get_history(self, session_id: str) -> list[dict]:
        """Retorna el historial completo de la sesión."""
        return self._sessions.get(session_id, [])

    def get_context_text(self, session_id: str) -> str:
        """
        Retorna el historial como texto legible para incluir
        en el system prompt del LLM.
        """
        history = self.get_history(session_id)
        if not history:
            return "No hay historial previo."

        lines = []
        for entry in history:
            lines.append(f"[{entry['agent']}] {entry['role']}: {entry['content']}")
        return "\n".join(lines)

    def get_llm_messages(self, session_id: str) -> list[dict]:
        """
        Retorna el historial en formato de mensajes para el LLM
        (lista de {role, content}).
        """
        history = self.get_history(session_id)
        messages = []
        for entry in history:
            # Mapear roles al formato OpenAI
            role = "user" if entry["role"] == "user" else "assistant"
            messages.append({
                "role":    role,
                "content": f"[{entry['agent']}]: {entry['content']}",
            })
        return messages

    def exists(self, session_id: str) -> bool:
        return session_id in self._sessions

    def clear(self, session_id: str):
        if session_id in self._sessions:
            del self._sessions[session_id]
            log.info(f"[SessionStore] sesión eliminada: {session_id}")


# Instancia global — todos los agentes importan esta misma instancia
session_store = SessionStore()
