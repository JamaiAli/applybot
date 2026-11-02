"""
Unified LLM client for ApplyPilot.

Auto-detects provider from environment:
  GEMINI_API_KEY    -> Google Gemini (primary)
  OPENAI_API_KEY    -> OpenAI (fallback)
  ANTHROPIC_API_KEY -> Anthropic (fallback)
  LLM_URL           -> Local llama.cpp / Ollama compatible endpoint

LLM_MODEL env var overrides the default (fast) model for any provider.
LLM_MODEL_QUALITY env var sets a higher-quality model for critical steps
(resume tailoring, cover letters). Falls back to LLM_MODEL if not set.

When a model hits a 429 rate limit, the client automatically tries the
next model in the fallback chain — including cross-provider fallback to
OpenAI and Anthropic if their API keys are configured.
"""

import logging
import os
import time
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model registry — each entry knows its provider, endpoint, and API key
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelEntry:
    """A model with everything needed to call it."""
    name: str
    provider: str           # "gemini", "openai", "anthropic", "local"
    base_url: str
    api_key: str


def _build_fallback_chain(primary_model: str, quality: bool = False) -> list[ModelEntry]:
    """Build a cross-provider fallback chain starting from the primary model.

    Groq comes first (free & generous), then Gemini, OpenAI, DeepSeek, Anthropic.
    Only providers whose API key is configured are included; the provider that
    owns ``primary_model`` is pulled to the front of the chain.
    """
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "")
    groq_key = os.environ.get("GROQ_API_KEY", "")

    gemini_url = "https://generativelanguage.googleapis.com/v1beta/openai"
    openai_url = "https://api.openai.com/v1"
    anthropic_url = "https://api.anthropic.com"
    deepseek_url = "https://api.deepseek.com/v1"
    groq_url = "https://api.groq.com/openai/v1"

    if quality:
        groq_models = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
        gemini_models = ["gemini-3.1-pro-preview", "gemini-3.8-flash", "gemini-3.5-flash"]
        openai_models = ["gpt-4.1-mini", "gpt-4.1-nano"]
        anthropic_models = ["claude-sonnet-4-5-20250514", "claude-haiku-4-5-20251001"]
    else:
        groq_models = ["llama-3.1-8b-instant", "llama-3.3-70b-versatile"]
        gemini_models = ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.1-flash-lite"]
        openai_models = ["gpt-4.1-nano", "gpt-4.1-mini"]
        anthropic_models = ["claude-haiku-4-5-20251001"]
    deepseek_models = ["deepseek-chat"]

    # Provider priority (free/generous first). Anthropic uses its own API format
    # in _try_entry; the others (groq/gemini/openai/deepseek) are OpenAI-compatible.
    providers = [
        ("groq", groq_key, groq_url, groq_models),
        ("gemini", gemini_key, gemini_url, gemini_models),
        ("openai", openai_key, openai_url, openai_models),
        ("deepseek", deepseek_key, deepseek_url, deepseek_models),
        ("anthropic", anthropic_key, anthropic_url, anthropic_models),
    ]
    # Pull the provider that owns the primary model to the front (stable sort).
    providers.sort(key=lambda p: 0 if primary_model in p[3] else 1)

    chain: list[ModelEntry] = []
    for name, key, url, models in providers:
        if not key:
            continue
        start = models.index(primary_model) if primary_model in models else 0
        for m in models[start:]:
            chain.append(ModelEntry(m, name, url, key))

    if not chain:
        raise RuntimeError(
            "No LLM provider configured. Set GROQ_API_KEY, GEMINI_API_KEY, "
            "OPENAI_API_KEY, DEEPSEEK_API_KEY, or ANTHROPIC_API_KEY."
        )

    return chain


# ---------------------------------------------------------------------------
# Provider detection (for primary model selection)
# ---------------------------------------------------------------------------

def _detect_provider(quality: bool = False) -> tuple[str, str, str]:
    """Return (base_url, model, api_key) for the primary provider."""
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    groq_key = os.environ.get("GROQ_API_KEY", "")
    local_url = os.environ.get("LLM_URL", "")

    model_override = os.environ.get("LLM_MODEL", "")
    quality_model = os.environ.get("LLM_MODEL_QUALITY", "")

    if quality and quality_model:
        chosen_model = quality_model
    else:
        chosen_model = model_override

    if groq_key and not local_url:
        return (
            "https://api.groq.com/openai/v1",
            chosen_model or ("llama-3.3-70b-versatile" if quality else "llama-3.1-8b-instant"),
            groq_key,
        )
    if gemini_key and not local_url:
        return (
            "https://generativelanguage.googleapis.com/v1beta/openai",
            chosen_model or "gemini-3.8-flash",
            gemini_key,
        )
    if openai_key and not local_url:
        return (
            "https://api.openai.com/v1",
            chosen_model or "gpt-4.1-nano",
            openai_key,
        )
    if local_url:
        return (
            local_url.rstrip("/"),
            chosen_model or "local-model",
            os.environ.get("LLM_API_KEY", ""),
        )
    raise RuntimeError(
        "No LLM provider configured. "
        "Set GEMINI_API_KEY, OPENAI_API_KEY, or LLM_URL."
    )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

