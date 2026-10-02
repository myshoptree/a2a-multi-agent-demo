"""
Shared: cliente OpenRouter para ambos agentes.
"""
import os
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()


def get_llm_client() -> AsyncOpenAI:
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key or api_key.startswith("sk-or-xxx"):
        raise ValueError(
            "OPENROUTER_API_KEY no configurada.\n"
            "Copia .env.example → .env y pon tu API key."
        )
    return AsyncOpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        default_headers={
            "HTTP-Referer": "http://localhost:9000",
            "X-Title": "A2A Multi-Agent Demo",
        },
    )


def get_model() -> str:
    return os.getenv("MODEL_NAME", "openai/gpt-4o-mini")
