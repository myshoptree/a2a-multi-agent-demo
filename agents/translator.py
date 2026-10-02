"""
Agent 1: Orquestador con ReAct loop + function calling
======================================================
El LLM decide cuándo y cómo usar los otros agentes.

Tools disponibles para el LLM:
  - discover_agent(skill)        → consulta /agents, retorna URL del agente
  - call_agent(url, text)        → llama al agente encontrado via A2A JSON-RPC

Flujo:
  1. Cliente envía texto a Agent 1
  2. LLM razona qué necesita hacer
  3. LLM llama discover_agent para encontrar agentes por skill
  4. LLM llama call_agent para invocarlos
  5. LLM construye la respuesta final
"""
import sys, os, logging, uuid, json
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
from a2a.utils.constants import VERSION_HEADER, PROTOCOL_VERSION_1_0

from shared.llm import get_llm_client, get_model
from shared.proto_helpers import make_text_message, extract_text

log = logging.getLogger("agent1.orchestrator")

# ── Definición de tools para el LLM ──────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "discover_agent",
            "description": (
                "Descubre agentes disponibles en la red consultando el registro /agents. "
                "Retorna la URL del agente que tiene la skill solicitada."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "skill": {
                        "type": "string",
                        "description": "ID de la skill que necesitas. Ej: 'resume_text', 'translate_es_en'",
                    }
                },
                "required": ["skill"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "call_agent",
            "description": (
                "Llama a un agente A2A en la URL indicada y le envía un texto. "
                "Retorna la respuesta del agente."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL del agente a llamar (obtenida de discover_agent).",
                    },
                    "text": {
                        "type": "string",
                        "description": "Texto a enviar al agente.",
                    },
                },
                "required": ["url", "text"],
            },
        },
    },
]

SYSTEM_PROMPT = """Eres un agente orquestador inteligente.

Cuando recibas un mensaje del usuario debes:
1. Analizar qué necesita
2. Usar discover_agent para encontrar agentes especializados disponibles en la red
3. Usar call_agent para invocarlos con el texto apropiado
4. Construir una respuesta final clara y útil

Agentes que puedes descubrir:
- skill 'resume_text': resume cualquier texto en 3 bullets
- skill 'translate_es_en': traduce texto español → inglés

Siempre descubre los agentes antes de llamarlos — no asumas URLs.
"""


# ── Implementación de las tools ───────────────────────────────────────────────

async def discover_agent(skill: str, server_base: str) -> str:
    """Consulta /agents y retorna la URL del agente con la skill pedida."""
    import httpx

    discover_url = f"{server_base}/agents"
    log.info(f"[Tool:discover_agent] GET {discover_url} skill={skill}")

    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(discover_url)
        r.raise_for_status()
        agents = [a for a in r.json().get("agents", []) if "card" in a]

    for entry in agents:
        card = entry["card"]
        for s in card.get("skills", []):
            if s.get("id") == skill:
                interfaces = card.get("supportedInterfaces", [])
                url = interfaces[0]["url"] if interfaces else card.get("url", "")
                if not url.endswith("/"):
                    url += "/"
                log.info(f"[Tool:discover_agent] encontrado: {card.get('name')} → {url}")
                return url

    return f"No se encontró agente con skill '{skill}'"


async def call_agent(url: str, text: str) -> str:
    """Llama a un agente A2A via JSON-RPC y retorna su respuesta."""
    import httpx

    if not url.endswith("/"):
        url += "/"

    log.info(f"[Tool:call_agent] POST {url} texto={text[:60]}")

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

    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(url, json=payload,
                         headers={VERSION_HEADER: PROTOCOL_VERSION_1_0})
        r.raise_for_status()
        data = r.json()

    if "error" in data:
        log.error(f"[Tool:call_agent] error: {data['error']}")
        return f"Error del agente: {data['error'].get('message', '')}"

    result = data.get("result", {})
    msg = result.get("message", {})
    parts = msg.get("parts", [])
    response = "\n".join(p["text"] for p in parts if "text" in p)
    log.info(f"[Tool:call_agent] respuesta: \"{response[:80]}\"")
    return response


