"""
Helpers compartidos para construir objetos protobuf de a2a-sdk v1.2.
"""
import uuid
from a2a.types import Message, Part, Role
from a2a.server.agent_execution import RequestContext


def make_text_message(text: str) -> Message:
    """Construye un Message de respuesta del agente con texto plano."""
    msg = Message()
    msg.message_id = uuid.uuid4().hex
    msg.role = Role.Value("ROLE_AGENT")
    part = msg.parts.add()
    part.text = text
    return msg


def extract_text(context: RequestContext) -> str:
    """Extrae todo el texto del mensaje entrante."""
    if not context.message:
        return ""
    return " ".join(
        p.text for p in context.message.parts
        if p.HasField("text")
    ).strip()
