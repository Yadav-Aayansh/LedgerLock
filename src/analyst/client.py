"""Transport for the analyst: any provider, by base URL + API key.

Two wire protocols cover essentially everything:

  * `anthropic` — Anthropic's Messages API. Spoken through the official SDK,
    which also accepts a custom `base_url`, so any Anthropic-compatible
    gateway works here too.
  * `openai`    — the OpenAI `/chat/completions` shape, which OpenAI,
    OpenRouter, Google's OpenAI-compatible endpoint, vLLM, Ollama, LM Studio
    and most local proxies all speak. Implemented over stdlib HTTP so the
    demo keeps its no-install promise.

Presets fill in the base URL, protocol and key variable for common providers;
every part can be overridden. API keys come from the environment, or are handed
in at call time by the web viewer — never from a command-line flag, and never
persisted. They must not end up in shell history, the audit trail, the response
cache, the provider sidecar, or results.md.

`make demo` must produce the numbers in results.md on a machine with no
credentials at all, and a report whose figures move between runs is not
reproducible. So the default is record/replay: a live run writes every
response to `runs/analyst_cache.jsonl` keyed by a hash of the exact prompt
(plus the provider and model), and later runs replay it.

If a line has no cached response and no credentials, the analyst is reported
as not run for that line — never silently skipped, and never faked.
"""

import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ANTHROPIC_PROTOCOL, OPENAI_PROTOCOL = "anthropic", "openai"
OFF, REPLAY, LIVE = "off", "replay", "live"

# A stated assumption, not a live rate. results.md says so.
USD_TO_INR = 88.0

# USD per 1M tokens, input/output, for models whose published rates are known
# here. Anything else records tokens and declines to assert a price -- a made
# up rate in results.md is worse than no rate. Supply your own with
# --analyst-price IN,OUT.
PRICING = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


@dataclass(frozen=True)
class Provider:
    name: str
    protocol: str
    base_url: str = ""
    model: str = ""
    key_env: str = ""
    price: tuple = ()
    # A credential supplied at call time rather than through the environment --
    # the web viewer's key field. `repr=False` so it cannot surface in a
    # traceback or a debug print, and nothing serialises it: the sidecar writes
    # named fields only, and the cache records the prompt and the reply.
    key_value: str = field(default="", repr=False, compare=False)

    @property
    def api_key(self):
        """In-memory credential first, then the environment."""
        if self.key_value:
            return self.key_value
        return os.environ.get(self.key_env, "") if self.key_env else ""

    @property
    def key_source(self):
        if self.key_value:
            return "supplied at call time"
        if self.key_env and os.environ.get(self.key_env):
            return f"${self.key_env}"
        return "none"

    def describe(self):
        where = self.base_url or "the provider default endpoint"
        return f"`{self.model}` via {self.protocol} at {where}"


# base_url, protocol, key env var. Model is deliberately not defaulted for
# third-party providers: guessing a model id that may not exist would put a
# fabricated name in the report.
PRESETS = {
    "anthropic":  ("", ANTHROPIC_PROTOCOL, "ANTHROPIC_API_KEY", "claude-opus-5"),
    "openai":     ("https://api.openai.com/v1", OPENAI_PROTOCOL, "OPENAI_API_KEY", ""),
    "openrouter": ("https://openrouter.ai/api/v1", OPENAI_PROTOCOL, "OPENROUTER_API_KEY", ""),
    "gemini":     ("https://generativelanguage.googleapis.com/v1beta/openai",
                   OPENAI_PROTOCOL, "GEMINI_API_KEY", ""),
    "local":      ("http://127.0.0.1:8090/v1", OPENAI_PROTOCOL, "", ""),
}


