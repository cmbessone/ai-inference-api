"""Resolución de API keys a `client_id`.

Las keys se guardan hasheadas (SHA-256): el proceso nunca compara secretos en texto plano y un
volcado de memoria/config no las expone directamente. El lookup se hace por hash, así que no
depende del contenido del secreto (sin timing leaks por comparación de strings).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Protocol


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def parse_api_keys(spec: str) -> Mapping[str, str]:
    """`"cliente:key,cliente2:key2"` → `{sha256(key): cliente}` (pura)."""
    pairs = (item.split(":", 1) for item in spec.split(",") if ":" in item)
    return {hash_key(key.strip()): client.strip() for client, key in pairs if key.strip()}


class KeyStore(Protocol):
    """Puerto: hoy lee de config; en producción, Redis/DB/secret manager con cache local."""

    def client_for(self, raw_key: str) -> str | None: ...


class StaticKeyStore:
    def __init__(self, hashed: Mapping[str, str]) -> None:
        self._hashed = dict(hashed)

    def client_for(self, raw_key: str) -> str | None:
        return self._hashed.get(hash_key(raw_key))
