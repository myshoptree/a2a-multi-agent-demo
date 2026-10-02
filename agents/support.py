"""
Agent: Soporte
Responde consultas de soporte. Si el mensaje no es de su competencia, enruta.
Ruta: /support
"""
import sys, os, logging, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing_extensions import override
from starlette.applications import Starlette

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.types import AgentCard, AgentInterface

from shared.proto_helpers import make_text_message, extract_text
from shared.a2a_tools import run_react_loop
from shared.session_store import session_store

log = logging.getLogger("agent.support")

SYSTEM = """Eres el Agente de Soporte.
SIEMPRE comienza tu respuesta identificándote: "🎧 Agente de Soporte:"

Tu competencia:
- Problemas técnicos con productos o servicios
- Quejas y reclamos
- Seguimiento de tickets y casos abiertos
- Devoluciones y garantías
- Escalamiento de incidencias

Tienes acceso al historial completo de la conversación.
Siempre ofrece un número de ticket para seguimiento.

REGLA: Si el mensaje es sobre un problema, error, queja, ticket o devolución → responde tú mismo.
SOLO enruta si el mensaje es CLARAMENTE sobre comprar productos, precios o cotizaciones.
En caso de duda → responde tú mismo."""


class SupportExecutor(AgentExecutor):

    def __init__(self, server_base: str):
        self._server_base = server_base

    @override
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        text       = extract_text(context)
        session_id = context.message.context_id or context.context_id or "default"

        log.info(f"[Soporte] execute() session={session_id} → \"{text[:80]}\"")
        session_store.add_message(session_id, "user", text, "Soporte")

        if not text:
            await event_queue.enqueue_event(make_text_message("Error: no se recibió texto."))
            return

        try:
            result = await run_react_loop(
                agent_label="Soporte",
                system_prompt=SYSTEM,
                user_text=text,
                server_base=self._server_base,
                session_id=session_id,
            )

            if result.type == "answer":
                await event_queue.enqueue_event(make_text_message(result.content))
            else:
                await event_queue.enqueue_event(
                    make_text_message(json.dumps({
                        "type":       result.type,
                        "agent_name": result.agent_name,
                        "url":        result.url,
                        "reason":     result.reason,
                    }, ensure_ascii=False))
                )
        except Exception as e:
            log.error(f"[Soporte] ERROR: {e}")
            await event_queue.enqueue_event(make_text_message(f"[Soporte] Error: {e}"))

    @override
    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise Exception("cancel not supported")


def build_app(public_url: str, server_base: str) -> Starlette:
    card = AgentCard()
    card.name = "Agente de Soporte"
    card.description = (
        "Especialista en soporte técnico y atención post-venta. "
        "Maneja problemas técnicos, quejas, reclamos, devoluciones, "
        "garantías y seguimiento de tickets."
    )
    card.version = "1.0.0"
    card.default_input_modes.append("text/plain")
    card.default_output_modes.append("text/plain")

    iface = card.supported_interfaces.add()
    iface.url = f"{public_url}/"

    s1 = card.skills.add()
    s1.id = "technical_support"
    s1.name = "Soporte técnico"
    s1.description = "Diagnostica y resuelve problemas técnicos con productos o servicios."
    s1.tags.extend(["soporte", "tecnico", "problema", "error"])

    s2 = card.skills.add()
    s2.id = "complaint_handling"
    s2.name = "Gestión de quejas y reclamos"
    s2.description = "Atiende quejas, reclamos, devoluciones y garantías."
    s2.tags.extend(["soporte", "queja", "reclamo", "devolucion", "garantia"])

    s3 = card.skills.add()
    s3.id = "ticket_tracking"
    s3.name = "Seguimiento de tickets"
    s3.description = "Abre, consulta y escala tickets de soporte."
    s3.tags.extend(["soporte", "ticket", "caso", "seguimiento"])

    handler = DefaultRequestHandler(
        agent_executor=SupportExecutor(server_base=server_base),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )

    routes = create_agent_card_routes(card) + create_jsonrpc_routes(handler, rpc_url="/")
    return Starlette(routes=routes)
