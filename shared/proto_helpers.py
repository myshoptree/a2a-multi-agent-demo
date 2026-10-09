"""
Helpers compartidos para construir objetos protobuf de a2a-sdk v1.2.
"""
import uuid
from typing import Optional
from a2a.types import Message, Role
from a2a.server.agent_execution import RequestContext

HANDOFF_METADATA_KEY = "a2a_handoff"


def make_text_message(text: str) -> Message:
    """Construye un Message de respuesta del agente con texto plano."""
    msg = Message()
    msg.message_id = uuid.uuid4().hex
    msg.role = Role.Value("ROLE_AGENT")
    part = msg.parts.add()
    part.text = text
    return msg


def make_handoff_message(
    agent_name: str,
    url: str,
    reason: str,
    human_text: Optional[str] = None,
) -> Message:
    """
    Construye un Message con handoff estructurado en metadata.
    El body de texto queda legible para clientes que no entienden handoffs.
    """
    msg = make_text_message(
        human_text or f"Te paso con {agent_name}."
    )
    handoff = msg.metadata.fields[HANDOFF_METADATA_KEY].struct_value
    handoff.fields["agent_name"].string_value = agent_name
    handoff.fields["url"].string_value = url
    handoff.fields["reason"].string_value = reason
    return msg


def extract_text(context: RequestContext) -> str:
    """Extrae todo el texto del mensaje entrante."""
    if not context.message:
        return ""
    return " ".join(
        p.text for p in context.message.parts
        if p.HasField("text")
    ).strip()
