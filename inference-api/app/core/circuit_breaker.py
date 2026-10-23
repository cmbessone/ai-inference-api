"""Circuit breaker: máquina de estados pura + stores intercambiables.

Diseño (ver ADR-007):
- La **lógica** (cuándo abrir, cuándo probar, cuándo cerrar) son funciones puras sobre un
  `BreakerSnapshot` inmutable. Se testea sin Redis ni reloj real.
- El **almacenamiento** del estado es un puerto (`BreakerStore`):
  * `RedisBreakerStore`: estado compartido entre réplicas mediante scripts Lua atómicos que
    replican exactamente las mismas reglas. Todas las réplicas abren/cierran juntas (HA).
  * `InMemoryBreakerStore`: estado local aplicando las funciones puras. Fallback si Redis cae y
    también se reutiliza como guardia de Redis mismo (`RedisGateway`).
  * `FallbackBreakerStore`: Composite que usa Redis y degrada a memoria.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from app.core.clock import Clock
from app.core.errors import BackendUnavailableError

if TYPE_CHECKING:
    from app.core.redis_gateway import RedisGateway


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half-open"


@dataclass(frozen=True, slots=True)
class BreakerPolicy:
    failure_threshold: int = 5
    window_s: float = 30.0
    open_s: float = 60.0
    probe_ttl_s: float = 30.0


@dataclass(frozen=True, slots=True)
class BreakerSnapshot:
    failures: tuple[float, ...] = ()
    opened_at: float | None = None
    probe_until: float | None = None


@dataclass(frozen=True, slots=True)
class Admission:
    allowed: bool
    state: CircuitState
    retry_after_s: int = 0
    is_probe: bool = False


@dataclass(frozen=True, slots=True)
class BreakerStatus:
    state: CircuitState
    retry_after_s: int = 0


# ── Núcleo funcional ────────────────────────────────────────────────────────────


def state_of(snapshot: BreakerSnapshot, now: float, policy: BreakerPolicy) -> CircuitState:
    if snapshot.opened_at is None:
        return CircuitState.CLOSED
    if now < snapshot.opened_at + policy.open_s:
        return CircuitState.OPEN
    return CircuitState.HALF_OPEN


def seconds_until_half_open(snapshot: BreakerSnapshot, now: float, policy: BreakerPolicy) -> int:
    if snapshot.opened_at is None:
        return 0
    return max(0, math.ceil(snapshot.opened_at + policy.open_s - now))


def admit(
    snapshot: BreakerSnapshot, now: float, policy: BreakerPolicy
) -> tuple[Admission, BreakerSnapshot]:
    """¿Puede pasar este request? En HALF_OPEN deja pasar un único probe a la vez."""
    state = state_of(snapshot, now, policy)
    if state is CircuitState.CLOSED:
        return Admission(allowed=True, state=state), snapshot
    if state is CircuitState.OPEN:
        retry = seconds_until_half_open(snapshot, now, policy)
        return Admission(allowed=False, state=state, retry_after_s=retry), snapshot
    if snapshot.probe_until is not None and now < snapshot.probe_until:
        retry = max(1, math.ceil(snapshot.probe_until - now))
        return Admission(allowed=False, state=state, retry_after_s=retry), snapshot
    probing = replace(snapshot, probe_until=now + policy.probe_ttl_s)
    return Admission(allowed=True, state=state, is_probe=True), probing


def on_failure(snapshot: BreakerSnapshot, now: float, policy: BreakerPolicy) -> BreakerSnapshot:
    state = state_of(snapshot, now, policy)
    if state is CircuitState.HALF_OPEN:
        return BreakerSnapshot(opened_at=now)  # el probe falló: reabrir
    if state is CircuitState.OPEN:
        return snapshot  # fallos tardíos de requests que ya estaban en vuelo
    failures = tuple(t for t in (*snapshot.failures, now) if t > now - policy.window_s)
    if len(failures) >= policy.failure_threshold:
        return BreakerSnapshot(opened_at=now)
    return replace(snapshot, failures=failures)


def on_success(snapshot: BreakerSnapshot, now: float, policy: BreakerPolicy) -> BreakerSnapshot:
    if state_of(snapshot, now, policy) is CircuitState.HALF_OPEN:
        return BreakerSnapshot()  # el probe salió bien: cerrar y olvidar fallos
    return snapshot


def status_of(snapshot: BreakerSnapshot, now: float, policy: BreakerPolicy) -> BreakerStatus:
    return BreakerStatus(
        state=state_of(snapshot, now, policy),
        retry_after_s=seconds_until_half_open(snapshot, now, policy),
    )


# ── Stores (cáscara imperativa) ─────────────────────────────────────────────────


class BreakerStore(Protocol):
    async def admit(self, now: float) -> Admission: ...

    async def record_failure(self, now: float) -> None: ...

    async def record_success(self, now: float) -> None: ...

    async def status(self, now: float) -> BreakerStatus: ...


class InMemoryBreakerStore:
    """Aplica las funciones puras sobre un snapshot local.

    No necesita locks: no hay `await` entre leer y escribir el snapshot, y el event loop es
    single-threaded, así que cada transición es atómica.
    """

    def __init__(self, policy: BreakerPolicy) -> None:
        self._policy = policy
        self._snapshot = BreakerSnapshot()

    async def admit(self, now: float) -> Admission:
        admission, self._snapshot = admit(self._snapshot, now, self._policy)
        return admission

    async def record_failure(self, now: float) -> None:
        self._snapshot = on_failure(self._snapshot, now, self._policy)

    async def record_success(self, now: float) -> None:
        self._snapshot = on_success(self._snapshot, now, self._policy)

    async def status(self, now: float) -> BreakerStatus:
        return status_of(self._snapshot, now, self._policy)


# Los scripts Lua replican `admit`, `on_failure` y `on_success`. Las claves comparten el hash tag
# `{cb:<name>}` para caer en el mismo slot si se usa Redis Cluster (Lua multi-key lo requiere).
_ADMIT_LUA = """
local opened = redis.call('GET', KEYS[1])
if not opened then return {1, 'closed', 0, 0} end
local now = tonumber(ARGV[1])
local reopen_at = tonumber(opened) + tonumber(ARGV[2])
if now < reopen_at then return {0, 'open', reopen_at - now, 0} end
if redis.call('SET', KEYS[2], '1', 'NX', 'PX', ARGV[3]) then return {1, 'half-open', 0, 1} end
return {0, 'half-open', math.max(redis.call('PTTL', KEYS[2]), 1000), 0}
"""

_FAILURE_LUA = """
local now = tonumber(ARGV[1])
local opened = redis.call('GET', KEYS[2])
if opened then
  if now < tonumber(opened) + tonumber(ARGV[4]) then return 'open' end
  redis.call('SET', KEYS[2], ARGV[1], 'PX', ARGV[6])
  redis.call('DEL', KEYS[1], KEYS[3])
  return 'reopened'
