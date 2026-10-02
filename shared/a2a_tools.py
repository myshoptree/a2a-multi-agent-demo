"""
shared/a2a_tools.py
===================
Tools A2A compartidas por todos los agentes.

Cada agente puede:
  - discover_agents : descubrir agentes disponibles
  - call_agent      : llamar a otro agente
  - route_to        : enrutar al cliente a otro agente si el mensaje no es de su competencia

El run_react_loop retorna siempre un AgentResponse:
  - type="answer"  → el agente responde directamente
  - type="routing" → el agente enruta a otro especialista { agent_name, url, reason }
"""
import logging, uuid, json
from dataclasses import dataclass
from typing import Optional

from a2a.utils.constants import VERSION_HEADER, PROTOCOL_VERSION_1_0
from shared.llm import get_llm_client, get_model
from shared.session_store import session_store

log = logging.getLogger("shared.a2a_tools")


# ── Resultado del loop ────────────────────────────────────────────────────────

@dataclass
class AgentResponse:
    type: str            # "answer" | "routing"
    content: str         # texto de respuesta si type="answer"
    agent_name: str = "" # nombre del agente destino si type="routing"
    url: str = ""        # URL del agente destino si type="routing"
    reason: str = ""     # razón del enrutamiento


# ── Definición de tools ───────────────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "discover_agents",
            "description": (
                "Descubre todos los agentes disponibles consultando /agents. "
                "Retorna nombre, descripción, skills y URL de cada agente. "
                "Úsala cuando el mensaje no sea de tu competencia y necesites enrutar."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "route_to",
            "description": (
                "Enruta al cliente a otro agente especializado. "
                "Úsala cuando el mensaje NO sea de tu competencia. "
                "El cliente hablará directo con ese agente."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_name": {
                        "type": "string",
                        "description": "Nombre del agente al que enrutas.",
                    },
                    "url": {
                        "type": "string",
                        "description": "URL del agente obtenida de discover_agents.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Por qué este agente es el indicado.",
                    },
                },
                "required": ["agent_name", "url", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "call_agent",
            "description": (
                "Llama a otro agente A2A y obtiene su respuesta. "
                "Úsala cuando necesites consultar a un especialista "
                "para complementar tu propia respuesta."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_name": {
                        "type": "string",
                        "description": "Nombre del agente a consultar.",
                    },
                    "url": {
                        "type": "string",
                        "description": "URL del agente obtenida de discover_agents.",
                    },
                    "message": {
                        "type": "string",
                        "description": "Mensaje a enviar al agente.",
                    },
                },
                "required": ["agent_name", "url", "message"],
            },
        },
    },
]


# ── Implementación de tools ───────────────────────────────────────────────────

async def discover_agents(server_base: str) -> str:
    import httpx

    url = f"{server_base}/agents"
    log.info(f"[discover_agents] GET {url}")

    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(url)
        r.raise_for_status()
        agents = [a for a in r.json().get("agents", []) if "card" in a]

    if not agents:
        return "No hay agentes disponibles."

    lines = []
    for entry in agents:
        card = entry["card"]
        interfaces = card.get("supportedInterfaces", [])
        agent_url = interfaces[0]["url"] if interfaces else card.get("url", "")
        if not agent_url.endswith("/"):
            agent_url += "/"
        skills = [
            f"  - [{s['id']}] {s['name']}: {s['description']}"
            for s in card.get("skills", [])
        ]
        lines.append(
            f"Agente: {card.get('name')}\n"
            f"Descripción: {card.get('description')}\n"
            f"URL: {agent_url}\n"
            f"Skills:\n" + "\n".join(skills)
        )
        log.info(f"[discover_agents] encontrado: {card.get('name')} → {agent_url}")

    return "\n\n---\n\n".join(lines)


async def call_agent(agent_name: str, url: str, message: str, session_id: str) -> str:
    import httpx

    if not url.endswith("/"):
        url += "/"

    log.info(f"[call_agent] → {agent_name} ({url}) session={session_id}")

    payload = {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex,
        "method": "SendMessage",
        "params": {
            "message": {
                "role":       "ROLE_USER",
                "message_id": uuid.uuid4().hex,
                "context_id": session_id,
                "parts": [{"text": message}],
            }
        },
    }

    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(url, json=payload,
                         headers={VERSION_HEADER: PROTOCOL_VERSION_1_0})
        r.raise_for_status()
        data = r.json()

    if "error" in data:
        log.error(f"[call_agent] error de {agent_name}: {data['error']}")
        return f"Error: {data['error'].get('message', '')}"

    result   = data.get("result", {})
    msg      = result.get("message", {})
    response = "\n".join(p["text"] for p in msg.get("parts", []) if "text" in p)
    log.info(f"[call_agent] respuesta de {agent_name}: \"{response[:80]}\"")
    return response


# ── ReAct loop compartido ─────────────────────────────────────────────────────

async def run_react_loop(
    agent_label: str,
    system_prompt: str,
    user_text: str,
    server_base: str,
    session_id: str,
) -> AgentResponse:
    """
    Ejecuta el ReAct loop.
    Retorna AgentResponse con type="answer" o type="routing".
    """
    client    = get_llm_client()
    model     = get_model()

    context_text = session_store.get_context_text(session_id)
    full_system  = (
        f"{system_prompt}\n\n"
        f"--- Historial de la conversación ---\n"
        f"{context_text}\n"
        f"------------------------------------"
    )

    history  = session_store.get_llm_messages(session_id)
    messages = (
        [{"role": "system", "content": full_system}]
        + history
        + [{"role": "user", "content": user_text}]
    )

    log.info(f"[{agent_label}] ReAct loop — session={session_id} modelo={model}")

    while True:
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            max_tokens=1024,
            temperature=0.2,
        )

        msg    = resp.choices[0].message
        finish = resp.choices[0].finish_reason
        log.info(f"[{agent_label}] finish_reason={finish}")

        messages.append(msg.model_dump(exclude_none=True))

        # ── Respuesta directa ──────────────────────────────────────────────
        if finish == "stop":
            final = msg.content or ""
            log.info(f"[{agent_label}] respuesta directa: \"{final[:80]}\"")
            session_store.add_message(session_id, "assistant", final, agent_label)
            return AgentResponse(type="answer", content=final)

        # ── Tool calls ─────────────────────────────────────────────────────
        if finish == "tool_calls" and msg.tool_calls:
            for tool_call in msg.tool_calls:
                name = tool_call.function.name
                args = json.loads(tool_call.function.arguments)

                log.info(f"[{agent_label}] tool: {name}({json.dumps(args)[:120]})")

                if name == "discover_agents":
                    result = await discover_agents(server_base)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": str(result),
                    })

                elif name == "route_to":
                    url = args["url"]
                    if not url.endswith("/"):
                        url += "/"
                    log.info(f"[{agent_label}] *** ENRUTANDO a: {args['agent_name']} → {url} ***")
                    log.info(f"[{agent_label}]   razón: {args['reason']}")
                    return AgentResponse(
                        type="routing",
                        content="",
                        agent_name=args["agent_name"],
                        url=url,
                        reason=args["reason"],
                    )

                elif name == "call_agent":
                    log.info(f"[{agent_label}] *** consultando a: {args['agent_name']} ***")
                    result = await call_agent(
                        agent_name=args["agent_name"],
                        url=args["url"],
                        message=args["message"],
                        session_id=session_id,
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": str(result),
                    })
        else:
            return AgentResponse(type="answer", content=msg.content or "[sin respuesta]")
