from copy import deepcopy
import io
import json
import socket
import ssl
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

from catalog_library import douban_source as douban


def movie(**overrides):
    return {"@context": "https://schema.org", "@type": "Movie", "url": "https://movie.douban.com/subject/1292052/",
            "name": "肖申克的救赎", "description": "用于本地测试的简介", "alternateName": ["The Shawshank Redemption"],
            "aggregateRating": {"@type": "AggregateRating", "ratingValue": "9.7", "bestRating": "10", "ratingCount": "1,234,567"},
            "actor": [{"@type": "Person", "name": "蒂姆·罗宾斯", "url": "https://movie.douban.com/celebrity/1054521/"}],
            "director": [{"@type": "Person", "name": "弗兰克", "url": "https://movie.douban.com/personage/123/"}],
            **overrides}


def html(value=None, *, canonical="https://movie.douban.com/subject/1292052/", extra="", title="肖申克的救赎 (豆瓣)"):
    scripts = '' if value is False else '<script type="application/ld+json">' + json.dumps(value if value is not None else movie(), ensure_ascii=False) + '</script>'
    link = '<link rel="canonical" href="' + canonical + '">' if canonical else ''
    return '<!doctype html><html><head><title>' + title + '</title>' + link + scripts + '</head><body>' + extra + '</body></html>'


class FakeResponse(io.BytesIO):
    def __init__(self, body=None, *, headers=None, status=200, url=None):
        super().__init__(body.encode('utf-8') if isinstance(body, str) else body if body is not None else html().encode('utf-8'))
        self.headers = headers or {"Content-Type": "text/html; charset=utf-8"}
        self.status = status
        self.url = url or douban.source_url(1292052)

    def geturl(self):
        return self.url


