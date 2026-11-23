"""Private, credential-free subscription entitlement check for Jellyfin scheduled tasks.

Only an explicit provider expiry / disabled status revokes access. Transient errors
are UNKNOWN and must never cause the Jellyfin administrator or other customers to
be disabled. No provider credentials are returned or persisted here.
"""
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.mega import MegaClientError, MegaOTTClient
from app.clients.xtream import XtreamClient, XtreamError
from app.models import SubscriptionMapping
from app.schemas import EntitlementState
from app.services.provider_dns import ProviderDNSUnavailable, select_provider_dns
from app.schemas import ClientPlatform


def is_expired(value: datetime | None) -> bool:
    if value is None:
        return False
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    return value <= datetime.now(UTC)


class EntitlementService:
    def __init__(
        self,
        db: Session,
        *,
        mega_client_factory: Callable[[], MegaOTTClient],
        xtream_client_factory: Callable[[], XtreamClient],
    ) -> None:
        self._db = db
        self._mega_client_factory = mega_client_factory
        self._xtream_client_factory = xtream_client_factory

    def check(self, username: str) -> EntitlementState:
        mapping = self._db.scalar(
            select(SubscriptionMapping).where(SubscriptionMapping.username == username)
        )
        if mapping is None:
            return EntitlementState.UNAVAILABLE

        mega = self._mega_client_factory()
        try:
            try:
                subscription = mega.get_subscription(mapping.mega_subscription_id)
            except (MegaClientError, ValueError):
                return EntitlementState.UNAVAILABLE
        finally:
            close = getattr(mega, "close", None)
            if callable(close):
                close()

        # A changed username is NOT automatically rebound to another Jellyfin user.
        if (subscription.id != mapping.mega_subscription_id
                or subscription.username != mapping.username):
            return EntitlementState.DISABLED

        # The provider is authoritative after renewal; never rely on stale local expiry.
        if subscription.expiring_at != mapping.expiring_at:
            mapping.expiring_at = subscription.expiring_at
            mapping.last_synced_at = datetime.now(UTC)
            self._db.commit()
        if is_expired(subscription.expiring_at):
            return EntitlementState.EXPIRED

        # Mega detail may omit a password. Treat this as an unverifiable account;
        # do NOT revoke sessions because of a provider API schema limitation.
        secret = subscription.password
        password = secret.get_secret_value() if secret is not None else ""
        if not password:
            return EntitlementState.UNAVAILABLE
        try:
            dns = select_provider_dns(subscription, ClientPlatform.ANDROID)
        except ProviderDNSUnavailable:
            return EntitlementState.UNAVAILABLE

        xtream = self._xtream_client_factory()
        try:
            try:
                status = xtream.authenticate(
                    dns_link=dns, username=username, password=password
                )
            except XtreamError:
                return EntitlementState.UNAVAILABLE
        finally:
            close = getattr(xtream, "close", None)
            if callable(close):
                close()
        if status.code == "SUCCESS":
            return (EntitlementState.EXPIRED if is_expired(status.account_expires_at)
                    else EntitlementState.ACTIVE)
        if status.code == "EXPIRED":
            return EntitlementState.EXPIRED
        if status.code == "DISABLED":
            return EntitlementState.DISABLED
        return EntitlementState.UNAVAILABLE
