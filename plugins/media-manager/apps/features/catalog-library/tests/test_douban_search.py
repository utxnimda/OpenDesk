"""Official search HTML fixtures; no real network or details-page requests."""
from html import escape
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode

from catalog_library import douban_search as search, douban_source as source
from test_douban_source import FakeResponse


def tracker(target, category="1002", **extra):
    return "https://www.douban.com/link2/?" + urlencode({"url": target, "query": "fixture", "cat_id": category,
        "type": "search", "pos": "0", **extra})


def row(identity=37530010, *, label="电视剧", name="毛骨悚然的恋爱", href=None, cast="原名:오싹한 연애 / 李敏燮 / 朴恩斌 / 2026", paragraphs=()):
    href = href or tracker(f"https://movie.douban.com/subject/{identity}/")
    return ('<div class="result"><div class="pic"><a href="https://evil.test/tracker"><img src="https://evil.test/image"></a></div>'
        '<div class="content"><div class="title"><h3><span>[' + escape(label) + ']</span> <a href="' + escape(href, quote=True) + '">'
        + escape(name) + '</a></h3></div><div class="rating-info"><span class="subject-cast">' + escape(cast) + '</span></div>'
        + ''.join('<p>' + escape(value) + '</p>' for value in paragraphs) + '</div></div>')


def page(*rows, title="豆瓣搜索", extra=""):
    return '<html><head><title>' + escape(title) + '</title></head><body>' + extra + '<div class="search-result"><div class="result-list">' + ''.join(rows) + '</div></div></body></html>'


class DoubanSearchParserTest(unittest.TestCase):
    def test_real_shape_movie_and_tv_rows_preserve_disambiguation_without_retrieval(self):
        content = page(row(), row(5501711, label="电影", cast="原名:오싹한 연애 / 黄仁镐 / 孙艺珍 / 2011"))
        with patch.object(source, '_request_public_html', side_effect=AssertionError('pure parser')):
            results, warnings = search.parse_html(content)
        self.assertEqual([entry['id'] for entry in results], [37530010, 5501711])
        self.assertEqual([entry['type'] for entry in results], ['电视剧', '电影'])
        self.assertEqual([entry['media_type'] for entry in results], ['tv', 'movie'])
        self.assertEqual([entry['year'] for entry in results], ['2026', '2011'])
        self.assertEqual(results[0]['source_url'], 'https://movie.douban.com/subject/37530010/')
        self.assertIn('朴恩斌', results[0]['description'])
        self.assertEqual(results[0]['cover_url'], '')
        self.assertEqual(warnings, [])

    def test_tracking_url_is_decoded_exactly_once_and_never_follows_arbitrary_targets(self):
        bad = ['https://evil.test/subject/1/', '//movie.douban.com/subject/1/', 'http://movie.douban.com/subject/1/',
            'https://movie.douban.com/subject/1/?secret=x', 'https://movie.douban.com/subject/1/#frag',
            'https://movie.douban.com@evil.test/subject/1/', 'https://movie.douban.com/subject/../1/',
            'https%3A%2F%2Fmovie.douban.com%2Fsubject%2F1%2F', tracker('https://movie.douban.com/subject/1/')]
        for target in bad:
            with self.subTest(target=target):
                self.assertEqual(search.parse_html(page(row(href=tracker(target))))[0], [])
        for link in (tracker('https://movie.douban.com/subject/1/') + '&url=https%3A%2F%2Fevil.test',
            tracker('https://movie.douban.com/subject/1/', '1065'),
            tracker('https://movie.douban.com/subject/1/').replace('www.douban.com/link2/', 'evil.test/link2/')):
            self.assertEqual(search.parse_html(page(row(href=link)))[0], [])

    def test_unrelated_recommendations_and_duplicate_result_links_are_not_candidates(self):
        content = page(row(), row(), extra=row(666) + '<aside><a href="https://movie.douban.com/subject/777/">Other</a></aside>')
        self.assertEqual([value['id'] for value in search.parse_html(content)[0]], [37530010])
        ambiguous = row().replace('</h3>', '<a href="https://movie.douban.com/subject/7/">Other</a></h3>')
        self.assertEqual(search.parse_html(page(ambiguous))[0], [])

    def test_limit_is_twenty_and_does_not_follow_next_page(self):
        rows, warnings = search.parse_html(page(*(row(identity) for identity in range(1, 26))))
        self.assertEqual(len(rows), 20)
        self.assertTrue(any('20' in warning for warning in warnings))

    def test_empty_result_requires_recognized_container_and_explicit_empty_message(self):
        self.assertEqual(search.parse_html(page('没有找到相关结果')), ([], []))
        self.assertTrue(search.parse_html(page())[1])
        with self.assertRaisesRegex(ValueError, '结构'):
            search.parse_html('<html><title>豆瓣</title><body>No known wrapper</body></html>')

    def test_security_and_redirect_html_stop_without_partial_results(self):
        for content in (page(row(), title='豆瓣 - 安全验证'), page(row(), extra='<form action="https://accounts.douban.com/login"></form>'),
            page(row(), extra='<meta http-equiv="refresh" content="0;url=https://sec.douban.com/">'),
            page(row(), extra='<script>window.location="https://sec.douban.com/c";</script>')):
            with self.assertRaisesRegex(ValueError, '停止|验证'):
                search.parse_html(content)

    def test_response_depth_and_result_count_limits(self):
        for content in ('x' * (source.MAX_RESPONSE_BYTES + 1), '<div>' * 140,
                        page(*(row(identity) for identity in range(1, 103)))):
            with self.assertRaisesRegex(ValueError, '安全|大小'):
                search.parse_html(content)


