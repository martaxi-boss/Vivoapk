"""Per-session, fail-closed catalogue authorization.

A signed, short-lived VivoAPK session identifies exactly one subscription mapping.
Every catalogue request rechecks Mega OTT ownership, entitlement and Xtream status.
Only sanitized display metadata is returned: never stream URLs or provider secrets.
"""
from __future__ import annotations
import re
from collections.abc import Callable
from datetime import UTC, datetime
from types import SimpleNamespace

import jwt
from sqlalchemy.orm import Session

from app.auth_rate_limit import charge_auth_attempt
from app.clients.catalog import CatalogUnavailable, XtreamCatalogClient
from app.clients.mega import MegaClientError, MegaOTTClient
from app.clients.xtream import XtreamClient, XtreamError
from app.config import Settings
from app.models import SubscriptionMapping
from app.schemas import (
    CatalogKind,
    CatalogListResponse,
    ClientPlatform,
    EpgResponse,
    SeriesDetailResponse,
)
from app.services.provider_dns import ProviderDNSUnavailable, select_provider_dns

_SUB = re.compile(r"^mapping:([1-9][0-9]{0,18})$")


class CatalogDenied(RuntimeError):
    """No valid and currently authorized subscription corresponds to this request."""


def mapping_id_for_token(token: str, settings: Settings) -> int:
    if not token or len(token) > 4096:
        raise CatalogDenied("Invalid session")
    try:
        claims = jwt.decode(
            token,
            settings.session_signing_key.get_secret_value(),
            algorithms=["HS256"],
            issuer="vivoapk",
            options={"require": ["sub", "iat", "exp", "iss"]},
            leeway=0,
        )
    except jwt.InvalidTokenError:
        raise CatalogDenied("Invalid session") from None
    match = _SUB.fullmatch(str(claims.get("sub", "")))
    if match is None:
        raise CatalogDenied("Invalid session")
    return int(match.group(1))


def _expired(value: datetime | None) -> bool:
    if value is None:
        return False
    if value.tzinfo is None or value.utcoffset() is None:
        # The legacy local mapping may have been imported as naive UTC.
        value = value.replace(tzinfo=UTC)
    return value <= datetime.now(UTC)


