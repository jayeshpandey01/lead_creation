"""Small OpenRouter client shared by research and email drafting."""
import requests

from .settings import settings


def chat_completion(messages: list[dict], temperature: float) -> str:
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")

    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://copys-client.vercel.app",
            "X-Title": "Copys Lead Research",
        },
        json={
            "model": settings.openrouter_model,
            "messages": messages,
            "temperature": temperature,
        },
        timeout=90,
    )
    response.raise_for_status()
    result = response.json()
    try:
        content = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("OpenRouter returned no chat completion content") from exc
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("OpenRouter returned an empty chat completion")
    return content.strip()
