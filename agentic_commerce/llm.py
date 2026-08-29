import os
import time

from google import genai
from google.genai import types
from pydantic import BaseModel

MODEL = os.getenv("AC_MODEL", "gemini-3.5-flash")
# The free-tier cap is per key PER MODEL, so a spent bucket is not the end of the
# run: the same keys have a full bucket on the next model. Tried in order.
MODEL_FALLBACKS = [
    m.strip()
    for m in os.getenv(
        "AC_MODEL_FALLBACKS",
        "gemini-3.6-flash,gemini-flash-latest,gemini-2.5-flash",
    ).split(",")
    if m.strip()
]
# Without this the SDK blocks on the socket indefinitely.
TIMEOUT_MS = int(os.getenv("AC_LLM_TIMEOUT_MS", "120000"))

# Rotate to another key and keep this one -- the condition clears on its own.
_TRANSIENT = (
    "quota",
    "429",
    "rate limit",
    "resource exhausted",
    "503",
    "500",
    "unavailable",
    "overloaded",
    "high demand",
    "timeout",
    "timed out",
    "deadline",
)
# The key's project is refused outright; it will fail identically on every later
# call, so drop it from the pool rather than rotating past it every time.
_KEY_FATAL = (
    "permission_denied",
    "denied access",
    "api key not valid",
    "consumer_suspended",
)
# The 429 no backoff fixes. Its retryDelay says ~30s and is misleading: this is the
# daily cap, and only a different model clears it before midnight.
_PER_DAY = "perday"

_ROUNDS = 3
_BACKOFF = (5, 15)  # seconds before rounds 2 and 3


class LLMUnavailable(Exception):
    """No key/model combination could serve this call. Callers fall back offline."""


class _ModelExhausted(Exception):
    def __init__(self, model: str):
        super().__init__(f"{model} is out of quota")
        self.model = model


def _load_keys() -> list[str]:
    keys = [k.strip() for i in range(1, 11) if (k := os.getenv(f"GOOGLE_API_KEY_{i}", "")).strip()]
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        if (k := os.getenv(name, "").strip()) and k not in keys:
            keys.append(k)
    return keys


class LLM:
    """One instance per process. Owns key rotation and usage counters."""

    def __init__(self):
        self.keys = _load_keys()
        self._next = 0
        self.requests = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.dropped_keys: list[str] = []
        self.exhausted_models: list[str] = []
        # Kept alive: a client built inline per request is garbage-collected
        # before the request completes.
        self._clients: dict[str, genai.Client] = {}

    @property
    def available(self) -> bool:
        return bool(self.keys)

    def _attempt_order(self) -> list[str]:
        """Every live key once, rotated so load spreads across calls.

        Returns a snapshot, not an index: dropping a denied key mutates self.keys
        mid-call, and rotating by index over a shrinking list skips keys.
        """
        start = self._next % len(self.keys)
        self._next += 1
        return self.keys[start:] + self.keys[:start]

    def _client(self, key: str) -> genai.Client:
        if key not in self._clients:
            self._clients[key] = genai.Client(
                api_key=key, http_options=types.HttpOptions(timeout=TIMEOUT_MS)
            )
        return self._clients[key]

    def _drop_key(self, key: str, reason: str) -> None:
        if key in self.keys:
            self.keys.remove(key)
            self._clients.pop(key, None)
            self.dropped_keys.append(f"...{key[-6:]}: {reason}")

    def call(
        self, prefix: str, suffix: str, schema: type[BaseModel], temperature: float = 0.0
    ) -> BaseModel:
        """Typed call returning an instance of `schema`.

        `prefix` must be byte-identical across calls: Gemini's implicit prompt cache
        keys on shared leading tokens. Temperature defaults to 0 so a re-sync of
        unchanged input produces unchanged output.
        """
        if not self.keys:
            raise LLMUnavailable("no GOOGLE_API_KEY_n / GEMINI_API_KEY in the environment")

        cfg = types.GenerateContentConfig(
            temperature=temperature,
            response_mime_type="application/json",
            response_schema=schema,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        chain = [m for m in [MODEL, *MODEL_FALLBACKS] if m not in self.exhausted_models]
        chain = chain or [MODEL]

        last_error: Exception | None = None
        for candidate in chain:
            try:
                return self._try_model(candidate, prefix + suffix, cfg, schema)
            except _ModelExhausted as e:
                last_error = e.__cause__ or e
                if candidate not in self.exhausted_models:
                    self.exhausted_models.append(candidate)
        raise LLMUnavailable(
            f"every model out of quota: {', '.join(chain)}; "
            f"dropped keys: {self.dropped_keys or 'none'}; last error: {last_error}"
        )

    def _try_model(self, model, contents, cfg, schema) -> BaseModel:
        """One model, every live key, `_ROUNDS` times with backoff.

        Raises _ModelExhausted when this model is out of quota. Anything else
        propagates: a malformed prompt does not get better on a different model.
        """
        last_error = None
        for round_no in range(_ROUNDS):
            if round_no:
                time.sleep(_BACKOFF[min(round_no - 1, len(_BACKOFF) - 1)])
            for key in self._attempt_order():
                if key not in self.keys:  # dropped earlier in this call
                    continue
                self.requests += 1
                try:
                    resp = self._client(key).models.generate_content(
                        model=model, contents=contents, config=cfg
                    )
                    if usage := getattr(resp, "usage_metadata", None):
                        self.input_tokens += usage.prompt_token_count or 0
                        self.output_tokens += usage.candidates_token_count or 0
                    return (
                        resp.parsed
                        if resp.parsed is not None
                        else schema.model_validate_json(resp.text)
                    )
                except Exception as e:
                    message, last_error = str(e).lower(), e
                    if any(t in message for t in _KEY_FATAL):
                        self._drop_key(key, "project denied")
                        continue
                    if not any(t in message for t in _TRANSIENT):
                        raise
                    if _PER_DAY in message:
                        raise _ModelExhausted(model) from e
            if not self.keys:
                break
        if not self.keys:
            raise LLMUnavailable(
                f"every key refused; dropped: {self.dropped_keys}; last error: {last_error}"
            )
        raise _ModelExhausted(model) from last_error

    def stats(self) -> dict:
        return {
            "requests": self.requests,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "live_keys": len(self.keys),
            "dropped_keys": self.dropped_keys,
            "exhausted_models": self.exhausted_models,
            "cost_usd": 0.0,
        }  # free tier
