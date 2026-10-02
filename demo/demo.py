"""
Demo: Conversación fluida con enrutamiento transparente.

Flujo:
  1er mensaje → General → retorna URL del especialista
  Siguientes  → habla directo con el especialista
  Si el especialista enruta → el cliente sigue al nuevo agente
  Ctrl+C para salir
"""
import sys, os, asyncio, uuid, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from a2a.utils.constants import VERSION_HEADER, PROTOCOL_VERSION_1_0

console = Console()

PORT        = int(os.getenv("SERVER_PORT", "9000"))
GENERAL_URL = f"http://localhost:{PORT}/general/"
HEADERS     = {VERSION_HEADER: PROTOCOL_VERSION_1_0}


async def send(url: str, text: str, session_id: str) -> str:
    payload = {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex,
        "method": "SendMessage",
        "params": {
            "message": {
                "role":       "ROLE_USER",
                "message_id": uuid.uuid4().hex,
                "context_id": session_id,
                "parts": [{"text": text}],
            }
        },
    }
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(url, json=payload, headers=HEADERS)
        r.raise_for_status()
        data = r.json()

    if "error" in data:
        return None, f"[ERROR] {data['error'].get('message', str(data['error']))}"

    result   = data.get("result", {})
    msg      = result.get("message", {})
    raw_text = "\n".join(p["text"] for p in msg.get("parts", []) if "text" in p)

    # Intentar parsear como routing JSON
    try:
        parsed = json.loads(raw_text)
        if parsed.get("type") == "routing":
            return parsed, None
    except (json.JSONDecodeError, AttributeError):
        pass

    return None, raw_text


async def main():
    session_id   = uuid.uuid4().hex[:12]
    current_url  = GENERAL_URL
    current_name = "General"

    console.print(Panel.fit(
        "[bold]A2A Multi-Agent — Atención al Cliente[/bold]\n"
        f"Session: [yellow]{session_id}[/yellow]\n"
        "[dim]Ctrl+C para salir[/dim]",
        border_style="blue"
    ))

    try:
        async with httpx.AsyncClient(timeout=3) as c:
            await c.get(f"http://localhost:{PORT}/")
    except Exception:
        console.print("[red]✗ Servidor no disponible. Ejecuta: python3 server.py[/red]")
        return

    console.print("[green]✓ Servidor activo[/green]\n")

    try:
        while True:
            console.print(Rule(f"[dim]{current_name}[/dim]"))
            texto = Prompt.ask("[yellow]Mensaje[/yellow]")

            if not texto.strip():
                continue

            console.print(f"[dim]→ {current_url}[/dim]\n")

            routing, respuesta = await send(current_url, texto, session_id)

            if routing:
                new_name = routing.get("agent_name", "?")
                new_url  = routing.get("url", "")
                reason   = routing.get("reason", "")

                console.print(
                    f"[dim]⟳ Enrutando a [bold]{new_name}[/bold]: {reason}[/dim]\n"
                )

                current_url  = new_url
                current_name = new_name

                # Reenviar al nuevo agente y seguir hasta obtener respuesta real
                for _ in range(3):
                    routing2, respuesta2 = await send(current_url, texto, session_id)
                    if routing2:
                        current_url  = routing2.get("url", current_url)
                        current_name = routing2.get("agent_name", current_name)
                        console.print(f"[dim]⟳ Re-enrutado a [bold]{current_name}[/bold][/dim]\n")
                    else:
                        respuesta = respuesta2
                        break

            if respuesta:
                console.print(Panel(
                    respuesta,
                    title=f"[green]{current_name}[/green]",
                    border_style="green"
                ))
            else:
                console.print(f"[red]Sin respuesta del agente {current_name}[/red]")

            console.print()

    except KeyboardInterrupt:
        console.print(f"\n[bold green]✓ Sesión {session_id} finalizada[/bold green]")


if __name__ == "__main__":
    asyncio.run(main())
