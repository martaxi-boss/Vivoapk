"""Synthetic-only redirect policy tests. No private line or network calls."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import unittest

from app.services.redirect_preflight import (
    AuthorizedMediaGrant, RedirectDenied, preflight_redirect,
)


class RedirectPreflightTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 25, tzinfo=UTC)
        self.grant = AuthorizedMediaGrant(
            account_id="synthetic-a",
            asset_id="demo",
            expires_at=self.now + timedelta(seconds=30),
            active=True,
            url="https://media.example.org/media/demo.mp4",
        )

    def decide(self, grant=None, account="synthetic-a", asset="demo"):
        return preflight_redirect(
            self.grant if grant is None else grant,
            authenticated_account=account,
            requested_asset=asset,
            approved_origin="https://media.example.org",
            now=self.now,
        )

    def test_mp4_and_hls_have_empty_redirect_body(self):
        for suffix in ("mp4", "m3u8"):
            with self.subTest(suffix=suffix):
                grant = replace(
                    self.grant,
                    url=f"https://media.example.org/media/demo.{suffix}",
                )
                decision = self.decide(grant)
                self.assertEqual(decision.status, 302)
                self.assertEqual(decision.body, b"")
                self.assertEqual(dict(decision.headers)["Location"], grant.url)
                self.assertEqual(dict(decision.headers)["Cache-Control"], "no-store")
                self.assertEqual(dict(decision.headers)["Referrer-Policy"], "no-referrer")
                self.assertEqual(dict(decision.headers)["Content-Length"], "0")

    def test_other_account_denied(self):
        with self.assertRaises(RedirectDenied):
            self.decide(account="synthetic-b")

    def test_empty_identity_denied(self):
        with self.assertRaises(RedirectDenied):
            self.decide(account="")

    def test_other_asset_denied(self):
        with self.assertRaises(RedirectDenied):
            self.decide(asset="other")

    def test_revoked_denied(self):
        with self.assertRaises(RedirectDenied):
            self.decide(replace(self.grant, active=False))

    def test_expired_denied(self):
        with self.assertRaises(RedirectDenied):
            self.decide(replace(self.grant, expires_at=self.now))

    def test_naive_expiry_denied(self):
        with self.assertRaises(RedirectDenied):
            self.decide(replace(self.grant, expires_at=self.now.replace(tzinfo=None)))

    def test_untrusted_insecure_or_credential_bearing_targets_denied(self):
        for url in (
            "http://media.example.org/media/demo.mp4",
            "https://other.example.org/media/demo.mp4",
            "https://media.example.org.evil.example/media/demo.mp4",
            "https://media.example.org@other.example.org/media/demo.mp4",
            "https://media.example.org:8443/media/demo.mp4",
            "https://media.example.org/media/demo.mp4?secret=synthetic",
            "https://media.example.org/media/demo.mp4#fragment",
            "https://media.example.org/media/%64emo.mp4",
            "https://media.example.org/media/other.mp4",
            "https://media.example.org/media/demo.mp4\r\nX-Test:bad",
            "https://media.example.org\\@other.example.org/media/demo.mp4",
            "https://127.0.0.1/media/demo.mp4",
        ):
            with self.subTest(url=url), self.assertRaises(RedirectDenied):
                self.decide(replace(self.grant, url=url))

    def test_errors_and_repr_do_not_reveal_target(self):
        grant = replace(self.grant, url="https://other.example.org/media/demo.mp4")
        self.assertNotIn(grant.url, repr(grant))
        self.assertNotIn(self.grant.url, repr(self.decide()))
        with self.assertRaises(RedirectDenied) as err:
            self.decide(grant)
        self.assertNotIn(grant.url, str(err.exception))


if __name__ == "__main__":
    unittest.main()
