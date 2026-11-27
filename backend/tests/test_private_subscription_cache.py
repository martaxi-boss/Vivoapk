from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from threading import Event
import unittest

import httpx
from pydantic import SecretStr

from app.clients.mega import MegaOTTClient, MegaSubscription, MegaUpstreamError
from app.services.private_subscription_cache import PrivateSubscriptionCache


class PrivateMetadataCacheTests(unittest.TestCase):
    def setUp(self):
        self.tick = 0.0
        self.base = datetime(2030, 1, 1, tzinfo=UTC)
        self.authority = object()
        self.cache = PrivateSubscriptionCache(clock=lambda: self.tick,
            utcnow=lambda: self.base+timedelta(seconds=self.tick))
        self.calls = []

    def row(self, key=1, *, expires=None):
        return MegaSubscription(type="m3u", id=key, username=f"synthetic-{key}",
            password=SecretStr("synthetic-private-password"),
            expiring_at=expires or self.base+timedelta(days=1),
            dns_link="https://origin.example.com")

    def load(self, key):
        self.calls.append(key)
        return self.row(key)

    def get(self, key=1, loader=None):
        return self.cache.get(key, loader or self.load, authority=self.authority)

    def test_reuses_details_for_five_seconds_then_refreshes(self):
        for t in (0, 1, 2, 4.99):
            self.tick=t;self.assertEqual(self.get().id,1)
        self.assertEqual(self.calls,[1]);self.tick=5;self.get();self.assertEqual(self.calls,[1,1])

    def test_known_expiry_is_not_extended_by_cache_ttl(self):
        def load(key):
            self.calls.append(key)
            return self.row(key,expires=self.base+timedelta(seconds=2))
        self.get(loader=load);self.tick=2
        expired=self.get(loader=load)
        self.assertEqual(len(self.calls),2);self.assertLessEqual(expired.expiring_at,self.base+timedelta(seconds=2))

    def test_models_and_identities_are_isolated(self):
        first=self.get(1);first.username="changed-by-caller"
        self.assertEqual(self.get(1).username,"synthetic-1");self.assertEqual(self.get(2).username,"synthetic-2")
        self.assertEqual(self.calls,[1,2])

    def test_missing_password_is_never_retained(self):
        def load(key):
            self.calls.append(key);value=self.row(key);value.password=None;return value
        self.get(loader=load);self.get(loader=load);self.assertEqual(len(self.calls),2)

    def test_refresh_error_never_uses_stale_success(self):
        self.get();self.tick=5
        def fail(_key):raise MegaUpstreamError("synthetic unavailable",status_code=503)
        with self.assertRaises(MegaUpstreamError):self.get(loader=fail)
        self.get();self.assertEqual(self.calls,[1,1])

    def test_server_retry_after_suppresses_queries_until_deadline(self):
        def limited(key):
            self.calls.append(key);raise MegaUpstreamError("synthetic quota",status_code=429,retry_after_seconds=28)
        with self.assertRaises(MegaUpstreamError):self.get(loader=limited)
        self.tick=27
        with self.assertRaises(MegaUpstreamError):self.get(2)
        self.assertEqual(self.calls,[1]);self.tick=28;self.get(2);self.assertEqual(self.calls,[1,2])

    def test_explicit_refusal_never_reuses_affected_details_or_invalidates_other_identity(self):
        self.get(1);self.tick=4;self.get(2);self.tick=5
        def deny(key):
            self.calls.append(key);raise MegaUpstreamError("synthetic refused",status_code=403)
        with self.assertRaises(MegaUpstreamError):self.get(1,deny)
        with self.assertRaises(MegaUpstreamError):self.get(1,deny)
        self.assertEqual(self.get(2).id,2);self.assertEqual(self.calls,[1,2,1,1])

    def test_new_authority_has_no_old_values_or_cooldown(self):
        self.get();self.authority=object();self.get();self.assertEqual(self.calls,[1,1])

    def test_concurrent_same_identity_has_one_upstream_query(self):
        entered,release=Event(),Event()
        def load(key):
            self.calls.append(key);entered.set();self.assertTrue(release.wait(2));return self.row(key)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(self.get,1,load);self.assertTrue(entered.wait(1));second=pool.submit(self.get,1,load)
            with self.assertRaises(TimeoutError):second.result(timeout=.05)
            release.set();self.assertEqual(first.result().id,1);self.assertEqual(second.result().id,1)
        self.assertEqual(self.calls,[1])

    def test_other_identity_does_not_wait_for_slow_lookup(self):
        entered,release=Event(),Event()
        def slow(key):
            entered.set();self.assertTrue(release.wait(2));return self.row(key)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(self.get,1,slow);self.assertTrue(entered.wait(1))
            try:self.assertEqual(pool.submit(self.get,2).result(timeout=.5).id,2)
            finally:release.set()
            first.result()

    def test_bounds_pruning_and_shutdown_cleanup(self):
        self.cache=PrivateSubscriptionCache(max_entries=2,clock=lambda:self.tick,utcnow=lambda:self.base)
        for key in (1,2,3):self.get(key)
        self.assertLessEqual(len(self.cache._slots),2)
        self.tick=5;self.cache.prune();self.assertEqual(len(self.cache._slots),0)
        self.get();self.cache.clear();self.assertEqual(len(self.cache._slots),0)

    def test_real_http_parser_preserves_numeric_retry_after(self):
        def respond(_request):return httpx.Response(429,headers={"Retry-After":"28"})
        with MegaOTTClient(base_url="https://mega.example/api",token="synthetic-key",transport=httpx.MockTransport(respond)) as client:
            with self.assertRaises(MegaUpstreamError) as caught:client.get_subscription(1)
        self.assertEqual(caught.exception.status_code,429);self.assertEqual(caught.exception.retry_after_seconds,28)

    def test_two_active_identities_stay_below_observed_sixty_query_budget(self):
        for index in range(180):
            self.tick=index/3
            self.get(1);self.get(2)
        self.assertLessEqual(len(self.calls),24)

    def test_concurrent_waiter_respects_first_request_rate_refusal(self):
        entered,release=Event(),Event()
        def limited(key):
            self.calls.append(key);entered.set();self.assertTrue(release.wait(2))
            raise MegaUpstreamError("synthetic quota",status_code=429,retry_after_seconds=28)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(self.get,1,limited);self.assertTrue(entered.wait(1));second=pool.submit(self.get,1,limited)
            with self.assertRaises(TimeoutError):second.result(timeout=.05)
            release.set()
            for future in (first,second):
                with self.assertRaises(MegaUpstreamError):future.result()
        self.assertEqual(self.calls,[1])

    def test_real_http_parser_preserves_http_date_retry_after(self):
        retry=format_datetime(datetime.now(UTC)+timedelta(seconds=30),usegmt=True)
        def respond(_request):return httpx.Response(429,headers={"Retry-After":retry})
        with MegaOTTClient(base_url="https://mega.example/api",token="synthetic-key",transport=httpx.MockTransport(respond)) as client:
            with self.assertRaises(MegaUpstreamError) as caught:client.get_subscription(1)
        self.assertTrue(20<=caught.exception.retry_after_seconds<=30)


if __name__=="__main__":unittest.main()