class DoubanSearchNetworkTest(unittest.TestCase):
    def test_keyword_and_standard_search_url_use_one_same_canonical_search_get(self):
        expected = search.search_url('毛骨悚然的恋爱')
        for query in ('毛骨悚然的恋爱', expected, 'https://www.douban.com/search?cat=1002&q=毛骨悚然的恋爱'):
            with self.subTest(query=query), patch.object(source, '_request_public_html', return_value=page(row())) as fetch, \
                 patch.object(source, 'fetch_snapshot', side_effect=AssertionError('no candidate details')):
                result = source.search(query)
                self.assertEqual(result['results'][0]['id'], 37530010)
                self.assertEqual(result['search_url'], expected)
                self.assertFalse(result['unavailable'])
                fetch.assert_called_once_with(expected, deadline=None)

    def test_invalid_urls_categories_queries_and_pagination_never_start_network(self):
        bad = ['', ' ', 'x' * 201, 'q\nname', True, 'https://evil.test/search?cat=1002&q=x',
            'https://www.douban.com/search?cat=1065&q=x', 'https://www.douban.com/search?cat=1005&q=x',
            'https://www.douban.com/search?cat=1002&q=x&q=y', 'https://www.douban.com/search?cat=1002&q=',
            'https://www.douban.com/search?cat=1002&q=x&start=20', 'https://www.douban.com/search?cat=1002&q=x&api_key=private',
            'http://www.douban.com/search?cat=1002&q=x', '//www.douban.com/search?cat=1002&q=x',
            'https://www.douban.com:443/search?cat=1002&q=x', 'https://www.douban.com/search?cat=1002&q=x#fragment',
            'https://www.douban.com/search?cat=1002&q=%0a', 'https://www.douban.com/search?cat=1002&q=%zz']
        with patch.object(source, '_request_public_html') as fetch:
            for query in bad:
                with self.subTest(query=query), self.assertRaises(ValueError):
                    source.search(query)
            for offset in (1, -1, True):
                with self.assertRaises(ValueError):
                    source.search('name', offset=offset)
            with self.assertRaises(ValueError):
                source.search('name', kind='member')
            fetch.assert_not_called()

    def test_real_transport_is_fixed_search_host_identifying_agent_no_cookie_and_no_tracker_request(self):
        url = search.search_url('毛骨悚然的恋爱')
        with patch.object(source, 'build_opener') as build:
            build.return_value.open.return_value = FakeResponse(page(row()), url=url)
            result = source.search('毛骨悚然的恋爱')
        build.return_value.open.assert_called_once()
        request = build.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, url)
        self.assertEqual(request.get_method(), 'GET')
        self.assertNotIn('Mozilla', request.get_header('User-agent'))
        self.assertIsNone(request.get_header('Cookie'))
        self.assertIsNone(request.get_header('Authorization'))
        self.assertEqual(result['results'][0]['id'], 37530010)

    def test_blocked_http_or_security_page_returns_unavailable_not_false_no_results(self):
        url = search.search_url('作品')
        for error in (HTTPError(url, 403, 'private server body', {}, None), HTTPError(url, 429, 'private body', {}, None)):
            with patch.object(source, 'build_opener') as build:
                build.return_value.open.side_effect = error
                result = source.search('作品')
                build.return_value.open.assert_called_once()
            self.assertTrue(result['unavailable'])
            self.assertIsNone(result['total'])
            self.assertEqual(result['search_url'], url)
            self.assertNotIn('private', repr(result))
        with patch.object(source, '_request_public_html', return_value=page(row(), title='安全验证')) as fetch:
            result = source.search('作品')
            fetch.assert_called_once()
        self.assertEqual(result['results'], [])
        self.assertIsNone(result['total'])
        self.assertTrue(any('停止' in text for text in result['warnings']))

    def test_redirect_is_rejected_and_search_keeps_manual_official_fallback(self):
        with patch.object(source, 'build_opener') as build:
            build.return_value.open.side_effect = ValueError('豆瓣返回重定向，已停止')
            result = source.search('作品')
            build.return_value.open.assert_called_once()
        self.assertTrue(result['unavailable'])
        self.assertIn('官网', result['warnings'][0])
        self.assertEqual(result['search_url'], search.search_url('作品'))

    def test_exact_subject_security_failure_also_keeps_official_fallback_without_retry(self):
        with patch.object(source, '_request_html', side_effect=ValueError(source._BLOCKED)) as fetch:
            result = source.search('1292052')
        fetch.assert_called_once()
        self.assertTrue(result['unavailable'])
        self.assertIsNone(result['total'])
        self.assertEqual(result['source_url'], source.source_url(1292052))
        self.assertEqual(result['search_url'], search.search_url('1292052'))

    def test_shared_transport_does_not_open_unapproved_domains_trackers_api_or_person_details(self):
        bad = ['https://www.douban.com/link2/?url=https%3A%2F%2Fevil.test', 'https://www.douban.com/personage/27220007/',
            'https://movie.douban.com/celebrity/1/', 'https://api.douban.com/v2/movie/search?q=x',
            'https://www.douban.com/search?cat=1005&q=x', 'https://evil.test/subject/1/']
        with patch.object(source, 'build_opener') as build:
            for url in bad:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    source._request_public_html(url)
            build.assert_not_called()


if __name__ == '__main__':
    unittest.main()