class DoubanPageTest(unittest.TestCase):
    def test_jsonld_graph_uses_only_matching_movie_not_recommendations(self):
        payload = {"@graph": [movie(url="https://movie.douban.com/subject/111/", name="推荐影片"), movie()]}
        result = douban.parse_html(html(payload), 1292052)
        self.assertEqual(result['subject']['name'], '肖申克的救赎')
        payload = {"@type": "ItemList", "itemListElement": [movie()]}
        with self.assertRaises(ValueError):
            douban.parse_html(html(payload), 1292052)

    def test_relative_jsonld_links_are_same_origin_and_every_identity_must_match(self):
        for fields in ({'url': '/subject/1292052/'}, {'url': '/subject/1292052/', '@id': douban.source_url(1292052)}):
            self.assertIn('metadata.ratings.douban', douban.parse_html(html(movie(**fields)), 1292052)['candidates'])
        for identity in ('/subject/111/', 'https://evil.test/subject/1292052/', '//movie.douban.com/subject/1292052/',
                         '/subject/1292052/?key=private', None, {'id': '1292052'}):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                douban.parse_html(html(movie(**{'@id': identity})), 1292052)

    def test_canonical_and_jsonld_subject_identity_must_not_conflict(self):
        for content in (html(canonical='https://movie.douban.com/subject/111/'), html(movie(url='https://movie.douban.com/subject/111/')),
                        html([movie(), movie(name='conflict')]), html(False, canonical=''), html(movie(), canonical='https://evil.test/subject/1292052/')):
            with self.subTest(content=content[:80]), self.assertRaises(ValueError):
                douban.parse_html(content, 1292052)

    def test_explicit_jsonld_id_can_prove_identity_without_canonical(self):
        data = movie()
        data['@id'] = data.pop('url')
        result = douban.parse_html(html(data, canonical=''), 1292052)
        self.assertIn('metadata.ratings.douban', result['candidates'])

    def test_anonymous_head_movie_requires_exact_canonical_and_body_cannot_claim_it(self):
        data = movie(); data.pop('url')
        self.assertIn('metadata.ratings.douban', douban.parse_html(html(data), 1292052)['candidates'])
        with self.assertRaises(ValueError):
            douban.parse_html(html(data, canonical=''), 1292052)
        with self.assertRaises(ValueError):
            douban.parse_html(html(False, extra='<script type="application/ld+json">' + json.dumps(data) + '</script>'), 1292052)

    def test_missing_rating_does_not_become_zero_or_clear_other_fields(self):
        result = douban.parse_html(html(movie(aggregateRating=None)), 1292052)
        self.assertNotIn('metadata.ratings.douban', result['candidates'])
        self.assertEqual(result['subject']['name'], '肖申克的救赎')
        self.assertNotIn('credits', result['raw'])
        self.assertTrue(any('评分' in warning for warning in result['warnings']))

    def test_rating_is_bounded_finite_exact_10_scale_and_requires_vote_count(self):
        for changed in ({'ratingValue': True}, {'ratingValue': 0}, {'ratingValue': '0'}, {'ratingValue': float('inf')}, {'ratingValue': 'NaN'}, {'ratingValue': 10.1},
                        {'ratingValue': -1}, {'bestRating': 5}, {'bestRating': True}, {'ratingCount': '1,23'},
                        {'ratingCount': True}, {'ratingCount': -1}, {'ratingCount': 1.5}, {'ratingCount': None},
                        {'ratingCount': '9' * 5000}):
            rating = {**movie()['aggregateRating'], **changed}
            with self.subTest(changed=changed):
                self.assertIsNone(douban._rating(rating, 1292052, '2026-10-06T00:00:00Z'))

    def test_rating_uses_shared_validator_and_invalid_timestamp_is_not_imported(self):
        with patch.object(douban, 'validate_rating', wraps=douban.validate_rating) as validate:
            result = douban.parse_html(html(), 1292052, fetched_at='2026-10-06T00:00:00Z')
        validate.assert_called_once()
        self.assertEqual(validate.call_args.args[0], 'douban')
        self.assertEqual(result['candidates']['metadata.ratings.douban']['score'], 9.7)
        self.assertIsNone(douban._rating(movie()['aggregateRating'], 1292052, 'now'))

    def test_security_login_captcha_and_sec_redirect_pages_stop_without_candidates(self):
        values = [html(title='豆瓣 - 安全验证'), html(extra='<form action="https://accounts.douban.com/login"><input name="password"></form>'),
                  html(extra='<input name="captcha-solution">'), html(extra='<meta http-equiv="refresh" content="0;url=https://sec.douban.com/">'),
                  html(extra='<script>window.location = "https://sec.douban.com/";</script>')]
        for content in values:
            with self.subTest(content=content[-150:]), self.assertRaisesRegex(ValueError, '停止|验证'):
                douban.parse_html(content, 1292052)

    def test_normal_login_navigation_is_not_an_authentication_challenge(self):
        result = douban.parse_html(html(extra='<a href="https://accounts.douban.com/passport/login">登录</a>'), 1292052)
        self.assertIn('metadata.ratings.douban', result['candidates'])

    def test_valueless_irrelevant_html_attributes_do_not_crash_primary_parse(self):
        extra = '<link rel><meta name content><form action><input name></form><script type></script>'
        result = douban.parse_html(html(extra=extra), 1292052)
        self.assertIn('metadata.ratings.douban', result['candidates'])

    def test_script_and_html_limits_fail_before_import(self):
        with self.assertRaisesRegex(ValueError, '大小'):
            douban.parse_html('x' * (douban.MAX_RESPONSE_BYTES + 1), 1292052)
        with self.assertRaisesRegex(ValueError, '安全'):
            douban.parse_html(html(False, extra='<script type="application/ld+json">' + ' ' * (douban.MAX_JSONLD_BYTES + 1) + '</script>'), 1292052)
        with self.assertRaisesRegex(ValueError, '结构'):
            douban.parse_html('<div>' * 132, 1292052)


