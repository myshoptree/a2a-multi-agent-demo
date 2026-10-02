"""
Agent 2: Resumidor + Coordinador  (a2a-sdk v1.2, protobuf)
"""
import sys, os, uuid, logging
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

from shared.llm import get_llm_client, get_model
from shared.proto_helpers import make_text_message, extract_text

log = logging.getLogger("agent2.summarizer")


class SummarizerLogic:
    SYSTEM = (
        "Eres un experto en síntesis. Responde con exactamente 3 bullets "
        "concisos en el mismo idioma del texto. "
        "Formato: '• punto\\n• punto\\n• punto'. Nada más."
    )

    async def summarize(self, text: str) -> str:
        log.info(f"  → llamando OpenRouter modelo={get_model()}")
        client = get_llm_client()
        resp = await client.chat.completions.create(
            model=get_model(),
            messages=[
                {"role": "system", "content": self.SYSTEM},
                {"role": "user",   "content": text},
            ],
            max_tokens=256,
            temperature=0.3,
        )
        result = resp.choices[0].message.content or ""
        log.info(f"  ← resumen recibido ({len(result)} chars)")
        return result


async def call_agent1_via_a2a(agent1_url: str, text: str) -> str:
    """Llama a Agent 1 usando JSON-RPC A2A v1.2."""
    import httpx
    from a2a.utils.constants import VERSION_HEADER, PROTOCOL_VERSION_1_0

    log.info(f"[Agent2→Agent1] A2A call → {agent1_url}/")
    payload = {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex,
        "method": "SendMessage",
        "params": {
            "message": {
                "role": "ROLE_USER",
                "message_id": uuid.uuid4().hex,
                "parts": [{"text": text}],
            }
        },
    }
    headers = {VERSION_HEADER: PROTOCOL_VERSION_1_0}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{agent1_url}/", json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    log.info(f"[Agent2→Agent1] respuesta HTTP {resp.status_code}")

    if "error" in data:
        log.error(f"[Agent2→Agent1] error: {data['error']}")
        return "[sin respuesta de Agent 1]"

    result = data.get("result", {})
    msg = result.get("message", {})
    parts = msg.get("parts", [])
    translation = " ".join(p["text"] for p in parts if "text" in p).strip()
    translation = translation or "[sin respuesta de Agent 1]"
    log.info(f"[Agent2→Agent1] traducción extraída: \"{translation[:80]}\"")
    return translation


COORD_PREFIXES = [
    "translate and summarize:",
    "traduce y resume:",
    "traducir y resumir:",
    "translate_and_summarize:",
]


class SummarizerExecutor(AgentExecutor):

    def __init__(self, agent1_url: str):
        self._logic = SummarizerLogic()
        self._agent1_url = agent1_url

    @override
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        text = extract_text(context)
        log.info(f"[Agent2] execute() → texto: \"{text[:80]}\"")

        if not text:
            await event_queue.enqueue_event(make_text_message("Error: no se recibió texto."))
            return

        lower = text.lower()
        is_coord = any(lower.startswith(kw) for kw in COORD_PREFIXES)

        try:
            if is_coord:
                log.info("[Agent2] modo coordinador A2A activado")
                for kw in COORD_PREFIXES:
                    if lower.startswith(kw):
                        text = text[len(kw):].strip()
                        break

                log.info("[Agent2] [Paso 1/2] delegando traducción a Agent 1 vía A2A...")
                translated = await call_agent1_via_a2a(self._agent1_url, text)
                log.info(f"[Agent2] [Paso 1/2] traducción recibida: \"{translated[:80]}\"")

                log.info("[Agent2] [Paso 2/2] resumiendo traducción...")
                summary = await self._logic.summarize(translated)
                log.info("[Agent2] resumen OK")

                # Un solo mensaje de respuesta con todo el resultado
                response = (
                    f"[Traducción] {translated}\n\n"
                    f"[Resumen]\n{summary}"
                )
                await event_queue.enqueue_event(make_text_message(response))
            else:
                log.info("[Agent2] modo simple (solo resumir)")
                summary = await self._logic.summarize(text)
                log.info("[Agent2] resumen OK")
                await event_queue.enqueue_event(make_text_message(summary))

        except Exception as e:
            log.error(f"[Agent2] ERROR: {e}")
            await event_queue.enqueue_event(make_text_message(f"[Resumidor] Error: {e}"))

    @override
    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise Exception("cancel not supported")


def build_app(public_url: str, agent1_url: str) -> Starlette:
    card = AgentCard()
    card.name = "Agente Resumidor + Coordinador"
    card.description = (
        "Resume textos en 3 bullets. "
        "Puede coordinar con el Agente Traductor vía A2A."
    )
    card.version = "1.0.0"
    card.default_input_modes.append("text/plain")
    card.default_output_modes.append("text/plain")

    iface = card.supported_interfaces.add()
    iface.url = f"{public_url}/"

    s1 = card.skills.add()
    s1.id = "resume_text"
    s1.name = "Resumidor de texto"
    s1.description = "Resume cualquier texto en 3 bullets concisos."
    s1.tags.extend(["summary", "nlp", "text"])
    s1.examples.extend(["Resume este artículo: ..."])
    s1.input_modes.append("text/plain")
    s1.output_modes.append("text/plain")

    s2 = card.skills.add()
    s2.id = "translate_and_summarize"
    s2.name = "Traducir y Resumir (A2A)"
    s2.description = "Traduce ES→EN via Agent 1 (A2A) y luego resume en 3 bullets."
    s2.tags.extend(["a2a", "translation", "summary", "orchestration"])
    s2.examples.extend(["traduce y resume: El cambio climático es..."])
    s2.input_modes.append("text/plain")
    s2.output_modes.append("text/plain")

    handler = DefaultRequestHandler(
        agent_executor=SummarizerExecutor(agent1_url=agent1_url),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )

    routes = create_agent_card_routes(card) + create_jsonrpc_routes(handler, rpc_url="/")
    return Starlette(routes=routes)
