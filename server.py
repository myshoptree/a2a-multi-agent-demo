"""
Servidor principal: un proceso, tres agentes, un puerto.
SessionStore compartido — todos los agentes leen/escriben el mismo historial.

  /general/   → Agente General (entry point)
  /sales/     → Agente de Ventas
  /support/   → Agente de Soporte
  /agents     → Discovery
  /           → Health check
"""
import sys, os, logging
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn
from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.requests import Request
from starlette.responses import JSONResponse

from agents.general  import build_app as build_general
from agents.sales    import build_app as build_sales
from agents.support  import build_app as build_support

load_dotenv()

PORT        = int(os.getenv("SERVER_PORT", "9000"))
HOST        = os.getenv("SERVER_HOST", "0.0.0.0")
BASE        = f"http://localhost:{PORT}"
GENERAL_URL = f"{BASE}/general"
SALES_URL   = f"{BASE}/sales"
SUPPORT_URL = f"{BASE}/support"

log = logging.getLogger("server")


async def root(request: Request) -> JSONResponse:
    return JSONResponse({
        "service": "A2A Multi-Agent Demo",
        "entry_point": GENERAL_URL,
        "agents": {
            "general": GENERAL_URL,
            "sales":   SALES_URL,
            "support": SUPPORT_URL,
        },
        "discovery": f"{BASE}/agents",
    })


async def agents_discovery(request: Request) -> JSONResponse:
    """
    Retorna las Agent Cards de Ventas y Soporte.
    El Agente General NO se incluye — es el coordinador, no un especialista.
    """
    import httpx

    candidates = [
        f"{SALES_URL}/.well-known/agent-card.json",
        f"{SUPPORT_URL}/.well-known/agent-card.json",
    ]
    found = []
    async with httpx.AsyncClient(timeout=5) as client:
        for url in candidates:
            try:
                r = await client.get(url)
                if r.status_code == 200:
                    found.append({"card_url": url, "card": r.json()})
                    log.info(f"[discovery] OK  {url}")
                else:
                    found.append({"card_url": url, "error": f"HTTP {r.status_code}"})
            except Exception as e:
                found.append({"card_url": url, "error": str(e)})
                log.error(f"[discovery] ERROR {url} → {e}")

    return JSONResponse({
        "server": BASE,
        "total":  len([x for x in found if "card" in x]),
        "agents": found,
    })


def build_main_app() -> Starlette:
    general_app = build_general(public_url=GENERAL_URL, server_base=BASE)
    sales_app   = build_sales(public_url=SALES_URL,     server_base=BASE)
    support_app = build_support(public_url=SUPPORT_URL, server_base=BASE)

    return Starlette(routes=[
        Route("/",       endpoint=root),
        Route("/agents", endpoint=agents_discovery),
        Mount("/general", app=general_app),
        Mount("/sales",   app=sales_app),
        Mount("/support", app=support_app),
    ])


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║         A2A Multi-Agent — Atención al Cliente                ║
╠══════════════════════════════════════════════════════════════╣
║  Entry point → http://localhost:{PORT}/general/               ║
║                                                              ║
║  Agentes:                                                    ║
║    General  → http://localhost:{PORT}/general/                ║
║    Ventas   → http://localhost:{PORT}/sales/                  ║
║    Soporte  → http://localhost:{PORT}/support/                ║
║                                                              ║
║  Discovery  → http://localhost:{PORT}/agents                  ║
╚══════════════════════════════════════════════════════════════╝
""")
    uvicorn.run(build_main_app(), host=HOST, port=PORT, log_level="info")