class DoubanNetworkTest(unittest.TestCase):
    def test_fetch_is_one_fixed_host_get_without_cookies_images_or_person_requests(self):
        with patch.object(douban, 'build_opener') as build:
            build.return_value.open.return_value = FakeResponse()
            result = douban.fetch_snapshot(1292052)
        build.return_value.open.assert_called_once()
        request = build.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://movie.douban.com/subject/1292052/')
        self.assertEqual(request.get_method(), 'GET')
        self.assertIn('Nimda', request.get_header('User-agent'))
        self.assertNotIn('Mozilla', request.get_header('User-agent'))
        self.assertIsNone(request.get_header('Cookie'))
        self.assertIsNone(request.get_header('Authorization'))
        self.assertIsInstance(build.call_args.args[0], douban._NoRedirects)
        self.assertNotIn('credits', result['raw'])

    def test_numeric_ids_and_standard_movie_subject_links_remain_compatible(self):
        with patch.object(douban, '_request_html', return_value=html()) as request:
            for query in ('1292052', 'https://movie.douban.com/subject/1292052/'):
                self.assertEqual(douban.search(query)['results'][0]['id'], 1292052)
        self.assertEqual(request.call_count, 2)
        bad = ['https://api.douban.com/v2/movie/subject/1292052', 'http://movie.douban.com/subject/1292052/',
               'https://www.douban.com/subject/1292052/', 'https://movie.douban.com:443/subject/1292052/',
               'https://movie.douban.com@evil.test/subject/1292052/', '//movie.douban.com/subject/1292052/',
               'https://movie.douban.com/subject/1292052/?api_key=secret', 'https://movie.douban.com/subject/1292052/#people',
               'https://movie.douban.com/subject/../1292052/', 'https://sec.douban.com/', '0', True]
        with patch.object(douban, '_request_html') as request:
            for query in bad:
                with self.subTest(query=query), self.assertRaises(ValueError):
                    douban.search(query)
            request.assert_not_called()

    def test_cover_only_and_pagination_do_not_start_network(self):
        with patch.object(douban, '_request_html') as request:
            with self.assertRaisesRegex(ValueError, '封面'):
                douban.fetch_snapshot(1292052, cover_only=True)
            for offset in (1, -1, True):
                with self.assertRaises(ValueError):
                    douban.search('1292052', offset=offset)
            request.assert_not_called()

    def test_redirects_http_access_controls_stop_once_without_retry(self):
        with self.assertRaisesRegex(ValueError, '重定向'):
            douban._NoRedirects().redirect_request(None, None, 302, '', {}, 'https://sec.douban.com/')
        for code in (401, 403, 429, 404):
            with self.subTest(code=code), patch.object(douban, 'build_opener') as build:
                build.return_value.open.side_effect = HTTPError(douban.source_url(1292052), code, 'remote body secret', {}, None)
                with self.assertRaisesRegex(ValueError, f'HTTP {code}') as error:
                    douban.fetch_snapshot(1292052)
                self.assertNotIn('remote body secret', str(error.exception))
                build.return_value.open.assert_called_once()

    def test_response_size_type_encoding_redirect_and_truncation_are_rejected(self):
        responses = [FakeResponse(headers={'Content-Length': str(douban.MAX_RESPONSE_BYTES + 1)}),
                     FakeResponse(headers={'Content-Length': 'unknown'}), FakeResponse(headers={'Content-Length': '9' * 5000}),
                     FakeResponse(headers={'Content-Length': '1000000'}),
                     FakeResponse(body=b'x' * (douban.MAX_RESPONSE_BYTES + 1)), FakeResponse(body=b'\xff'),
                     FakeResponse(headers={'Content-Type': 'application/json'}), FakeResponse(headers={'Content-Encoding': 'gzip'}),
                     FakeResponse(url='https://sec.douban.com/'), FakeResponse(status=403)]
        for response in responses:
            with self.subTest(headers=response.headers), patch.object(douban, 'build_opener') as build:
                build.return_value.open.return_value = response
                with self.assertRaises(ValueError):
                    douban._request_html(1292052)

    def test_network_failures_are_sanitized_and_expired_budget_never_opens(self):
        for error in (URLError('private proxy credentials'), URLError(socket.timeout()), ssl.SSLError('private tls data')):
            with self.subTest(error=type(error)), patch.object(douban, 'build_opener') as build:
                build.return_value.open.side_effect = error
                with self.assertRaises(ValueError) as caught:
                    douban._request_html(1292052)
                self.assertNotIn('private', str(caught.exception))
        with patch.object(douban, 'build_opener') as build, self.assertRaisesRegex(ValueError, '总时限'):
            douban._request_html(1292052, deadline=0)
        build.return_value.open.assert_not_called()


if __name__ == '__main__':
    unittest.main()