class CatalogService:
    def __init__(
        self,
        db: Session,
        settings: Settings,
        *,
        mega_client_factory: Callable[[], MegaOTTClient],
        xtream_client_factory: Callable[[], XtreamClient],
        catalog_client_factory: Callable[[], XtreamCatalogClient],
    ) -> None:
        self._db = db
        self._settings = settings
        self._mega_client_factory = mega_client_factory
        self._xtream_client_factory = xtream_client_factory
        self._catalog_client_factory = catalog_client_factory

    def _authorize(self, *, token: str, platform: ClientPlatform) -> tuple[str, str, str]:
        mapping_id = mapping_id_for_token(token, self._settings)
        mapping = self._db.get(SubscriptionMapping, mapping_id)
        if mapping is None or _expired(mapping.expiring_at):
            raise CatalogDenied("Subscription unavailable")

        # Existing, cross-worker admission budget; no passwords or usernames logged.
        charge_auth_attempt(self._db, self._settings, mapping.username, purpose="catalog")

        mega = self._mega_client_factory()
        try:
            try:
                subscription = mega.get_subscription(mapping.mega_subscription_id)
            except MegaClientError:
                raise CatalogUnavailable("Mega account lookup failed") from None
        finally:
            close = getattr(mega, "close", None)
            if callable(close):
                close()

        if (subscription.id != mapping.mega_subscription_id
                or subscription.username != mapping.username
                or _expired(subscription.expiring_at)):
            raise CatalogDenied("Subscription unavailable")
        # Password is obtained from the private provider API *in memory only*,
        # not saved in Jellyfin, PostgreSQL, responses or job artifacts.
        secret = subscription.password
        password = secret.get_secret_value() if secret is not None else ""
        if not password:
            raise CatalogUnavailable("Provider cannot authorize catalogue access")
        try:
            origin = select_provider_dns(
                SimpleNamespace(
                    dns_link=subscription.dns_link,
                    dns_link_samsung_lg=subscription.dns_link_for_samsung_lg,
                ),
                platform,
            )
        except ProviderDNSUnavailable:
            raise CatalogUnavailable("Provider DNS unavailable") from None

        xtream = self._xtream_client_factory()
        try:
            try:
                status = xtream.authenticate(
                    dns_link=origin, username=mapping.username, password=password
                )
            except XtreamError:
                raise CatalogUnavailable("Provider authentication unavailable") from None
        finally:
            close = getattr(xtream, "close", None)
            if callable(close):
                close()

        if status.code != "SUCCESS" or _expired(status.account_expires_at):
            raise CatalogDenied("Subscription unavailable")

        return origin, mapping.username, password

    def list(self, *, token: str, kind: CatalogKind, platform: ClientPlatform) -> CatalogListResponse:
        origin, username, password = self._authorize(token=token, platform=platform)
        catalogue = self._catalog_client_factory()
        categories, items = catalogue.list(
            dns_link=origin, username=username, password=password, kind=kind
        )
        return CatalogListResponse(kind=kind, categories=categories, items=items)

    def categories(
        self, *, token: str, kind: CatalogKind, platform: ClientPlatform
    ) -> CatalogListResponse:
        origin, username, password = self._authorize(token=token, platform=platform)
        categories = self._catalog_client_factory().list_categories(
            dns_link=origin, username=username, password=password, kind=kind,
        )
        return CatalogListResponse(kind=kind, categories=categories, items=[])

    def category_items(
        self, *, token: str, kind: CatalogKind,
        category_id: str, platform: ClientPlatform
    ) -> CatalogListResponse:
        origin, username, password = self._authorize(token=token, platform=platform)
        items = self._authorized_category_items(
            origin=origin, username=username, password=password,
            kind=kind, category_id=category_id,
        )
        return CatalogListResponse(kind=kind, categories=[], items=items)

    def _authorized_category_items(
        self, *, origin: str, username: str, password: str,
        kind: CatalogKind, category_id: str
    ) -> list[dict[str, str | None]]:
        catalogue = self._catalog_client_factory()
        categories = catalogue.list_categories(
            dns_link=origin, username=username, password=password, kind=kind,
        )
        # Check both the owner's current category and each stream's category.
        if not any(category["id"] == category_id for category in categories):
            raise CatalogDenied("Category not authorized for subscription")
        items = catalogue.list_category_items(
            dns_link=origin, username=username, password=password,
            kind=kind, category_id=category_id,
        )
        return [entry for entry in items if entry["category_id"] == category_id]

    def live_preview(self, *, token: str, platform: ClientPlatform) -> CatalogListResponse:
        origin, username, password = self._authorize(token=token, platform=platform)
        catalogue = self._catalog_client_factory()
        categories, items = catalogue.preview_live(
            dns_link=origin, username=username, password=password,
        )
        return CatalogListResponse(
            kind=CatalogKind.LIVE, categories=categories, items=items,
        )

    def series_detail(
        self, *, token: str, platform: ClientPlatform,
        category_id: str, series_id: str
    ) -> SeriesDetailResponse:
        origin, username, password = self._authorize(token=token, platform=platform)
        entries = self._authorized_category_items(
            origin=origin, username=username, password=password,
            kind=CatalogKind.SERIES, category_id=category_id,
        )
        if not any(entry["id"] == series_id for entry in entries):
            raise CatalogDenied("Series not available for this subscription")
        payload = self._catalog_client_factory().series_detail(
            dns_link=origin, username=username, password=password, series_id=series_id
        )
        return SeriesDetailResponse.model_validate(payload)

    def short_epg(
        self, *, token: str, platform: ClientPlatform,
        category_id: str, stream_id: str, limit: int
    ) -> EpgResponse:
        origin, username, password = self._authorize(token=token, platform=platform)
        entries = self._authorized_category_items(
            origin=origin, username=username, password=password,
            kind=CatalogKind.LIVE, category_id=category_id,
        )
        if not any(entry["id"] == stream_id for entry in entries):
            raise CatalogDenied("Channel not available for this subscription")
        payload = self._catalog_client_factory().short_epg(
            dns_link=origin, username=username, password=password,
            stream_id=stream_id, limit=limit
        )
        return EpgResponse.model_validate(payload)