end
redis.call('ZADD', KEYS[1], now, ARGV[5])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - tonumber(ARGV[2]))
redis.call('PEXPIRE', KEYS[1], ARGV[2])
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[3]) then
  redis.call('SET', KEYS[2], ARGV[1], 'PX', ARGV[6])
  redis.call('DEL', KEYS[1])
  return 'opened'
end
return 'closed'
"""

_SUCCESS_LUA = """
local opened = redis.call('GET', KEYS[2])
if opened and tonumber(ARGV[1]) >= tonumber(opened) + tonumber(ARGV[2]) then
  redis.call('DEL', KEYS[1], KEYS[2], KEYS[3])
  return 1
end
return 0
"""

# El estado "abierto" se guarda con un TTL de seguridad largo: si nadie vuelve a llamar, Redis
# limpia la clave sola. Nunca vence antes de tiempo: es muy superior a `open_s`.
_OPEN_KEY_SAFETY_TTL_MS = 24 * 3600 * 1000


def _ms(seconds: float) -> int:
    return int(seconds * 1000)


class RedisBreakerStore:
    def __init__(self, gateway: RedisGateway, policy: BreakerPolicy, name: str = "llm") -> None:
        self._gateway = gateway
        self._policy = policy
        tag = f"{{cb:{name}}}"
        self._failures_key = f"{tag}:failures"
        self._opened_key = f"{tag}:opened_at"
        self._probe_key = f"{tag}:probe"
        self._admit = gateway.register_script(_ADMIT_LUA)
        self._failure = gateway.register_script(_FAILURE_LUA)
        self._success = gateway.register_script(_SUCCESS_LUA)

    async def admit(self, now: float) -> Admission:
        keys = [self._opened_key, self._probe_key]
        args = [_ms(now), _ms(self._policy.open_s), _ms(self._policy.probe_ttl_s)]
        allowed, state, wait_ms, is_probe = await self._gateway.run(
            lambda _: self._admit(keys=keys, args=args)
        )
        return Admission(
            allowed=bool(allowed),
            state=CircuitState(_decode(state)),
            retry_after_s=math.ceil(int(wait_ms) / 1000),
            is_probe=bool(is_probe),
        )

    async def record_failure(self, now: float) -> None:
        keys = [self._failures_key, self._opened_key, self._probe_key]
        args: list[int | str] = [
            _ms(now),
            _ms(self._policy.window_s),
            self._policy.failure_threshold,
            _ms(self._policy.open_s),
            f"{_ms(now)}:{uuid.uuid4().hex}",
            _OPEN_KEY_SAFETY_TTL_MS,
        ]
        await self._gateway.run(lambda _: self._failure(keys=keys, args=args))

    async def record_success(self, now: float) -> None:
        keys = [self._failures_key, self._opened_key, self._probe_key]
        args = [_ms(now), _ms(self._policy.open_s)]
        await self._gateway.run(lambda _: self._success(keys=keys, args=args))

    async def status(self, now: float) -> BreakerStatus:
        raw = await self._gateway.run(lambda r: r.get(self._opened_key))
        opened_at = None if raw is None else int(raw) / 1000
        return status_of(BreakerSnapshot(opened_at=opened_at), now, self._policy)


def _decode(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


class FallbackBreakerStore:
    """Composite: Redis como fuente de verdad compartida y memoria local si Redis no responde."""

    def __init__(self, primary: BreakerStore, fallback: BreakerStore) -> None:
        self._primary = primary
        self._fallback = fallback

    async def admit(self, now: float) -> Admission:
        try:
            return await self._primary.admit(now)
        except BackendUnavailableError:
            return await self._fallback.admit(now)

    async def record_failure(self, now: float) -> None:
        await self._fallback.record_failure(now)  # la memoria local siempre se mantiene al día
        try:
            await self._primary.record_failure(now)
        except BackendUnavailableError:
            return

    async def record_success(self, now: float) -> None:
        await self._fallback.record_success(now)
        try:
            await self._primary.record_success(now)
        except BackendUnavailableError:
            return

    async def status(self, now: float) -> BreakerStatus:
        try:
            return await self._primary.status(now)
        except BackendUnavailableError:
            return await self._fallback.status(now)


class CircuitBreaker:
    """Fachada que combina política, store y reloj."""

    def __init__(self, store: BreakerStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    async def admit(self) -> Admission:
        return await self._store.admit(self._clock.now())

    async def record_failure(self) -> None:
        await self._store.record_failure(self._clock.now())

    async def record_success(self) -> None:
        await self._store.record_success(self._clock.now())

    async def status(self) -> BreakerStatus:
        return await self._store.status(self._clock.now())
