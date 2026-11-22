"""Select the original Mega OTT origin, never reconstruct a customer's DNS.

A displayed domain-menu option (e.g. zvpnm.com or beuaya.com) is only
a base available when creating a subscription in the provider's dashboard.
The authenticated subscription's complete dns_link fields are authoritative.
"""
from app.models import SubscriptionMapping
from app.schemas import ClientPlatform, NATIVE_TV_PLATFORMS
from app.url_safety import UnsafeOutboundURL, validate_dns_link


class ProviderDNSUnavailable(ValueError):
    """Selected Mega OTT URL is absent or cannot be used safely."""


def select_provider_dns(mapping: SubscriptionMapping, platform: ClientPlatform) -> str:
    """Return the exact provider-supplied URL for the app's fixed platform.

    Generic clients use dns_link, including any provider-approved base domain.
    Native Samsung Tizen and LG webOS clients use the independent TV field.
    There is never a fallback or guessed subdomain.
    """
    raw = (
        mapping.dns_link_samsung_lg
        if platform in NATIVE_TV_PLATFORMS
        else mapping.dns_link
    )
    if not raw or not raw.strip():
        raise ProviderDNSUnavailable("Missing authoritative DNS for platform")
    try:
        validate_dns_link(raw)
    except (UnsafeOutboundURL, ValueError) as exc:
        raise ProviderDNSUnavailable("Invalid authoritative DNS for platform") from exc
    return raw
