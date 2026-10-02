# A2A Multi-Agent Demo

Demo funcional del protocolo **Agent-to-Agent (A2A)** usando Python, donde múltiples agentes se comunican entre sí, comparten contexto de conversación y se enrutan dinámicamente según el contenido del mensaje.

## ¿Qué es A2A?

A2A (Agent-to-Agent) es un protocolo abierto que permite a agentes de IA comunicarse entre sí de forma estandarizada usando HTTP + JSON-RPC. Cada agente expone una **Agent Card** en `/.well-known/agent-card.json` que describe su nombre, capacidades y skills — otros agentes la leen para decidir a quién llamar.

## Arquitectura

Un solo servidor, un solo puerto, tres agentes montados en sub-rutas:

```
http://localhost:9000/general/   → Agente General (entry point)
http://localhost:9000/sales/     → Agente de Ventas
http://localhost:9000/support/   → Agente de Soporte
http://localhost:9000/agents     → Discovery: lista las Agent Cards
```

### Agentes

| Agente | Rol | Ruta |
|--------|-----|------|
| **General** | Recibe todos los mensajes. Responde saludos y consultas genéricas. Enruta al especialista correcto cuando la intención es clara. | `/general/` |
| **Ventas** | Maneja productos, precios, cotizaciones, stock y proceso de compra. | `/sales/` |
| **Soporte** | Maneja problemas técnicos, quejas, tickets, devoluciones y garantías. | `/support/` |

### Flujo de comunicación

```
1er mensaje (saludo):
  Cliente → General → responde directamente

1er mensaje (intención clara):
  Cliente → General → descubre agentes → enruta → Ventas o Soporte

Siguientes mensajes:
  Cliente → Ventas (directo, sin pasar por General)
  Cliente → Soporte (directo, sin pasar por General)

Si el agente recibe algo fuera de su competencia:
  Ventas → descubre agentes → enruta → Soporte
  Soporte → descubre agentes → enruta → Ventas
```

## Características

- **Un solo servidor** — todos los agentes corren en el mismo proceso y puerto
- **Agent Cards** — cada agente describe sus capacidades en JSON siguiendo el estándar A2A
- **Discovery dinámico** — los agentes consultan `/agents` para descubrir a quién llamar, sin URLs hardcodeadas entre ellos
- **ReAct loop con function calling** — el LLM decide cuándo hacer discovery y cuándo enrutar usando tools reales
- **Session compartida** — todos los agentes leen y escriben en el mismo `SessionStore` en memoria, manteniendo el historial completo de la conversación
- **Peer-to-peer** — después del primer enrutamiento el cliente habla directo con el especialista sin intermediarios
- **OpenRouter** — cualquier modelo compatible con OpenAI (GPT-4o-mini, Claude, Mistral, etc.)

## Estructura del proyecto

```
a2a_multi_agent/
├── server.py                  # Servidor principal — monta los 3 agentes
├── agents/
│   ├── general.py             # Agente General (enrutador)
│   ├── sales.py               # Agente de Ventas
│   └── support.py             # Agente de Soporte
├── shared/
│   ├── llm.py                 # Cliente OpenRouter compartido
│   ├── proto_helpers.py       # Helpers para protobuf de a2a-sdk v1.2
│   ├── a2a_tools.py           # Tools A2A: discover_agents, call_agent, route_to + ReAct loop
│   └── session_store.py       # Store en memoria compartido por todos los agentes
├── demo/
│   └── demo.py                # Cliente interactivo CLI
├── .env.example
└── pyproject.toml
```

## Tools disponibles para cada agente

Todos los agentes tienen acceso a las mismas tools via `shared/a2a_tools.py`:

| Tool | Descripción |
|------|-------------|
| `discover_agents` | Consulta `/agents`, lee las Agent Cards y retorna la lista de agentes disponibles con sus skills y URLs |
| `call_agent` | Llama a otro agente via JSON-RPC A2A y retorna su respuesta |
| `route_to` | Enruta al cliente a otro agente — retorna la URL del especialista para que el cliente hable directo |

## Instalación

```bash
git clone <repo>
cd a2a_multi_agent

python3 -m venv .venv
source .venv/bin/activate

pip install uv
uv pip install a2a-sdk uvicorn httpx openai python-dotenv rich starlette sse-starlette
```

## Configuración

```bash
cp .env.example .env
```

Edita `.env`:

```env
OPENROUTER_API_KEY=sk-or-...      # Tu API key de OpenRouter
MODEL_NAME=openai/gpt-4o-mini     # Modelo a usar
SERVER_PORT=9000
```

## Uso

**Terminal 1 — Servidor:**
```bash
source .venv/bin/activate
python3 server.py
```

**Terminal 2 — Demo:**
```bash
source .venv/bin/activate
python3 demo/demo.py
```

Escribe mensajes libremente. El sistema enruta al agente correcto automáticamente. Ctrl+C para salir.

## Ejemplo de conversación

```
Mensaje: hola
→ General: ¡Hola! ¿En qué puedo ayudarte hoy?

Mensaje: quiero saber qué productos tienen
⟳ Enrutando a Agente de Ventas
→ Ventas: 🛒 Agente de Ventas: Tenemos los siguientes productos...

Mensaje: el producto que compré no funciona
⟳ Enrutando a Agente de Soporte
→ Soporte: 🎧 Agente de Soporte: Lamento el inconveniente. Tu ticket es #S-4821...
```

## Logs del servidor

Cada decisión del LLM es visible en los logs:

```
[General]  finish_reason=tool_calls
[General]  tool: discover_agents
[General]  tool: route_to({'agent_name': 'Agente de Ventas', ...})
[General]  *** ENRUTANDO a: Agente de Ventas → http://localhost:9000/sales/ ***

[Ventas]   ReAct loop — session=abc123
[Ventas]   respuesta directa: "🛒 Agente de Ventas: ..."
[SessionStore] [abc123] Ventas (assistant): "🛒 Agente de Ventas: ..."
```

## Tecnologías

| Tecnología | Uso |
|------------|-----|
| [a2a-sdk v1.2](https://github.com/google/a2a-python) | Protocolo A2A (protobuf, JSON-RPC, Agent Cards) |
| [Starlette](https://www.starlette.io/) | Servidor ASGI — montaje de sub-apps por ruta |
| [OpenRouter](https://openrouter.ai/) | LLM backend con function calling |
| [OpenAI Python SDK](https://github.com/openai/openai-python) | Cliente compatible con OpenRouter |
| [Rich](https://github.com/Textualize/rich) | CLI interactivo con colores y paneles |

## Conceptos demostrados

1. **Agent Card discovery** — los agentes se descubren leyendo cards JSON, no URLs hardcodeadas
2. **ReAct loop** — el LLM razona, llama tools, observa resultados y decide
3. **Function calling para orquestación** — `discover_agents`, `call_agent` y `route_to` como tools reales del LLM
4. **Session compartida** — historial de conversación accesible por todos los agentes
5. **Peer-to-peer routing** — el General enruta una vez y desaparece; el cliente habla directo con el especialista
6. **Multi-agent en un solo proceso** — varios agentes A2A montados como sub-apps Starlette en un único servidor
