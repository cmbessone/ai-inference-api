"""Cache de respuestas (cache-aside) y lock distribuido para single-flight.

La clave es una función pura del request (`cache_key`): mismo modelo + mismo input normalizado
+ misma config → misma clave (R1-AC09, R1-AC10). La serialización también es pura y está
versionada en el namespace, así un cambio de formato invalida el cache sin colisiones.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Callable, Mapping

from app.core.errors import BackendUnavailableError
from app.core.ports import Completion, Usage
from app.core.redis_gateway import RedisGateway

_NAMESPACE = "cache:infer:v1"

# ── Funciones puras ─────────────────────────────────────────────────────────────


def normalize_input(text: str) -> str:
    """Normalización conservadora: NFC + trim. No colapsa espacios internos (cambian el prompt)."""
    return unicodedata.normalize("NFC", text).strip()


def cache_key(model: str, text: str, config: Mapping[str, object]) -> str:
    canonical = json.dumps(
        {"model": model, "input": normalize_input(text), "config": dict(config)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return f"{_NAMESPACE}:{hashlib.sha256(canonical.encode()).hexdigest()}"


def lock_key(key: str) -> str:
    return f"{key}:lock"


def failure_key(key: str) -> str:
    return f"{key}:failed"


def encode_completion(completion: Completion) -> bytes:
    usage = completion.usage
    payload = {"text": completion.text, "in": usage.tokens_in, "out": usage.tokens_out}
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()


def decode_completion(raw: bytes) -> Completion:
    data = json.loads(raw)
    return Completion(text=data["text"], usage=Usage(tokens_in=data["in"], tokens_out=data["out"]))


# ── Adaptadores ─────────────────────────────────────────────────────────────────


class RedisCache:
    """Cache tolerante a fallos: si Redis no responde, se comporta como un cache vacío (R1-AC12)."""

    def __init__(self, gateway: RedisGateway, on_fallback: Callable[[], None] = lambda: None):
        self._gateway = gateway
        self._on_fallback = on_fallback

    async def get(self, key: str) -> bytes | None:
        try:
            value = await self._gateway.run(lambda r: r.get(key))
        except BackendUnavailableError:
            self._on_fallback()
            return None
        return value.encode() if isinstance(value, str) else value

    async def set(self, key: str, value: bytes, ttl_s: float) -> None:
        try:
            await self._gateway.run(lambda r: r.set(key, value, px=int(ttl_s * 1000)))
        except BackendUnavailableError:
            self._on_fallback()


_RELEASE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""


class RedisLock:
    """Lock con dueño (`SET NX PX` + compare-and-delete): sólo el dueño puede liberarlo.

    Si Redis no responde, `acquire` devuelve `True`: cada réplica actúa como líder de su propio
    single-flight local. Se pierde la deduplicación global, nunca la disponibilidad.
    """

    def __init__(self, gateway: RedisGateway) -> None:
        self._gateway = gateway
        self._release = gateway.register_script(_RELEASE_LUA)

    async def acquire(self, key: str, token: str, ttl_s: float) -> bool:
        try:
            acquired = await self._gateway.run(
                lambda r: r.set(key, token, nx=True, px=int(ttl_s * 1000))
            )
        except BackendUnavailableError:
            return True
        return bool(acquired)

    async def release(self, key: str, token: str) -> None:
        try:
            await self._gateway.run(lambda _: self._release(keys=[key], args=[token]))
        except BackendUnavailableError:
            return  # el TTL lo libera solo
