import unittest
from monitor import article_ids, parse_comment_response, strip_tags, topic_allowed


class MonitorTests(unittest.TestCase):
    def test_article_id(self):
        self.assertEqual(article_ids('https://n.news.naver.com/mnews/article/001/0012345678?sid=101'), ('001', '0012345678'))
        self.assertIsNone(article_ids('https://example.com/article/001/0012345678'))

    def test_comment_jsonp(self):
        self.assertEqual(parse_comment_response('_callback({"success":true,"result":{"count":{"comment":101}}});'), 101)
        self.assertEqual(parse_comment_response('{"success":true,"result":{"count":{"comment":0}}}'), 0)

    def test_topic_filter(self):
        c = {'include_terms': ['금융', '은행'], 'exclude_terms': ['아이돌']}
        self.assertTrue(topic_allowed('은행 대출 증가', '', c))
        self.assertFalse(topic_allowed('아이돌 은행 광고', '', c))

    def test_html_entities(self):
        self.assertEqual(strip_tags('금융 &amp; <b>은행</b>'), '금융 & 은행')


if __name__ == '__main__':
    unittest.main()