# Display name and a few current model ids per provider, so the viewer can
# offer something valid instead of asking people to remember exact strings.
# Suggestions only: any id can be typed, none of these are validated here, and
# PRESETS deliberately still defaults no model -- a guessed id in results.md
# would be a fabricated measurement.
PROVIDER_INFO = {
    "anthropic": ("Anthropic", [
        "claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001", "claude-fable-5-1"]),
    "openai": ("OpenAI", [
        "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5"]),
    "openrouter": ("OpenRouter", [
        "anthropic/claude-opus-latest", "anthropic/claude-sonnet-latest",
        "openai/gpt-latest", "google/gemini-pro-latest", "google/gemini-flash-latest"]),
    "gemini": ("Google Gemini", [
        "gemini-3.8-flash", "gemini-3.1-flash-lite", "gemini-2.5-pro", "gemini-2.5-flash"]),
    "local": ("Local or custom", []),
}


def build_provider(preset="anthropic", base_url=None, model=None, protocol=None,
                   key_env=None, price=None, key_value=None):
    """Preset first, then explicit overrides. Everything is overridable."""
    if preset not in PRESETS:
        raise ValueError(f"unknown provider preset {preset!r}; "
                         f"choose from {', '.join(sorted(PRESETS))}")
    p_url, p_proto, p_key, p_model = PRESETS[preset]
    model = model or p_model
    return Provider(
        name=preset,
        protocol=protocol or p_proto,
        base_url=(base_url if base_url is not None else p_url).rstrip("/"),
        model=model,
        key_env=key_env if key_env is not None else p_key,
        price=tuple(price) if price else PRICING.get(model, ()),
        key_value=key_value or "",
    )


def cost_usd(usage, provider):
    """None when no rate is known. Tokens are still counted."""
    if not provider.price:
        return None
    return (usage["input_tokens"] * provider.price[0]
            + usage["output_tokens"] * provider.price[1]) / 1_000_000