# ── ReAct loop ────────────────────────────────────────────────────────────────

async def run_react_loop(user_text: str, server_base: str) -> str:
    """
    Ejecuta el ReAct loop: el LLM razona, llama tools, observa resultados,
    hasta llegar a una respuesta final.
    """
    client = get_llm_client()
    model  = get_model()

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": user_text},
    ]

    log.info(f"[Agent1] iniciando ReAct loop modelo={model}")

    while True:
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            max_tokens=1024,
            temperature=0.2,
        )

        msg = resp.choices[0].message
        finish = resp.choices[0].finish_reason

        log.info(f"[Agent1] finish_reason={finish}")

        # Agregar respuesta del LLM al historial
        messages.append(msg.model_dump(exclude_none=True))

        # Si el LLM terminó — devolver respuesta final
        if finish == "stop":
            log.info(f"[Agent1] respuesta final: \"{(msg.content or '')[:80]}\"")
            return msg.content or ""

        # Si el LLM quiere llamar tools
        if finish == "tool_calls" and msg.tool_calls:
            for tool_call in msg.tool_calls:
                name = tool_call.function.name
                args = json.loads(tool_call.function.arguments)

                log.info(f"[Agent1] LLM invoca tool: {name}({args})")

                # Ejecutar la tool
                if name == "discover_agent":
                    result = await discover_agent(
                        skill=args["skill"],
                        server_base=server_base,
                    )
                elif name == "call_agent":
                    result = await call_agent(
                        url=args["url"],
                        text=args["text"],
                    )
                else:
                    result = f"Tool '{name}' no encontrada"

                log.info(f"[Agent1] tool result: \"{str(result)[:80]}\"")

                # Agregar resultado de la tool al historial
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": str(result),
                })

        else:
            # finish_reason inesperado
            return msg.content or "[sin respuesta]"


# ── AgentExecutor ─────────────────────────────────────────────────────────────

class OrchestratorExecutor(AgentExecutor):

    def __init__(self, server_base: str):
        self._server_base = server_base

    @override
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        text = extract_text(context)
        log.info(f"[Agent1] execute() → \"{text[:80]}\"")

        if not text:
            await event_queue.enqueue_event(make_text_message("Error: no se recibió texto."))
            return

        try:
            result = await run_react_loop(text, self._server_base)
            await event_queue.enqueue_event(make_text_message(result))
        except Exception as e:
            log.error(f"[Agent1] ERROR: {e}")
            await event_queue.enqueue_event(make_text_message(f"[Agent1] Error: {e}"))

    @override
    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise Exception("cancel not supported")


# ── Starlette sub-app ─────────────────────────────────────────────────────────

def build_app(public_url: str, server_base: str) -> Starlette:
    card = AgentCard()
    card.name = "Agente Orquestador"
    card.description = (
        "Agente principal. Recibe cualquier mensaje, razona con LLM "
        "y orquesta agentes especializados via A2A usando function calling."
    )
    card.version = "1.0.0"
    card.default_input_modes.append("text/plain")
    card.default_output_modes.append("text/plain")

    iface = card.supported_interfaces.add()
    iface.url = f"{public_url}/"

    skill = card.skills.add()
    skill.id = "orchestrate"
    skill.name = "Orquestador A2A"
    skill.description = "Razona y delega a agentes especializados via A2A."
    skill.tags.extend(["orchestration", "a2a", "llm"])
    skill.input_modes.append("text/plain")
    skill.output_modes.append("text/plain")

    handler = DefaultRequestHandler(
        agent_executor=OrchestratorExecutor(server_base=server_base),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )

    routes = create_agent_card_routes(card) + create_jsonrpc_routes(handler, rpc_url="/")
    return Starlette(routes=routes)
