import unittest
from contextlib import contextmanager
from unittest.mock import patch

from pydantic import SecretStr

from app.schemas import CatalogKind
from app.services.media_body import MediaReadError, open_media_body
from app.services.media_origin import AuthorizedMediaOrigin, MediaAsset


class Reply:
    def __init__(self, status=206, headers=None, data=b'abc'):
        self.status = status
        self.headers = headers if headers is not None else [
            (b'content-type', b'video/mp4'), (b'content-length', b'3'),
            (b'content-range', b'bytes 0-2/10'), (b'etag', b'"stable"')]
        self.data, self.read, self.closed = data, False, False

    def iter_stream(self):
        self.read = True
        yield self.data


def redirect(location=b'https://cdn.example.net/file', status=302):
    return Reply(status, [(b'location', location), (b'set-cookie', b'untrusted=value')])


class FiniteRedirectTests(unittest.TestCase):
    def setUp(self):
        self.responses, self.requests, self.pools = [], [], []
        self.resolutions = []
        self.on_enter = None
        self.source = AuthorizedMediaOrigin(
            MediaAsset(CatalogKind.VOD, '1', '2'), 'https://origin.example.com',
            'synthetic-user', SecretStr('synthetic-password'), 'mp4')

    def resolver(self, host, port):
        self.resolutions.append((host, port))
        return ['127.0.0.1'] if host == 'internal.example.net' else ['93.184.216.34']

    def factory(self, target):
        test = self
        if self.pools:
            self.assertTrue(self.pools[-1].closed)
            self.assertTrue(self.responses[len(self.pools) - 1].closed)
        response = self.responses[len(self.pools)]

        class Pool:
            closed = False

            @contextmanager
            def stream(self, method, url, **kw):
                test.requests.append((method, url, dict(kw['headers']), target))
                if test.on_enter:
                    test.on_enter()
                try:
                    yield response
                finally:
                    response.closed = True

            def close(self):
                self.closed = True

        pool = Pool()
        self.pools.append(pool)
        return pool

    def read(self, *, enabled=True, **kwargs):
        with open_media_body(
            self.source, range_header='bytes=0-2', max_bytes=3,
            resolver=self.resolver, pool_factory=self.factory,
            allow_provider_redirects=enabled, **kwargs,
        ) as body:
            return b''.join(body.chunks)

    def denied(self, **kwargs):
        with self.assertRaises(MediaReadError):
            self.read(**kwargs)
        self.assertTrue(all(p.closed for p in self.pools))
        self.assertFalse(any(r.read for r in self.responses))

    def test_default_still_rejects_redirect_without_reading_body(self):
        self.responses = [redirect()]
        self.denied(enabled=False)
        self.assertEqual(len(self.requests), 1)

    def test_provider_issued_range_continuation_preserves_only_owned_headers(self):
        self.responses = [redirect(), Reply()]
        self.assertEqual(self.read(if_match='"stable"'), b'abc')
        self.assertFalse(self.responses[0].read)
        self.assertEqual(len(self.resolutions), 2)
        for _, _, headers, _ in self.requests:
            self.assertEqual(headers[b'Range'], b'bytes=0-2')
            self.assertEqual(headers[b'If-Match'], b'"stable"')
            self.assertEqual(headers[b'User-Agent'], b'Vivoapk/1.0')
            self.assertFalse({k.lower() for k in headers} & {
                b'cookie', b'authorization', b'referer', b'x-vivoapk-media-key'})
        self.assertTrue(all(p.closed for p in self.pools))

    def test_two_issued_hops_allowed_each_previous_response_and_pool_closed(self):
        self.responses = [redirect(b'/next', 307), redirect(status=308), Reply()]
        self.assertEqual(self.read(), b'abc')
        self.assertEqual(len(self.requests), 3)

    def test_third_redirect_is_terminal(self):
        self.responses = [redirect(b'/one'), redirect(b'/two'), redirect(b'/three')]
        self.denied()
        self.assertEqual(len(self.requests), 3)

    def test_issued_cycle_is_terminal_before_reopening_first_url(self):
        self.responses = [redirect(b'/next'), redirect(self.source._request_url().encode())]
        self.denied()
        self.assertEqual(len(self.requests), 2)

    def test_401_and_403_never_follow_even_with_location(self):
        for status in (401, 403):
            with self.subTest(status=status):
                self.setUp()
                self.responses = [redirect(status=status)]
                self.denied()
                self.assertEqual(len(self.requests), 1)

    def test_private_dns_hop_is_rejected_before_opening_pool(self):
        self.responses = [redirect(b'https://internal.example.net/file')]
        self.denied()
        self.assertEqual(len(self.requests), 1)

    def test_invalid_issued_locations_never_open_second_request(self):
        for location in (b'http://cdn.example.net/file', b'https://user@cdn.example.net/file',
                         b'https://cdn.example.net/file#fragment', b'/one/../file'):
            with self.subTest(location=location):
                self.setUp()
                self.responses = [redirect(location)]
                self.denied(allow_authorized_http=True)
                self.assertEqual(len(self.requests), 1)

    def test_ambiguous_location_rejected(self):
        self.responses = [Reply(302, [(b'location', b'/a'), (b'Location', b'/b')])]
        self.denied()

    def test_ignored_range_never_reads_unbounded_asset(self):
        self.responses = [redirect(), Reply(200)]
        self.denied()

    def test_range_and_validator_remain_strict_after_redirect(self):
        for changed in ((b'content-range', b'bytes 1-3/10'), (b'etag', b'"changed"')):
            with self.subTest(changed=changed):
                self.setUp()
                reply = Reply()
                reply.headers = [(k, changed[1] if k == changed[0] else v) for k, v in reply.headers]
                self.responses = [redirect(), reply]
                self.denied(if_match='"stable"')

    def test_redirect_deadline_covers_entire_chain(self):
        tick = [0]
        self.responses = [redirect()]
        self.on_enter = lambda: tick.__setitem__(0, 16)
        with patch('app.services.media_body.time.monotonic', side_effect=lambda: tick[0]):
            self.denied()
        self.assertEqual(len(self.requests), 1)

    def test_http_remains_explicit_optin_without_tls_downgrade(self):
        self.source = AuthorizedMediaOrigin(self.source.asset, 'http://origin.example.com',
                                           self.source.username, self.source.password, 'mp4')
        self.responses = [redirect(b'http://cdn.example.net/file'), Reply()]
        self.denied()
        self.assertFalse(self.requests)
        self.assertEqual(self.read(allow_authorized_http=True), b'abc')

    def test_live_sources_cannot_enable_finite_continuation(self):
        self.source = AuthorizedMediaOrigin(MediaAsset(CatalogKind.LIVE, '1', '2'),
                                           self.source.origin, self.source.username,
                                           self.source.password, 'ts')
        self.responses = []
        self.denied()
        self.assertFalse(self.requests)

    def test_episode_uses_same_bounded_continuation(self):
        self.source = AuthorizedMediaOrigin(MediaAsset(CatalogKind.SERIES, '1', '2', '3'),
                                           self.source.origin, self.source.username,
                                           self.source.password, 'mkv')
        self.responses = [redirect(), Reply()]
        self.assertEqual(self.read(), b'abc')


if __name__ == '__main__':
    unittest.main()
