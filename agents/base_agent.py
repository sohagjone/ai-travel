import ast
import json
import os
import time
from typing import Any, Dict, List, Optional

from openai import NotFoundError, OpenAI, RateLimitError

# Free providers that speak the OpenAI API. The app tries them in this order and
# uses every one that has a key in Streamlit Secrets (or in a local .env file).
# If one fails or hits its limit, it automatically moves on to the next.
PROVIDERS = [
    {
        "name": "gemini",
        "secrets": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),  # either name works
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "preferred": [
            "gemini-2.5-flash-lite",
            "gemini-2.5-flash",
            "gemini-3-flash-preview",
            "gemini-flash-latest",
        ],
        "prefer_word": "flash",
        "avoid": (),
        "max_tokens": 4000,
        "max_prompt_chars": 60000,  # about 15,000 tokens
    },
    {
        "name": "groq",
        "secrets": ("GROQ_API_KEY",),
        "base_url": "https://api.groq.com/openai/v1",
        "preferred": [
            "meta-llama/llama-4-scout-17b-16e-instruct",
            "llama-3.3-70b-versatile",
        ],
        "prefer_word": None,
        # gpt-oss models are limited to 8,000 tokens per minute on the free plan
        "avoid": ("gpt-oss",),
        "max_tokens": 1500,  # Groq counts the requested output size too
        "max_prompt_chars": 20000,  # about 5,000 tokens
    },
]

# Models that can't do normal text chat
SKIP_WORDS = ("whisper", "tts", "guard", "orpheus", "playai", "embed", "image",
              "imagen", "veo", "aqa", "live", "audio", "vision-only")

_model_cache: Dict[str, str] = {}  # provider name -> model id (found once)


def _get_secret(name: str) -> Optional[str]:
    """Read a value from Streamlit Secrets, falling back to environment variables."""
    value = None
    try:
        import streamlit as st

        value = st.secrets.get(name)
    except Exception:
        pass  # no secrets file (e.g. running locally), fall back to env
    return value or os.getenv(name)


def _get_first_secret(names) -> Optional[str]:
    for name in names:
        value = _get_secret(name)
        if value:
            return value
    return None


def _trim_prompt(prompt: str, max_chars: int) -> str:
    """Cut very long prompts so they stay under the provider's token limits."""
    return prompt if len(prompt) <= max_chars else prompt[:max_chars]


def _pick_model(provider: dict, client: OpenAI) -> str:
    """Choose a model that really exists for this key (model lists change often)."""
    name = provider["name"]
    if name in _model_cache:
        return _model_cache[name]

    override = _get_secret(f"{name.upper()}_MODEL")  # e.g. GROQ_MODEL in Secrets
    if override:
        _model_cache[name] = override
        return override

    available = [m.id.replace("models/", "") for m in client.models.list().data]
    for candidate in provider["preferred"]:
        if candidate in available:
            _model_cache[name] = candidate
            return candidate

    chat = [m for m in available if not any(w in m.lower() for w in SKIP_WORDS)]
    avoid = provider.get("avoid", ())
    chat = [m for m in chat if not any(a in m.lower() for a in avoid)] or chat
    word = provider["prefer_word"]
    if word:
        preferred = [m for m in chat if word in m.lower()]
        chat = preferred or chat
    if not chat:
        raise RuntimeError(f"{name}: no usable chat model found. Got: {available}")
    _model_cache[name] = chat[0]
    return chat[0]


class BaseAgent:
    def __init__(self, name: str, instructions: str, agent_type: str = "tools"):
        # agent_type is accepted for backward compatibility with the old code.
        self.name = name
        self.instructions = instructions
        self.agent_type = agent_type

        self.clients: List[tuple] = []  # (provider, client)
        for p in PROVIDERS:
            key = _get_first_secret(p["secrets"])
            if key:
                self.clients.append((p, OpenAI(base_url=p["base_url"], api_key=key)))
        if not self.clients:
            raise RuntimeError(
                "No AI key found. In Streamlit Cloud go to Manage app -> Settings -> "
                "Secrets and add GEMINI_API_KEY and/or GROQ_API_KEY. "
                "Locally, put them in a .env file."
            )

    async def run(self, messages: list) -> Dict[str, Any]:
        """Default run method to be overridden by child classes"""
        raise NotImplementedError("Subclasses must implement run()")

    @staticmethod
    def _load_payload(content: str) -> Dict[str, Any]:
        """Turn the message content back into a dict (JSON first, then a Python
        literal). Never uses eval(), so user input cannot run code."""
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return ast.literal_eval(content)

    def _query_llm(self, prompt: str) -> str:
        """Ask the free cloud models, trying each provider in turn."""
        errors = []
        for provider, client in self.clients:
            pname = provider["name"]
            try:
                model = _pick_model(provider, client)
            except Exception as e:
                errors.append(f"{pname}: {str(e)[:400]}")
                continue

            safe_prompt = _trim_prompt(prompt, provider["max_prompt_chars"])

            for attempt in range(2):
                try:
                    response = client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": self.instructions},
                            {"role": "user", "content": safe_prompt},
                        ],
                        temperature=0.3,
                        max_tokens=provider["max_tokens"],
                    )
                    text = response.choices[0].message.content or ""
                    if text.strip():
                        return text
                    errors.append(f"{pname} ({model}): empty response")
                    break  # try the next provider
                except RateLimitError as e:
                    errors.append(f"{pname} ({model}) rate limit: {str(e)[:400]}")
                    if attempt == 0:
                        time.sleep(15)  # short wait, then retry once
                except NotFoundError as e:
                    _model_cache.pop(pname, None)  # model was retired; re-pick next time
                    errors.append(f"{pname} ({model}) not found: {str(e)[:400]}")
                    break
                except Exception as e:
                    errors.append(f"{pname} ({model}): {str(e)[:400]}")
                    break  # not a rate limit, go to the next provider

        raise RuntimeError("All providers failed. " + " | ".join(errors))

    # Old name kept so older code that calls _query_ollama still works.
    def _query_ollama(self, prompt: str) -> str:
        return self._query_llm(prompt)

    def _parse_json_safely(self, text: str) -> Dict[str, Any]:
        """Safely parse JSON from text, handling potential errors"""
        try:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1:
                return json.loads(text[start : end + 1])
            return {"error": "No JSON content found"}
        except json.JSONDecodeError:
            return {"error": "Invalid JSON content"}
