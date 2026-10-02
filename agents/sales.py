"""
Agent: Ventas
Responde consultas de ventas. Si el mensaje no es de su competencia, enruta.
Ruta: /sales
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

log = logging.getLogger("agent.sales")

SYSTEM = """Eres el Agente de Ventas.
SIEMPRE comienza tu respuesta identificándote: "🛒 Agente de Ventas:"

Tu competencia:
- Consultas sobre productos y catálogo
- Precios y descuentos
- Cotizaciones personalizadas
- Disponibilidad de stock
- Proceso de compra y pagos
- Preguntas generales sobre qué servicios o productos ofrecemos

Tienes acceso al historial completo de la conversación.

REGLA: Si el mensaje tiene alguna relación con productos, servicios, compras o información comercial → responde tú mismo.
SOLO enruta si el mensaje es CLARAMENTE sobre soporte técnico, problemas con un producto ya comprado, quejas o tickets.
En caso de duda → responde tú mismo."""


class SalesExecutor(AgentExecutor):

    def __init__(self, server_base: str):
        self._server_base = server_base

    @override
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        text       = extract_text(context)
        session_id = context.message.context_id or context.context_id or "default"

        log.info(f"[Ventas] execute() session={session_id} → \"{text[:80]}\"")
        session_store.add_message(session_id, "user", text, "Ventas")

        if not text:
            await event_queue.enqueue_event(make_text_message("Error: no se recibió texto."))
            return

        try:
            result = await run_react_loop(
                agent_label="Ventas",
                system_prompt=SYSTEM,
                user_text=text,
                server_base=self._server_base,
                session_id=session_id,
            )

            if result.type == "answer":
                await event_queue.enqueue_event(make_text_message(result.content))
            else:
                # Enrutamiento — serializar para el cliente
                await event_queue.enqueue_event(
                    make_text_message(json.dumps({
                        "type":       result.type,
                        "agent_name": result.agent_name,
                        "url":        result.url,
                        "reason":     result.reason,
                    }, ensure_ascii=False))
                )
        except Exception as e:
            log.error(f"[Ventas] ERROR: {e}")
            await event_queue.enqueue_event(make_text_message(f"[Ventas] Error: {e}"))

    @override
    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise Exception("cancel not supported")


def build_app(public_url: str, server_base: str) -> Starlette:
    card = AgentCard()
    card.name = "Agente de Ventas"
    card.description = (
        "Especialista en ventas. Maneja consultas sobre productos, precios, "
        "cotizaciones, disponibilidad de stock y proceso de compra. "
        "Ideal para clientes interesados en adquirir productos o servicios."
    )
    card.version = "1.0.0"
    card.default_input_modes.append("text/plain")
    card.default_output_modes.append("text/plain")

    iface = card.supported_interfaces.add()
    iface.url = f"{public_url}/"

    s1 = card.skills.add()
    s1.id = "product_inquiry"
    s1.name = "Consulta de productos"
    s1.description = "Responde preguntas sobre productos, catálogo y características."
    s1.tags.extend(["ventas", "productos", "catalogo"])

    s2 = card.skills.add()
    s2.id = "quotation"
    s2.name = "Cotización"
    s2.description = "Genera cotizaciones personalizadas y maneja descuentos."
    s2.tags.extend(["ventas", "cotizacion", "precio", "descuento"])

    s3 = card.skills.add()
    s3.id = "purchase_process"
    s3.name = "Proceso de compra"
    s3.description = "Guía al cliente en el proceso de compra, pagos y disponibilidad."
    s3.tags.extend(["ventas", "compra", "pago", "stock"])

    handler = DefaultRequestHandler(
        agent_executor=SalesExecutor(server_base=server_base),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )

    routes = create_agent_card_routes(card) + create_jsonrpc_routes(handler, rpc_url="/")
    return Starlette(routes=routes)
