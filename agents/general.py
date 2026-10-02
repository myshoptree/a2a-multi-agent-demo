"""
Agent: General — enruta al especialista correcto.
Ruta: /general
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

log = logging.getLogger("agent.general")

SYSTEM = """Eres el Agente General — recepcionista de atención al cliente.

Reglas:
1. Si el mensaje es un saludo, presentación o consulta genérica → respóndelo tú mismo con amabilidad y pregunta en qué puedes ayudar. NO enrutes.
2. Si el mensaje tiene una intención clara (compra, soporte técnico, queja, etc.) → usa discover_agents y route_to para enviarlo al especialista correcto.
3. Solo enrutas cuando estás seguro de la intención del cliente.

SIEMPRE comienza tu respuesta identificándote: "🧑‍💼 General:"
"""


class GeneralExecutor(AgentExecutor):

    def __init__(self, server_base: str):
        self._server_base = server_base

    @override
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        text       = extract_text(context)
        session_id = context.message.context_id or context.context_id or "default"

        log.info(f"[General] execute() session={session_id} → \"{text[:80]}\"")
        session_store.add_message(session_id, "user", text, "General")

        if not text:
            await event_queue.enqueue_event(make_text_message("Error: no se recibió texto."))
            return

        try:
            result = await run_react_loop(
                agent_label="General",
                system_prompt=SYSTEM,
                user_text=text,
                server_base=self._server_base,
                session_id=session_id,
            )

            if result.type == "answer":
                # Saludo o consulta genérica — responde directamente
                await event_queue.enqueue_event(make_text_message(result.content))
            else:
                # Tiene intención clara — retorna routing al cliente
                await event_queue.enqueue_event(
                    make_text_message(json.dumps({
                        "type":       result.type,
                        "agent_name": result.agent_name,
                        "url":        result.url,
                        "reason":     result.reason,
                    }, ensure_ascii=False))
                )
        except Exception as e:
            log.error(f"[General] ERROR: {e}")
            await event_queue.enqueue_event(make_text_message(json.dumps({
                "type": "error", "reason": str(e)
            })))

    @override
    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise Exception("cancel not supported")


def build_app(public_url: str, server_base: str) -> Starlette:
    card = AgentCard()
    card.name = "Agente General"
    card.description = "Enrutador. Analiza el mensaje y devuelve la URL del agente especializado correcto."
    card.version = "1.0.0"
    card.default_input_modes.append("text/plain")
    card.default_output_modes.append("text/plain")

    iface = card.supported_interfaces.add()
    iface.url = f"{public_url}/"

    skill = card.skills.add()
    skill.id = "routing"
    skill.name = "Enrutamiento"
    skill.description = "Descubre agentes y retorna la URL del especialista correcto."
    skill.tags.extend(["general", "routing"])

    handler = DefaultRequestHandler(
        agent_executor=GeneralExecutor(server_base=server_base),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )

    routes = create_agent_card_routes(card) + create_jsonrpc_routes(handler, rpc_url="/")
    return Starlette(routes=routes)