_MAX_RETRIES = 3
_TIMEOUT = 300  # seconds


class LLMClient:
    """Multi-provider LLM client with automatic model fallback."""

    def __init__(self, base_url: str, model: str, api_key: str,
                 quality: bool = False) -> None:
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        self.quality = quality
        self._fallback_chain = _build_fallback_chain(model, quality=quality)
        self._client = httpx.Client(timeout=_TIMEOUT)
        # Track which models are temporarily exhausted (daily limit)
        self._exhausted: dict[str, float] = {}

        chain_names = [f"{e.name} ({e.provider})" for e in self._fallback_chain]
        log.info("Fallback chain (%s): %s",
                 "quality" if quality else "fast", " -> ".join(chain_names))

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        """Send a chat completion request with automatic cross-provider fallback."""
        # Qwen3 optimization
        if "qwen" in self.model.lower() and messages:
            first = messages[0]
            if first.get("role") == "user" and not first["content"].startswith("/no_think"):
                messages = [{"role": first["role"], "content": f"/no_think\n{first['content']}"}] + messages[1:]

        # Build list of models to try: skip recently exhausted ones
        now = time.time()
        entries_to_try = [
            e for e in self._fallback_chain
            if e.name not in self._exhausted or (now - self._exhausted[e.name]) > 300
        ]
        if not entries_to_try:
            self._exhausted.clear()
            entries_to_try = list(self._fallback_chain)

        for idx, entry in enumerate(entries_to_try):
            is_last = (idx == len(entries_to_try) - 1)
            result = self._try_entry(entry, messages, temperature, max_tokens, is_last)
            if result is not None:
                return result

        raise RuntimeError(
            f"All models exhausted after trying: "
            f"{[e.name for e in entries_to_try]}. "
            "Wait a few minutes for rate limits to reset."
        )

    def _try_entry(self, entry: ModelEntry, messages: list[dict],
                   temperature: float, max_tokens: int,
                   is_last: bool = False) -> str | None:
        """Try a single model entry. Dispatches to the right provider."""
        if entry.provider == "anthropic":
            return self._try_anthropic(entry, messages, temperature, max_tokens, is_last)
        else:
            return self._try_openai_compat(entry, messages, temperature, max_tokens, is_last)

    def _try_openai_compat(self, entry: ModelEntry, messages: list[dict],
                           temperature: float, max_tokens: int,
                           is_last: bool = False) -> str | None:
        """Try an OpenAI-compatible endpoint (Gemini, OpenAI, local)."""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {entry.api_key}",
        }
        # DeepSeek deepseek-chat has an 8192 max output token limit
        if entry.provider == "deepseek":
            max_tokens = min(max_tokens, 8192)
        payload = {
            "model": entry.name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        for attempt in range(_MAX_RETRIES):
            try:
                resp = self._client.post(
                    f"{entry.base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                if resp.status_code == 402:
                    # Payment Required — account out of credits; mark exhausted for 1 hour
                    log.warning("%s/%s payment required (402), marking exhausted for 1h",
                                entry.provider, entry.name)
                    self._exhausted[entry.name] = time.time() + 3600 - 300  # 1h from now
                    return None

                if resp.status_code == 400:
                    body = resp.text.lower()
                    if "api_key_invalid" in body or "api key expired" in body:
                        log.warning("%s/%s API key invalid/expired, trying next",
                                    entry.provider, entry.name)
                        self._exhausted[entry.name] = time.time()
                        return None
                    # Any other 400 (content safety, model not found, malformed prompt)
                    # — don't mark exhausted (it's per-request, not a quota), just skip
                    if not is_last:
                        log.warning("%s/%s 400 Bad Request, trying next: %.120s",
                                    entry.provider, entry.name, resp.text)
                        return None

                if resp.status_code == 404:
                    log.warning("%s/%s model not found (404), trying next",
                                entry.provider, entry.name)
                    self._exhausted[entry.name] = time.time()
                    return None

                if resp.status_code == 429:
                    body = resp.text.lower()
                    if "resource has been exhausted" in body or "quota" in body or "rate_limit" in body:
                        log.warning("%s/%s hit quota limit, trying next",
                                    entry.provider, entry.name)
                        self._exhausted[entry.name] = time.time()
                        return None

                    if attempt < _MAX_RETRIES - 1:
                        wait = 2 ** attempt + 1
                        log.warning("%s/%s 429 (RPM), retry in %ds (%d/%d)",
                                    entry.provider, entry.name, wait,
                                    attempt + 1, _MAX_RETRIES)
                        time.sleep(wait)
                        continue
                    elif not is_last:
                        log.warning("%s/%s still 429, trying next model",
                                    entry.provider, entry.name)
                        return None
                    else:
                        resp.raise_for_status()

                if resp.status_code == 503 and attempt < _MAX_RETRIES - 1:
                    wait = 2 ** attempt
                    log.warning("%s/%s 503, retry in %ds", entry.provider, entry.name, wait)
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                data = resp.json()
                # Guard against malformed responses (null body, null choices, null content)
                if not isinstance(data, dict) or not data.get("choices"):
                    if not is_last:
                        log.warning("%s/%s: malformed response (no choices), trying next",
                                    entry.provider, entry.name)
                        return None
                    raise RuntimeError(
                        f"Malformed response from {entry.provider}/{entry.name}: "
                        f"no choices in {type(data).__name__}"
                    )
                text = data["choices"][0]["message"]["content"]
                if text is None:
                    # Model returned null content (refusal, tool_call, etc.)
                    if not is_last:
                        log.warning("%s/%s: null content in response, trying next",
                                    entry.provider, entry.name)
                        return None
                    raise RuntimeError(
                        f"Null content from {entry.provider}/{entry.name} "
                        f"(refusal: {data['choices'][0]['message'].get('refusal', 'none')})"
                    )

                if entry.name != self.model:
                    log.info("Used fallback %s/%s (primary: %s)",
                             entry.provider, entry.name, self.model)
                return text

            except httpx.TimeoutException:
                if attempt < _MAX_RETRIES - 1:
                    wait = 2 ** attempt
                    log.warning("%s/%s timeout, retry in %ds",
                                entry.provider, entry.name, wait)
                    time.sleep(wait)
                    continue
                if not is_last:
                    log.warning("%s/%s timeout after retries, trying next",
                                entry.provider, entry.name)
                    return None
                raise

        return None

    def _try_anthropic(self, entry: ModelEntry, messages: list[dict],
                       temperature: float, max_tokens: int,
                       is_last: bool = False) -> str | None:
        """Try the Anthropic Messages API (different format from OpenAI)."""
        headers = {
            "Content-Type": "application/json",
            "x-api-key": entry.api_key,
            "anthropic-version": "2023-06-01",
        }

        # Convert OpenAI message format to Anthropic format
        # Extract system message if present
        system_text = ""
        api_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_text = msg["content"]
            else:
                api_messages.append({
                    "role": msg["role"],
                    "content": msg["content"],
                })

        # Anthropic requires at least one user message
        if not api_messages:
            return None

        payload: dict = {
            "model": entry.name,
            "messages": api_messages,
            "max_tokens": max_tokens,
        }
        if system_text:
            payload["system"] = system_text
        if temperature > 0:
            payload["temperature"] = temperature

        for attempt in range(_MAX_RETRIES):
            try:
                resp = self._client.post(
                    f"{entry.base_url}/v1/messages",
                    json=payload,
                    headers=headers,
                )
                if resp.status_code == 429:
                    body = resp.text.lower()
                    if "rate_limit" in body or "quota" in body:
                        log.warning("anthropic/%s hit rate limit, trying next", entry.name)
                        self._exhausted[entry.name] = time.time()
                        return None

                    if attempt < _MAX_RETRIES - 1:
                        wait = 2 ** attempt + 1
                        log.warning("anthropic/%s 429, retry in %ds (%d/%d)",
                                    entry.name, wait, attempt + 1, _MAX_RETRIES)
                        time.sleep(wait)
                        continue
                    elif not is_last:
                        return None
                    else:
                        resp.raise_for_status()

                if resp.status_code == 529 and attempt < _MAX_RETRIES - 1:
                    # Anthropic overloaded
                    wait = 2 ** attempt + 2
                    log.warning("anthropic/%s overloaded (529), retry in %ds",
                                entry.name, wait)
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                data = resp.json()

                # Extract text from Anthropic response format
                text_parts = []
                for block in data.get("content", []):
                    if block.get("type") == "text":
                        text_parts.append(block["text"])
                text = "\n".join(text_parts)

                if entry.name != self.model:
                    log.info("Used fallback anthropic/%s (primary: %s)",
                             entry.name, self.model)
                return text

            except httpx.TimeoutException:
                if attempt < _MAX_RETRIES - 1:
                    wait = 2 ** attempt
                    log.warning("anthropic/%s timeout, retry in %ds",
                                entry.name, wait)
                    time.sleep(wait)
                    continue
                if not is_last:
                    return None
                raise

        return None

    def ask(self, prompt: str, **kwargs) -> str:
        """Convenience: single user prompt -> assistant response."""
        return self.chat([{"role": "user", "content": prompt}], **kwargs)

    def close(self) -> None:
        self._client.close()


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_instance: LLMClient | None = None
_quality_instance: LLMClient | None = None


def get_client(quality: bool = False) -> LLMClient:
    """Return (or create) the module-level LLMClient singleton.

    Args:
        quality: If True, return a client configured with LLM_MODEL_QUALITY
                 for critical steps like resume tailoring and cover letters.
    """
    global _instance, _quality_instance

    if quality and os.environ.get("LLM_MODEL_QUALITY"):
        if _quality_instance is None:
            base_url, model, api_key = _detect_provider(quality=True)
            log.info("LLM quality provider: %s  model: %s", base_url, model)
            _quality_instance = LLMClient(base_url, model, api_key, quality=True)
        return _quality_instance

    if _instance is None:
        base_url, model, api_key = _detect_provider()
        log.info("LLM provider: %s  model: %s", base_url, model)
        _instance = LLMClient(base_url, model, api_key, quality=False)
    return _instance
