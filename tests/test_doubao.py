import base64
import json
import os
import threading
import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import patch

import doubao


class FakeResponse:
    def __init__(self, value, *, raw=False):
        self.body = value if raw else json.dumps(value).encode('utf-8')

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, size=-1):
        return self.body if size < 0 else self.body[:size]


class DoubaoImageTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            'ARK_API_KEY': 'test-secret-key',
            'ARK_IMAGE_MODEL': 'doubao-image-test',
        }, clear=False)
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        if doubao.GENERATION_LOCK.locked():
            try:
                doubao.GENERATION_LOCK.release()
            except RuntimeError:
                pass

    @staticmethod
    def facts():
        return {
            'category': '指令与代办事项',
            'content': '这是虚构的社区手工活动通知。',
            'event_time': '2099-10-14T15:00',
            'location': '一楼活动室',
        }

    @staticmethod
    def image_response(data=b'\xff\xd8\xff\xe0jpeg'):
        return {
            'data': [{
                'b64_json': base64.b64encode(data).decode('ascii'),
            }],
        }

    def test_capability_status_reports_text_and_image_without_leaking_key(self):
        result = doubao.capability_status()
        self.assertEqual(result, {
            'image_ready': True,
            'image_model': 'doubao-image-test',
            'text_ready': True,
            'text_model': doubao.DEFAULT_TEXT_MODEL,
        })
        serialized = json.dumps(result)
        self.assertNotIn('test-secret-key', serialized)

    def test_default_image_model_matches_current_official_flash_id(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                doubao.DEFAULT_IMAGE_MODEL,
                'doubao-seedream-5-0-flash-260915',
            )
            status = doubao.capability_status()
            self.assertEqual(status['image_model'], doubao.DEFAULT_IMAGE_MODEL)
            self.assertFalse(status['image_ready'])

    def test_generates_one_watermarked_jpeg_from_staff_facts(self):
        captured = {}

        def opener(request, timeout):
            captured['url'] = request.full_url
            captured['headers'] = dict(request.header_items())
            captured['payload'] = json.loads(request.data.decode('utf-8'))
            captured['timeout'] = timeout
            return FakeResponse(self.image_response())

        result = doubao.generate_image(self.facts(), opener=opener)
        self.assertEqual(result['filename'], 'doubao-generated.jpg')
        self.assertEqual(result['mime_type'], 'image/jpeg')
        self.assertTrue(base64.b64decode(result['data_base64']).startswith(b'\xff\xd8\xff'))
        self.assertEqual(captured['url'], f'{doubao.ARK_BASE_URL}/images/generations')
        self.assertEqual(captured['timeout'], 90)
        self.assertEqual(captured['payload']['model'], 'doubao-image-test')
        self.assertEqual(captured['payload']['size'], '1K')
        self.assertEqual(captured['payload']['response_format'], 'b64_json')
        self.assertEqual(captured['payload']['output_format'], 'jpeg')
        self.assertTrue(captured['payload']['watermark'])
        self.assertIn('虚构的社区手工活动', captured['payload']['prompt'])
        self.assertIn('不要生成任何文字', captured['payload']['prompt'])
        self.assertNotIn('test-secret-key', json.dumps(captured['payload']))
        self.assertEqual(captured['headers']['Authorization'], 'Bearer test-secret-key')

    def test_rejects_missing_key_or_invalid_model(self):
        for environment in (
            {'ARK_API_KEY': '', 'ARK_IMAGE_MODEL': 'doubao-image-test'},
            {'ARK_API_KEY': 'key', 'ARK_IMAGE_MODEL': 'bad model id'},
        ):
            with patch.dict(os.environ, environment, clear=False):
                with self.assertRaises(doubao.DoubaoNotConfigured):
                    doubao.generate_image(self.facts(), opener=lambda *_args, **_kwargs: None)

    def test_rejects_invalid_json_and_oversized_provider_response(self):
        with self.assertRaises(doubao.DoubaoError) as invalid:
            doubao.generate_image(
                self.facts(),
                opener=lambda *_args, **_kwargs: FakeResponse(b'not-json', raw=True),
            )
        self.assertEqual(invalid.exception.code, 'invalid_response')

        oversized = b'x' * (doubao.MAX_PROVIDER_RESPONSE_BYTES + 1)
        with self.assertRaises(doubao.DoubaoError) as too_large:
            doubao.generate_image(
                self.facts(),
                opener=lambda *_args, **_kwargs: FakeResponse(oversized, raw=True),
            )
        self.assertEqual(too_large.exception.code, 'response_too_large')

    def test_rejects_invalid_base64_non_jpeg_and_oversized_image(self):
        responses = (
            ({'data': [{'b64_json': '***'}]}, 'invalid_image'),
            (self.image_response(b'not-a-jpeg'), 'invalid_image'),
            (
                self.image_response(
                    b'\xff\xd8\xff' + b'x' * doubao.MAX_GENERATED_IMAGE_BYTES
                ),
                'image_too_large',
            ),
        )
        for response, expected_code in responses:
            with self.subTest(expected_code=expected_code):
                with self.assertRaises(doubao.DoubaoError) as raised:
                    doubao.generate_image(
                        self.facts(),
                        opener=lambda *_args, value=response, **_kwargs: FakeResponse(value),
                    )
                self.assertEqual(raised.exception.code, expected_code)

    def test_maps_provider_and_network_errors_without_leaking_secret(self):
        expected = {
            400: 'request_rejected',
            401: 'authentication_failed',
            403: 'authentication_failed',
            429: 'rate_limited',
            500: 'provider_unavailable',
        }
        for status, code in expected.items():
            with self.subTest(status=status):
                def failing_opener(request, timeout, current=status):
                    raise HTTPError(request.full_url, current, 'secret upstream body', None, None)

                with self.assertRaises(doubao.DoubaoError) as raised:
                    doubao.generate_image(self.facts(), opener=failing_opener)
                self.assertEqual(raised.exception.code, code)
                self.assertNotIn('test-secret-key', str(raised.exception))
                self.assertNotIn('upstream', str(raised.exception))

        with self.assertRaises(doubao.DoubaoError) as network:
            doubao.generate_image(
                self.facts(),
                opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError('secret host')),
            )
        self.assertEqual(network.exception.code, 'network_error')
        self.assertNotIn('secret host', str(network.exception))

    def test_rejects_concurrent_generation_without_calling_transport(self):
        transport_called = threading.Event()
        doubao.GENERATION_LOCK.acquire()
        try:
            with self.assertRaises(doubao.DoubaoError) as raised:
                doubao.generate_image(
                    self.facts(),
                    opener=lambda *_args, **_kwargs: transport_called.set(),
                )
        finally:
            doubao.GENERATION_LOCK.release()
        self.assertEqual(raised.exception.code, 'busy')
        self.assertFalse(transport_called.is_set())

    def test_generate_chat_reply_sends_persona_and_returns_reply(self):
        captured = {}

        def opener(request, timeout):
            captured['url'] = request.full_url
            captured['headers'] = dict(request.header_items())
            captured['payload'] = json.loads(request.data.decode('utf-8'))
            captured['timeout'] = timeout
            return FakeResponse({'choices': [{'message': {'content': '我是知应，社区里帮助您听明白事情的助手。'}}]})

        reply = doubao.generate_chat_reply('你是谁？', opener=opener)
        self.assertEqual(reply, '我是知应，社区里帮助您听明白事情的助手。')
        self.assertEqual(captured['url'], f'{doubao.ARK_BASE_URL}/chat/completions')
        self.assertEqual(captured['timeout'], 60)
        self.assertEqual(captured['payload']['model'], doubao.DEFAULT_TEXT_MODEL)
        self.assertEqual(captured['payload']['thinking'], {'type': 'disabled'})
        self.assertEqual(captured['payload']['messages'][0]['role'], 'system')
        self.assertIn('知应', captured['payload']['messages'][0]['content'])
        self.assertIn('不是豆包', captured['payload']['messages'][0]['content'])
        self.assertEqual(captured['payload']['messages'][1], {'role': 'user', 'content': '你是谁？'})
        self.assertNotIn('test-secret-key', json.dumps(captured['payload']))
        self.assertEqual(captured['headers']['Authorization'], 'Bearer test-secret-key')

    def test_generate_chat_reply_rejects_unconfigured_and_bad_replies(self):
        with patch.dict(os.environ, {'ARK_API_KEY': 'key', 'ARK_TEXT_MODEL': 'bad model id'}, clear=False):
            with self.assertRaises(doubao.DoubaoNotConfigured):
                doubao.generate_chat_reply('你好', opener=lambda *_args, **_kwargs: None)

        with self.assertRaises(doubao.DoubaoError) as invalid:
            doubao.generate_chat_reply('你好', opener=lambda *_args, **_kwargs: FakeResponse({'choices': []}))
        self.assertEqual(invalid.exception.code, 'invalid_response')

        with self.assertRaises(doubao.DoubaoError) as empty:
            doubao.generate_chat_reply('你好', opener=lambda *_args, **_kwargs: FakeResponse({'choices': [{'message': {'content': '   '}}]}))
        self.assertEqual(empty.exception.code, 'empty_reply')

    def test_generate_chat_reply_truncates_long_reply_and_rejects_empty_request(self):
        long_reply = '知' * 4000
        reply = doubao.generate_chat_reply('你好', opener=lambda *_args, **_kwargs: FakeResponse({'choices': [{'message': {'content': long_reply}}]}))
        self.assertEqual(len(reply), doubao.MAX_REPLY_CHARS)
        self.assertTrue(reply.endswith('知'))

        with self.assertRaises(doubao.DoubaoError) as empty:
            doubao.generate_chat_reply('   ', opener=lambda *_args, **_kwargs: None)
        self.assertEqual(empty.exception.code, 'empty_request')

        with self.assertRaises(doubao.DoubaoError) as too_long:
            doubao.generate_chat_reply('长' * 2001, opener=lambda *_args, **_kwargs: None)
        self.assertEqual(too_long.exception.code, 'request_too_long')

    def test_generate_chat_reply_maps_provider_errors_without_leaking_secret(self):
        def failing(request, timeout):
            raise HTTPError(request.full_url, 401, 'secret upstream body', None, None)

        with self.assertRaises(doubao.DoubaoError) as raised:
            doubao.generate_chat_reply('你好', opener=failing)
        self.assertEqual(raised.exception.code, 'authentication_failed')
        self.assertNotIn('test-secret-key', str(raised.exception))
        self.assertNotIn('upstream', str(raised.exception))


if __name__ == '__main__':
    unittest.main()