class AnalystClient:
    def __init__(self, mode, cache_path, system, schema, provider=None):
        self.cache_path = Path(cache_path)
        self.system = system
        self.schema = schema
        self.provider = provider or build_provider("anthropic")
        self.calls = 0
        self.usage = {"input_tokens": 0, "output_tokens": 0}
        self._client = None
        self.unavailable_reason = ""
        # _resolve sets unavailable_reason, so it must run after the defaults.
        self.cache = self._load_cache()
        self.mode = self._resolve(mode)

    @property
    def model(self):
        return self.provider.model

    def _load_cache(self):
        if not self.cache_path.exists():
            return {}
        with self.cache_path.open(encoding="utf-8") as fh:
            return {r["key"]: r for r in (json.loads(l) for l in fh if l.strip())}

    def _fallback(self, reason):
        self.unavailable_reason = reason
        return REPLAY if self.cache else OFF

    def _resolve(self, mode):
        if mode == LIVE:
            p = self.provider
            if not p.model:
                return self._fallback(f"no model named for provider '{p.name}' "
                                      f"(pass --analyst-model)")
            if p.protocol == OPENAI_PROTOCOL:
                if not p.base_url:
                    return self._fallback("no base URL for an OpenAI-shaped endpoint")
                if not p.api_key and p.key_env:
                    return self._fallback(f"${p.key_env} is not set")
                return LIVE
            try:
                import anthropic  # noqa: F401
            except ImportError:
                return self._fallback("the `anthropic` package is not installed")
            if not (p.api_key or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
                return self._fallback("no Anthropic credentials in the environment")
            return LIVE
        if mode == REPLAY and not self.cache:
            # Relative, not absolute: an absolute path in results.md is
            # machine-specific and breaks the reproducibility promise.
            return self._fallback("no cached responses at "
                                  f"{self.cache_path.parent.name}/{self.cache_path.name}")
        return mode

    def key(self, packet):
        # Provider and model are part of the key: replaying one model's answer
        # under another model's name would be a fabricated measurement.
        blob = json.dumps({"protocol": self.provider.protocol, "model": self.provider.model,
                           "system": self.system, "packet": packet},
                          sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:32]

    def propose(self, packet):
        """Returns (proposal_dict, source) or (None, reason)."""
        key = self.key(packet)

        if key in self.cache:
            rec = self.cache[key]
            self.usage["input_tokens"] += rec["usage"]["input_tokens"]
            self.usage["output_tokens"] += rec["usage"]["output_tokens"]
            return rec["proposal"], "replayed from cache"

        if self.mode != LIVE:
            return None, (self.unavailable_reason or "analyst disabled")

        call = (self._call_openai if self.provider.protocol == OPENAI_PROTOCOL
                else self._call_anthropic)
        proposal, usage, err = call(packet)
        if err:
            return None, err

        self.calls += 1
        self.usage["input_tokens"] += usage["input_tokens"]
        self.usage["output_tokens"] += usage["output_tokens"]
        self._record(key, proposal, usage)
        return proposal, f"live call to {self.provider.model}"

    def _record(self, key, proposal, usage):
        # Note what was asked and what came back -- never the credential.
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self.cache_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"key": key, "protocol": self.provider.protocol,
                                 "model": self.provider.model, "proposal": proposal,
                                 "usage": usage}) + "\n")
        self.cache[key] = {"key": key, "proposal": proposal, "usage": usage}

    # -- OpenAI-shaped /chat/completions -----------------------------------

    def _call_openai(self, packet):
        p = self.provider
        body = json.dumps({
            "model": p.model,
            "max_tokens": 8000,
            "messages": [{"role": "system", "content": self.system},
                         {"role": "user", "content": json.dumps(packet, indent=2)}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "reconciliation_proposal", "strict": True, "schema": self.schema}},
        }).encode()
        headers = {"Content-Type": "application/json"}
        if p.api_key:
            headers["Authorization"] = f"Bearer {p.api_key}"
        req = urllib.request.Request(f"{p.base_url}/chat/completions",
                                     data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            # Report the status, not the body: an error body can echo the request.
            return None, None, f"endpoint returned HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError) as exc:
            return None, None, f"endpoint unreachable: {exc.reason if hasattr(exc, 'reason') else exc}"

        choice = (data.get("choices") or [{}])[0]
        if choice.get("finish_reason") == "length":
            return None, None, "response hit the token cap before completing"
        text = (choice.get("message") or {}).get("content")
        if not text:
            return None, None, ("no content in the response "
                                f"(finish_reason={choice.get('finish_reason')})")
        u = data.get("usage", {})
        usage = {"input_tokens": u.get("prompt_tokens", 0),
                 "output_tokens": u.get("completion_tokens", 0)}
        try:
            return json.loads(text), usage, None
        except json.JSONDecodeError as exc:
            return None, None, f"response was not valid JSON: {exc}"

    # -- Anthropic Messages API --------------------------------------------

    def _call_anthropic(self, packet):
        import anthropic
        p = self.provider
        if self._client is None:
            kwargs = {}
            if p.api_key:
                kwargs["api_key"] = p.api_key
            if p.base_url:
                kwargs["base_url"] = p.base_url
            self._client = anthropic.Anthropic(**kwargs)

        response = self._client.beta.messages.create(
            model=p.model,
            max_tokens=8000,
            system=self.system,
            messages=[{"role": "user", "content": json.dumps(packet, indent=2)}],
            thinking={"type": "adaptive"},
            output_config={"effort": "high",
                           "format": {"type": "json_schema", "schema": self.schema}},
            # Server-side fallback: on a policy decline the same request is
            # re-run on a fallback model inside the same call, rather than the
            # run simply stopping.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )

        if response.stop_reason == "refusal":
            detail = getattr(response.stop_details, "explanation", "") or "refused"
            return None, None, f"model declined to answer: {detail}"

        text = next((b.text for b in response.content if b.type == "text"), None)
        if not text:
            return None, None, "no text block in the response"

        return (json.loads(text),
                {"input_tokens": response.usage.input_tokens,
                 "output_tokens": response.usage.output_tokens},
                None)
