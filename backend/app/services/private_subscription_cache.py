"""Bounded RAM-only Mega details reuse for already-private access checks.

This never caches the Xtream authorization decision. Callers still validate
identity, credentials, DNS, expiry and current Xtream status on every check.
"""
from __future__ import annotations

import copy
import math
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock

from app.clients.mega import MegaClientError, MegaUpstreamError


@dataclass
class _Slot:
    gate: object = field(default_factory=Lock)
    users: int = 0
    value: object = None
    until: float = 0


class PrivateSubscriptionCache:
    def __init__(self, *, ttl_seconds=5.0, max_entries=64,
                 clock=time.monotonic, utcnow=lambda: datetime.now(UTC)):
        if not 0 < ttl_seconds <= 5 or not 1 <= max_entries <= 64:
            raise ValueError("Invalid private metadata cache bounds")
        self._ttl, self._max = ttl_seconds, max_entries
        self._clock, self._utcnow = clock, utcnow
        self._lock = Lock()
        self._slots = {}
        self._authority = None
        self._cooldown = 0.0

    def _expired(self, value):
        expires = getattr(value, "expiring_at", None)
        if expires is None:
            return False
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        return expires <= self._utcnow()

    def _prune_locked(self):
        now = self._clock()
        for key, slot in tuple(self._slots.items()):
            if not slot.users and (slot.until <= now or self._expired(slot.value)):
                del self._slots[key]

    def prune(self):
        with self._lock:
            self._prune_locked()

    def clear(self):
        with self._lock:
            self._slots.clear()
            self._authority = None
            self._cooldown = 0.0

    def _check_authority_locked(self, authority):
        if self._authority is not authority:
            raise MegaUpstreamError("Private metadata context changed")

    def get(self, subscription_id, loader, *, authority):
        with self._lock:
            if self._authority is not authority:
                self._slots.clear()
                self._authority = authority
                self._cooldown = 0.0
            self._check_authority_locked(authority)
            self._prune_locked()
            slot = self._slots.get(subscription_id)
            if slot is None:
                if len(self._slots) >= self._max:
                    victim = next((key for key, value in self._slots.items() if not value.users), None)
                    if victim is None:
                        raise MegaUpstreamError("Private metadata capacity unavailable")
                    del self._slots[victim]
                slot = self._slots[subscription_id] = _Slot()
            slot.users += 1
        try:
            with slot.gate:
                with self._lock:
                    self._check_authority_locked(authority)
                if slot.value is not None and self._clock() < slot.until and not self._expired(slot.value):
                    return copy.deepcopy(slot.value)
                slot.value = None
                with self._lock:
                    if self._clock() < self._cooldown:
                        raise MegaUpstreamError("Private metadata cooling down", status_code=429,
                                                retry_after_seconds=math.ceil(self._cooldown-self._clock()))
                try:
                    value = loader(subscription_id)
                except MegaClientError as error:
                    status = getattr(error, "status_code", None)
                    with self._lock:
                        if self._authority is not authority:
                            raise
                        if status == 429:
                            delay = getattr(error, "retry_after_seconds", None)
                            self._cooldown = self._clock() + (delay if delay is not None else 60)
                    raise
                # Retain only successfully parsed details, never an unavailable
                # result or a stale fallback. Known expiry is checked on hits.
                with self._lock:
                    self._check_authority_locked(authority)
                secret = getattr(value, "password", None)
                if (not self._expired(value) and secret is not None
                        and bool(secret.get_secret_value())):
                    slot.value = copy.deepcopy(value)
                    slot.until = self._clock() + self._ttl
                return copy.deepcopy(value)
        finally:
            with self._lock:
                slot.users -= 1


class PrivateMegaClient:
    def __init__(self, client, cache, authority):
        self._client, self._cache, self._authority = client, cache, authority

    def get_subscription(self, subscription_id):
        return self._cache.get(subscription_id, self._client.get_subscription,
                               authority=self._authority)

    def close(self):
        close = getattr(self._client, "close", None)
        if callable(close):
            close()


def private_mega_client(app):
    factory = app.state.mega_client_factory
    return PrivateMegaClient(factory(), app.state.private_subscription_cache, factory)
