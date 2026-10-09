"""
Demo CLI — conversación fluida con handoff peer-to-peer.

Usa demo.client.A2AClient para la lógica de transporte y routing.
Esta capa solo pinta en Rich.
"""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule

from demo.client import A2AClient, RoutingLoopError

console = Console()

PORT        = int(os.getenv("SERVER_PORT", "9000"))
BASE_URL    = f"http://localhost:{PORT}"
GENERAL_URL = f"{BASE_URL}/general/"


async def health_check() -> bool:
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            await c.get(f"{BASE_URL}/")
        return True
    except Exception:
        return False


async def main() -> None:
    if not await health_check():
        console.print("[red]✗ Servidor no disponible. Ejecuta: python3 server.py[/red]")
        return

    async with A2AClient(entry_url=GENERAL_URL, entry_name="General") as client:
        console.print(Panel.fit(
            "[bold]A2A Multi-Agent — Atención al Cliente[/bold]\n"
            f"Session: [yellow]{client.session_id}[/yellow]\n"
            "[dim]Ctrl+C para salir[/dim]",
            border_style="blue",
        ))
        console.print("[green]✓ Servidor activo[/green]\n")

        try:
            while True:
                console.print(Rule(f"[dim]{client.current_name}[/dim]"))
                texto = Prompt.ask("[yellow]Mensaje[/yellow]")
                if not texto.strip():
                    continue

                console.print(f"[dim]→ {client.current_url}[/dim]\n")

                try:
                    reply = await client.send(texto)
                except RoutingLoopError as e:
                    console.print(
                        f"[red]✗ Loop de routing detectado: "
                        f"{' → '.join(e.visited)}[/red]\n"
                    )
                    client.reset_to_entry()
                    continue
                except Exception as e:
                    console.print(f"[red]✗ Error: {e}[/red]\n")
                    continue

                for hop in reply.handoff_path:
                    console.print(
                        f"[dim]⟳ Enrutando a [bold]{hop.agent_name}[/bold]: "
                        f"{hop.reason}[/dim]\n"
                    )

                console.print(Panel(
                    reply.text or "[sin respuesta]",
                    title=f"[green]{reply.agent_name}[/green]",
                    border_style="green",
                ))
                console.print()

        except KeyboardInterrupt:
            console.print(
                f"\n[bold green]✓ Sesión {client.session_id} finalizada[/bold green]"
            )


if __name__ == "__main__":
    asyncio.run(main())
