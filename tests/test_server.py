import base64
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import server


class QuietHandler(server.Handler):
    def log_message(self, format, *args):
        pass


class ServerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = server.DB
        self.db_path = Path(self.temp_dir.name) / 'app.db'
        server.DB = self.db_path
        server.reset_avatar_runtime()
        server.initialize()
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f'http://127.0.0.1:{self.httpd.server_port}'

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        server.DB = self.original_db
        self.temp_dir.cleanup()

    def request(self, method, path, payload=None):
        body = None
        headers = {}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        request = Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read().decode('utf-8'))
        except HTTPError as error:
            return error.code, json.loads(error.read().decode('utf-8'))

    def create_activity(self):
        status, result = self.request('POST', '/api/items', {
            'direction': '下达',
            'category': '指令与代办事项',
            'content': '明天下午有一场虚构的手工活动。',
            'event_time': '2099-10-14T15:00',
            'location': '一楼活动室',
            'image_key': 'activity',
            'response_mode': 'attendance',
        })
        self.assertEqual(status, 200)
        return result['id']

    def choice_payload(self, category='指令与代办事项', reminder_enabled=True):
        return {
            'direction': '下达',
            'category': category,
            'content': '这是一条虚构的知应测试内容。',
            'event_time': '2099-10-14T15:00' if reminder_enabled else '',
            'location': '一楼活动室' if category == '指令与代办事项' else '',
            'image_key': 'activity' if category == '指令与代办事项' else '',
            'reminder_enabled': reminder_enabled,
            'options': [
                {'label': '上午场', 'kind': 'accept'},
                {'label': '下午场', 'kind': 'accept'},
                {'label': '不参加', 'kind': 'reject'},
            ],
        }

    def runtime_payload(self, **changes):
        payload = {
            'session_id': 'test-session',
            'page_state': 'elder_active',
            'connection_state': 'connected',
            'render_state': 'rendering',
            'speech_state': 'idle',
            'asr_state': 'listening',
            'current_item_id': None,
            'last_action': 'SDK 已报告渲染，人物待确认',
            'container_width': 1280,
            'container_height': 720,
            'canvas_count': 1,
            'canvas_layout': 'Canvas1 405×720 @ 438,0 缓冲区1080×1920 absolute block/visible/o1 z100',
            'render_node_count': 2,
            'render_layout': '节点1 div#avatar-bg-container 1280×720 @ 0,0 absolute block/visible/o0 z0；节点2 canvas 405×720 @ 438,0 absolute block/visible/o1 z100',
        }
        return {**payload, **changes}

    def image_upload(self, mime_type='image/png', data=None, filename='notice.png'):
        if data is None:
            data = base64.b64decode(
                'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
            )
        return {
            'filename': filename,
            'mime_type': mime_type,
            'data_base64': base64.b64encode(data).decode('ascii'),
        }

    def create_choice_item(self, **changes):
        payload = {**self.choice_payload(), **changes}
        status, result = self.request('POST', '/api/items', payload)
        self.assertEqual(status, 200, result)
        return result['id']

    def test_migrates_old_items_table_without_losing_rows(self):
        legacy_path = Path(self.temp_dir.name) / 'legacy.db'
        con = sqlite3.connect(legacy_path)
        try:
            with con:
                con.execute('''CREATE TABLE items (
                    id INTEGER PRIMARY KEY, direction TEXT NOT NULL,
                    category TEXT NOT NULL, content TEXT NOT NULL,
                    status TEXT NOT NULL, feedback TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
                con.execute('''INSERT INTO items(direction, category, content, status)
                               VALUES('下达', '自由内容', '旧记录', '待老人确认')''')
        finally:
            con.close()
        server.DB = legacy_path
        server.initialize()
        with server.connect() as con:
            columns = {row['name'] for row in con.execute('PRAGMA table_info(items)')}
            tables = {row['name'] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            row = con.execute('SELECT * FROM items').fetchone()
        self.assertTrue({
            'event_time', 'location', 'image_key', 'response_mode',
            'reminder_enabled', 'selected_option_id', 'explanation',
            'explanation_source',
        } <= columns)
        self.assertTrue({'item_options', 'item_images', 'reminders', 'reminder_events'} <= tables)
        with server.connect() as con:
            image_columns = {
                value['name'] for value in con.execute('PRAGMA table_info(item_images)')
            }
        self.assertIn('source', image_columns)
        self.assertEqual(row['content'], '旧记录')
        self.assertEqual(row['response_mode'], 'acknowledge')
        self.assertEqual(row['reminder_enabled'], 0)
        server.DB = self.db_path

    def test_structured_item_keeps_distinct_legacy_response_history(self):
        item_id = self.create_activity()
        status, item = self.request('GET', f'/api/items/{item_id}')
        self.assertEqual(status, 200)
        self.assertEqual(item['status'], '待老人回应')
        self.assertEqual(item['event_time'], '2099-10-14T15:00')
        self.assertEqual(item['location'], '一楼活动室')
        self.assertEqual(item['events'], [])
        self.assertEqual(item['options'], [])

        expected = [
            ('确认收到', '已确认收到'),
            ('参加', '已选择参加'),
            ('稍后决定', '稍后决定'),
            ('拒绝', '已拒绝'),
            ('需要帮助', '需要帮助'),
        ]
        for action, expected_status in expected:
            status, result = self.request('POST', '/api/respond', {
                'id': item_id,
                'action': action,
            })
            self.assertEqual(status, 200)
            self.assertEqual(result['status'], expected_status)

        status, item = self.request('GET', f'/api/items/{item_id}')
        self.assertEqual(status, 200)
        self.assertEqual(item['status'], '需要帮助')
        self.assertEqual([event['action'] for event in item['events']],
                         [action for action, _ in expected])
        self.assertTrue(all(event['actor'] == '老人' for event in item['events']))

    def test_only_structured_items_store_configurable_options(self):
        payload = self.choice_payload(reminder_enabled=False)
        payload['event_time'] = ''
        status, created = self.request('POST', '/api/items', payload)
        self.assertEqual(status, 200, created)
        status, item = self.request('GET', f"/api/items/{created['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(item['response_mode'], 'options')
        self.assertEqual(
            [option['label'] for option in item['options']],
            ['上午场', '下午场', '不参加'],
        )

    def test_publicity_and_custom_items_only_accept_copy_and_image(self):
        for category in ('反诈宣传', '自由内容'):
            status, created = self.request('POST', '/api/items', {
                'direction': '下达',
                'category': category,
                'content': f'这是虚构的{category}内容。',
                'image_key': 'auto',
            })
            self.assertEqual(status, 200, created)
            status, item = self.request('GET', f"/api/items/{created['id']}")
            self.assertEqual(status, 200)
            self.assertEqual(item['category'], category)
            self.assertEqual(item['response_mode'], 'acknowledge')
            self.assertEqual(item['options'], [])
            self.assertFalse(item['reminder_enabled'])

            forbidden_payloads = (
                {'event_time': '2099-10-14T15:00'},
                {'location': '一楼活动室'},
                {'reminder_enabled': True},
                {'options': [{'label': '知道了', 'kind': 'acknowledge'}]},
            )
            for forbidden in forbidden_payloads:
                status, error = self.request('POST', '/api/items', {
                    'direction': '下达',
                    'category': category,
                    'content': '这是一条虚构测试内容。',
                    'image_key': 'auto',
                    **forbidden,
                })
                self.assertEqual(status, 400)
                self.assertIn('只允许设置正文与图片', error['error'])

    def test_option_choice_updates_status_and_history(self):
        item_id = self.create_choice_item()
        status, item = self.request('GET', f'/api/items/{item_id}')
        option = item['options'][1]
        status, result = self.request('POST', '/api/respond', {
            'id': item_id,
            'action': '选择选项',
            'option_id': option['id'],
        })
        self.assertEqual(status, 200)
        self.assertEqual(result['status'], '已选择：下午场')
        self.assertTrue(result['reminder_available'])

        status, item = self.request('GET', f'/api/items/{item_id}')
        self.assertEqual(item['selected_option_id'], option['id'])
        self.assertEqual(item['events'][0]['action'], '选择回应')
        self.assertEqual(item['events'][0]['detail'], '下午场')

        status, later = self.request('POST', '/api/respond', {
            'id': item_id,
            'action': '稍后决定',
        })
        self.assertEqual(status, 200)
        self.assertEqual(later['status'], '稍后决定')
        status, item = self.request('GET', f'/api/items/{item_id}')
        self.assertIsNone(item['selected_option_id'])

    def test_rejects_invalid_or_cross_item_options(self):
        base = self.choice_payload(reminder_enabled=False)
        for options in (
            [],
            [{'label': '重复', 'kind': 'custom'}, {'label': '重复', 'kind': 'accept'}],
            [{'label': '错误含义', 'kind': 'unknown'}],
            [{'label': str(index), 'kind': 'custom'} for index in range(4)],
        ):
            status, error = self.request('POST', '/api/items', {**base, 'options': options})
            self.assertEqual(status, 400)
            self.assertIn('error', error)

        first_id = self.create_choice_item(reminder_enabled=False, event_time='')
        second_id = self.create_choice_item(reminder_enabled=False, event_time='')
        _, second = self.request('GET', f'/api/items/{second_id}')
        status, error = self.request('POST', '/api/respond', {
            'id': first_id,
            'action': '选择选项',
            'option_id': second['options'][0]['id'],
        })
        self.assertEqual(status, 400)
        self.assertIn('error', error)

    def test_staff_image_upload_is_stored_and_served_without_blob_in_items(self):
        payload = self.choice_payload(reminder_enabled=False)
        payload['event_time'] = ''
        payload['image_key'] = ''
        payload['image_upload'] = self.image_upload()
        status, created = self.request('POST', '/api/items', payload)
        self.assertEqual(status, 200, created)

        status, item = self.request('GET', f"/api/items/{created['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(item['image_url'], f"/api/items/{created['id']}/image")
        self.assertEqual(item['image_filename'], 'notice.png')
        self.assertEqual(item['image_mime_type'], 'image/png')
        self.assertEqual(item['image_source'], 'staff_upload')
        self.assertNotIn('image_upload', item)
        self.assertNotIn('data', item)

        with urlopen(self.base_url + item['image_url'], timeout=3) as response:
            image_data = response.read()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers['Content-Type'], 'image/png')
            self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        self.assertTrue(image_data.startswith(b'\x89PNG\r\n\x1a\n'))

    def test_supported_image_signatures_and_automatic_fallback(self):
        valid_uploads = [
            self.image_upload(),
            self.image_upload('image/jpeg', b'\xff\xd8\xff\xe0jpeg', 'notice.jpg'),
            self.image_upload('image/webp', b'RIFF\x04\x00\x00\x00WEBPdata', 'notice.webp'),
        ]
        for upload in valid_uploads:
            payload = self.choice_payload(reminder_enabled=False)
            payload.update({'event_time': '', 'image_key': '', 'image_upload': upload})
            status, created = self.request('POST', '/api/items', payload)
            self.assertEqual(status, 200, created)

        payload = self.choice_payload(reminder_enabled=False)
        payload.update({'event_time': '', 'image_key': ''})
        status, created = self.request('POST', '/api/items', payload)
        self.assertEqual(status, 200)
        _, item = self.request('GET', f"/api/items/{created['id']}")
        self.assertEqual(item['image_key'], 'auto')
        self.assertEqual(item['image_url'], '')

    def test_rejects_unsafe_or_oversized_image_uploads(self):
        base = self.choice_payload(reminder_enabled=False)
        base.update({'event_time': '', 'image_key': ''})
        invalid_uploads = [
            self.image_upload('image/svg+xml', b'<svg></svg>', 'notice.svg'),
            self.image_upload('image/png', b'not-a-png', 'notice.png'),
            self.image_upload('image/png', b'\x89PNG\r\n\x1a\n' + b'x' * server.MAX_IMAGE_BYTES, 'large.png'),
        ]
        for upload in invalid_uploads:
            status, error = self.request(
                'POST', '/api/items', {**base, 'image_upload': upload}
            )
            self.assertEqual(status, 400)
            self.assertIn('error', error)

        status, error = self.request('GET', '/api/items/999/image')
        self.assertEqual(status, 404)
        self.assertEqual(error['error'], '图片不存在')
        status, error = self.request('POST', '/api/items', {
            'direction': '上传',
            'category': '自由内容',
            'content': '老人上传原话。',
            'image_upload': self.image_upload(),
        })
        self.assertEqual(status, 400)
        self.assertIn('不能附带事项图片', error['error'])

    def test_reminder_requires_eligible_saved_choice(self):
        item_id = self.create_choice_item()
        status, error = self.request('POST', '/api/reminders', {
            'item_id': item_id,
            'offset_minutes': 60,
        })
        self.assertEqual(status, 400)
        self.assertIn('先保存', error['error'])

        _, item = self.request('GET', f'/api/items/{item_id}')
        reject_option = next(option for option in item['options'] if option['kind'] == 'reject')
        self.request('POST', '/api/respond', {
            'id': item_id,
            'action': '选择选项',
            'option_id': reject_option['id'],
        })
        status, error = self.request('POST', '/api/reminders', {
            'item_id': item_id,
            'offset_minutes': 60,
        })
        self.assertEqual(status, 400)
        self.assertIn('不能设置提醒', error['error'])

    def test_reminder_api_persists_only_after_valid_choice(self):
        item_id = self.create_choice_item()
        _, item = self.request('GET', f'/api/items/{item_id}')
        accept_option = item['options'][0]
        self.request('POST', '/api/respond', {
            'id': item_id,
            'action': '选择选项',
            'option_id': accept_option['id'],
        })
        status, reminder = self.request('POST', '/api/reminders', {
            'item_id': item_id,
            'offset_minutes': 60,
        })
        self.assertEqual(status, 200, reminder)
        self.assertEqual(reminder['status'], 'scheduled')
        self.assertEqual(reminder['scheduled_for'], '2099-10-14T14:00:00')

        status, error = self.request('POST', '/api/reminders', {
            'item_id': item_id,
            'offset_minutes': 10,
        })
        self.assertEqual(status, 400)
        self.assertIn('已有未完成', error['error'])
        _, item = self.request('GET', f'/api/items/{item_id}')
        self.assertEqual(item['reminders'][0]['id'], reminder['id'])
        self.assertEqual(item['reminders'][0]['events'][0]['action'], 'scheduled')

    def _direct_choice_and_reminder(self, event_at, current):
        with server.connect() as con:
            item = server.create_item(con, {
                'direction': '下达',
                'category': '指令与代办事项',
                'content': '虚构的提醒状态机测试。',
                'event_time': server.iso_time(event_at),
                'location': '一楼活动室',
                'image_key': 'activity',
                'reminder_enabled': True,
                'options': [{'label': '接受测试安排', 'kind': 'accept'}],
            })
            option = con.execute(
                'SELECT id FROM item_options WHERE item_id=?', (item['id'],)
            ).fetchone()
            server.respond_to_item(con, {
                'id': item['id'], 'action': '选择选项', 'option_id': option['id'],
            })
            reminder = server.create_reminder(con, {
                'item_id': item['id'], 'offset_minutes': 60,
            }, now=current)
        return item['id'], reminder['id']

    def test_agent_advances_due_retry_and_single_escalation(self):
        current = datetime(2035, 1, 1, 9, 0)
        item_id, reminder_id = self._direct_choice_and_reminder(
            datetime(2035, 1, 1, 11, 0), current
        )
        first_due = datetime(2035, 1, 1, 10, 0)
        with server.connect() as con:
            self.assertEqual(server.advance_reminders(con, first_due), [(reminder_id, 'due')])
            claimed = server.claim_reminder(con, {}, now=first_due)
            self.assertEqual(claimed['reminder']['status'], 'presented')
            self.assertEqual(claimed['reminder']['presentation_count'], 1)

            server.advance_reminders(con, first_due + timedelta(minutes=3))
            claimed = server.claim_reminder(
                con, {'exclude_ids': [reminder_id]},
                now=first_due + timedelta(minutes=3),
            )
            self.assertEqual(claimed['reminder']['presentation_count'], 2)

            server.advance_reminders(con, first_due + timedelta(minutes=6))
            reminder = con.execute(
                'SELECT * FROM reminders WHERE id=?', (reminder_id,)
            ).fetchone()
            self.assertEqual(reminder['status'], 'overdue')
            self.assertIsNotNone(reminder['escalated_at'])
            server.advance_reminders(con, first_due + timedelta(minutes=30))
            followups = con.execute(
                '''SELECT COUNT(*) AS total FROM item_events
                   WHERE item_id=? AND action='需要工作人员跟进' ''',
                (item_id,),
            ).fetchone()['total']
            self.assertEqual(followups, 1)

    def test_unclaimed_reminder_is_not_reported_as_presented(self):
        current = datetime(2035, 2, 1, 9, 0)
        item_id, reminder_id = self._direct_choice_and_reminder(
            datetime(2035, 2, 1, 11, 0), current
        )
        due = datetime(2035, 2, 1, 10, 0)
        with server.connect() as con:
            server.advance_reminders(con, due)
            server.advance_reminders(
                con, due + timedelta(minutes=server.REMINDER_UNCLAIMED_GRACE_MINUTES + 1)
            )
            reminder = con.execute(
                'SELECT * FROM reminders WHERE id=?', (reminder_id,)
            ).fetchone()
            self.assertEqual(reminder['status'], 'overdue')
            self.assertEqual(reminder['presentation_count'], 0)
            claimed = server.claim_reminder(
                con, {}, now=due + timedelta(minutes=20)
            )
            self.assertEqual(claimed['reminder']['status'], 'overdue_presented')
            result = server.respond_to_reminder(con, {
                'id': reminder_id, 'action': 'help',
            }, now=due + timedelta(minutes=21))
            self.assertEqual(result['status'], 'help_requested')
            event = con.execute(
                '''SELECT * FROM item_events
                   WHERE item_id=? AND action='提醒时请求帮助' ''',
                (item_id,),
            ).fetchone()
            self.assertIsNotNone(event)
            self.assertIn('不表示已获得帮助', event['detail'])

    def test_reminder_acknowledge_and_snooze_are_distinct(self):
        current = datetime(2035, 3, 1, 9, 0)
        _, reminder_id = self._direct_choice_and_reminder(
            datetime(2035, 3, 1, 11, 0), current
        )
        due = datetime(2035, 3, 1, 10, 0)
        with server.connect() as con:
            server.advance_reminders(con, due)
            server.claim_reminder(con, {}, now=due)
            snoozed = server.respond_to_reminder(con, {
                'id': reminder_id, 'action': 'snooze',
            }, now=due + timedelta(minutes=1))
            self.assertEqual(snoozed['status'], 'scheduled')
            row = con.execute('SELECT * FROM reminders WHERE id=?', (reminder_id,)).fetchone()
            self.assertEqual(row['scheduled_for'], '2035-03-01T10:11:00')

            server.advance_reminders(con, datetime(2035, 3, 1, 10, 11))
            server.claim_reminder(con, {}, now=datetime(2035, 3, 1, 10, 11))
            acknowledged = server.respond_to_reminder(con, {
                'id': reminder_id, 'action': 'acknowledge',
            }, now=datetime(2035, 3, 1, 10, 12))
            self.assertEqual(acknowledged['status'], 'acknowledged')
            self.assertIn('不表示事项已经完成', acknowledged['detail'])

    def test_avatar_runtime_reports_rendering_without_persistence(self):
        status, initial = self.request('GET', '/api/avatar-runtime')
        self.assertEqual(status, 200)
        self.assertFalse(initial['fresh'])
        self.assertEqual(initial['render_state'], 'uninitialized')

        status, saved = self.request(
            'POST', '/api/avatar-runtime', self.runtime_payload(current_item_id=7)
        )
        self.assertEqual(status, 200)
        self.assertTrue(saved['fresh'])
        self.assertTrue(saved['elder_active'])
        self.assertEqual(saved['render_state'], 'rendering')
        self.assertEqual(saved['asr_state'], 'listening')
        self.assertEqual(saved['current_item_id'], 7)
        self.assertEqual(
            saved['canvas_layout'],
            'Canvas1 405×720 @ 438,0 缓冲区1080×1920 absolute block/visible/o1 z100',
        )
        self.assertEqual(saved['render_node_count'], 2)
        self.assertIn('avatar-bg-container', saved['render_layout'])

        status, current = self.request('GET', '/api/avatar-runtime')
        self.assertEqual(status, 200)
        self.assertEqual(current['session_id'], 'test-session')
        self.assertEqual(current['canvas_count'], 1)
        self.assertEqual(current['render_node_count'], 2)
        with server.connect() as con:
            tables = {row['name'] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
        self.assertNotIn('avatar_runtime', tables)

    def test_avatar_runtime_rejects_credentials_and_incomplete_state(self):
        with_secret = self.runtime_payload(appSecret='must-not-be-accepted')
        status, error = self.request('POST', '/api/avatar-runtime', with_secret)
        self.assertEqual(status, 400)
        self.assertIn('不允许', error['error'])

        incomplete = self.runtime_payload()
        incomplete.pop('render_state')
        status, error = self.request('POST', '/api/avatar-runtime', incomplete)
        self.assertEqual(status, 400)
        self.assertIn('不完整', error['error'])

        too_many_nodes = self.runtime_payload(render_node_count=41)
        status, error = self.request('POST', '/api/avatar-runtime', too_many_nodes)
        self.assertEqual(status, 400)
        self.assertIn('render_node_count', error['error'])

        oversized_layout = self.runtime_payload(render_layout='x' * 1601)
        status, error = self.request('POST', '/api/avatar-runtime', oversized_layout)
        self.assertEqual(status, 400)
        self.assertIn('过长', error['error'])

        invalid_asr = self.runtime_payload(asr_state='recording')
        status, error = self.request('POST', '/api/avatar-runtime', invalid_asr)
        self.assertEqual(status, 400)
        self.assertIn('语音识别状态', error['error'])

        status, current = self.request('GET', '/api/avatar-runtime')
        self.assertEqual(status, 200)
        self.assertEqual(current['session_id'], '')
        self.assertFalse(current['fresh'])

    def test_avatar_runtime_marks_stale_or_closed_session_offline(self):
        reported_at = datetime(2035, 4, 1, 9, 0)
        server.update_avatar_runtime(self.runtime_payload(), now=reported_at)
        fresh = server.avatar_runtime_snapshot(
            now=reported_at + timedelta(seconds=server.AVATAR_RUNTIME_STALE_SECONDS)
        )
        self.assertTrue(fresh['fresh'])
        stale = server.avatar_runtime_snapshot(
            now=reported_at + timedelta(seconds=server.AVATAR_RUNTIME_STALE_SECONDS + 1)
        )
        self.assertFalse(stale['fresh'])
        self.assertFalse(stale['elder_active'])

        server.update_avatar_runtime(
            self.runtime_payload(page_state='closing'),
            now=reported_at + timedelta(minutes=1),
        )
        closed = server.avatar_runtime_snapshot(
            now=reported_at + timedelta(minutes=1)
        )
        self.assertFalse(closed['fresh'])
        self.assertEqual(closed['page_state'], 'closing')

    def test_avatar_runtime_prefers_active_elder_over_staff_monitor_session(self):
        reported_at = datetime(2035, 4, 2, 9, 0)
        server.update_avatar_runtime(
            self.runtime_payload(session_id='elder-session'), now=reported_at
        )
        server.update_avatar_runtime(
            self.runtime_payload(
                session_id='staff-session',
                page_state='staff_view',
                connection_state='disconnected',
                render_state='uninitialized',
                canvas_count=0,
                canvas_layout='',
                render_node_count=0,
                render_layout='',
            ),
            now=reported_at + timedelta(seconds=2),
        )
        current = server.avatar_runtime_snapshot(
            now=reported_at + timedelta(seconds=3)
        )
        self.assertEqual(current['session_id'], 'elder-session')
        self.assertTrue(current['elder_active'])
        self.assertEqual(current['render_state'], 'rendering')

    def test_ai_status_exposes_image_and_text_capability_without_key(self):
        secret = 'ark-secret-must-not-leak'
        with patch.dict(os.environ, {
            'ARK_API_KEY': secret,
            'ARK_IMAGE_MODEL': 'doubao-image-test',
            'ARK_TEXT_MODEL': 'doubao-text-test',
        }, clear=False):
            status, result = self.request('GET', '/api/ai/status')
        self.assertEqual(status, 200)
        self.assertEqual(result, {
            'image_ready': True,
            'image_model': 'doubao-image-test',
            'text_ready': True,
            'text_model': 'doubao-text-test',
        })
        self.assertNotIn(secret, json.dumps(result))

    def test_voice_chat_returns_reply_without_writing_db(self):
        with patch.object(server.doubao, 'generate_chat_reply', return_value='我是知应。') as mocked:
            status, result = self.request('POST', '/api/voice/chat', {'text': '你是谁？'})
        self.assertEqual(status, 200, result)
        self.assertEqual(result, {'reply': '我是知应。'})
        mocked.assert_called_once_with('你是谁？')
        with server.connect() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM items').fetchone()[0], 0)

    def test_voice_chat_rejects_empty_or_overlong_and_unconfigured(self):
        status, error = self.request('POST', '/api/voice/chat', {'text': ''})
        self.assertEqual(status, 400)
        self.assertIn('error', error)

        status, error = self.request('POST', '/api/voice/chat', {'text': '长' * 2001})
        self.assertEqual(status, 400)

        with patch.object(server.doubao, 'generate_chat_reply', side_effect=server.doubao.DoubaoNotConfigured('对话')):
            status, error = self.request('POST', '/api/voice/chat', {'text': '你好'})
        self.assertEqual(status, 503)
        self.assertEqual(error['code'], 'not_configured')

    def test_ai_image_route_and_confirmed_source_are_separate_steps(self):
        generated = self.image_upload(
            'image/jpeg', b'\xff\xd8\xff\xe0generated', 'doubao-generated.jpg'
        )
        with patch.object(server.doubao, 'generate_image', return_value=generated) as mocked:
            status, preview = self.request('POST', '/api/ai/image', {
                'category': '反诈宣传',
                'content': '这是一条虚构的反诈宣传内容。',
                'event_time': '',
                'location': '',
            })
        self.assertEqual(status, 200, preview)
        self.assertEqual(preview, {'image_upload': generated})
        mocked.assert_called_once()

        with server.connect() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM items').fetchone()[0], 0)

        status, created = self.request('POST', '/api/items', {
            'direction': '下达',
            'category': '反诈宣传',
            'content': '这是一条虚构的反诈宣传内容。',
            'image_key': 'auto',
            'image_upload': preview['image_upload'],
            'image_source': 'doubao',
        })
        self.assertEqual(status, 200, created)
        _, item = self.request('GET', f"/api/items/{created['id']}")
        self.assertEqual(item['image_source'], 'doubao')
        self.assertNotIn('explanation', item)
        self.assertNotIn('explanation_source', item)

    def test_text_generation_routes_and_explanation_fields_are_disabled(self):
        status, error = self.request('POST', '/api/ai/generate', {
            'category': '反诈宣传', 'content': '虚构内容',
        })
        self.assertEqual(status, 404)
        self.assertEqual(error['error'], '接口不存在')
        status, error = self.request('POST', '/api/ai/rephrase', {'item_id': 1})
        self.assertEqual(status, 404)
        self.assertEqual(error['error'], '接口不存在')

        for field in ('explanation', 'explanation_source'):
            status, error = self.request('POST', '/api/items', {
                'direction': '下达',
                'category': '自由内容',
                'content': '虚构自定义内容。',
                'image_key': 'auto',
                field: '不应接受的文字模型结果',
            })
            self.assertEqual(status, 400)
            self.assertIn('只用于辅助生成图片', error['error'])

    def test_ai_image_route_rejects_non_staff_or_wrong_category_fields(self):
        for payload in (
            {'category': '症状描述', 'content': '老人症状原话'},
            {
                'category': '反诈宣传',
                'content': '虚构宣传',
                'event_time': '2099-10-14T15:00',
            },
        ):
            status, error = self.request('POST', '/api/ai/image', payload)
            self.assertEqual(status, 400)
            self.assertIn('error', error)

    def test_acknowledgement_notice_rejects_attendance_choice(self):
        status, result = self.request('POST', '/api/items', {
            'direction': '下达',
            'category': '反诈宣传',
            'content': '这是虚构的反诈宣传。',
            'response_mode': 'acknowledge',
        })
        self.assertEqual(status, 200)
        status, error = self.request('POST', '/api/respond', {
            'id': result['id'],
            'action': '参加',
        })
        self.assertEqual(status, 400)
        self.assertIn('error', error)

    def test_rejects_invalid_structured_fields(self):
        base = {
            'direction': '下达',
            'category': '指令与代办事项',
            'content': '虚构活动通知',
            'event_time': '2099-10-14T15:00',
            'location': '一楼活动室',
            'image_key': 'activity',
            'response_mode': 'attendance',
        }
        for changes in (
            {'image_key': 'https://example.com/image.png'},
            {'event_time': '明天下午三点'},
            {'location': ''},
            {'category': '自由内容'},
        ):
            payload = {**base, **changes}
            status, error = self.request('POST', '/api/items', payload)
            self.assertEqual(status, 400)
            self.assertIn('error', error)

    def test_unknown_item_and_reminder_return_404(self):
        status, error = self.request('GET', '/api/items/999')
        self.assertEqual(status, 404)
        self.assertEqual(error['error'], '事项不存在')
        status, error = self.request('POST', '/api/respond', {
            'id': 999,
            'action': '确认收到',
        })
        self.assertEqual(status, 404)
        self.assertEqual(error['error'], '事项不存在')
        status, error = self.request('POST', '/api/reminders/respond', {
            'id': 999,
            'action': 'acknowledge',
        })
        self.assertEqual(status, 404)
        self.assertEqual(error['error'], '提醒不存在')

    def test_staff_reply_is_saved_in_event_history(self):
        status, result = self.request('POST', '/api/items', {
            'direction': '上传',
            'category': '求助与代办申请',
            'content': '请工作人员联系我。',
        })
        self.assertEqual(status, 200)
        item_id = result['id']
        status, response = self.request('POST', '/api/respond', {
            'id': item_id,
            'action': '工作人员回复',
            'feedback': '工作人员稍后前来了解情况。',
        })
        self.assertEqual(status, 200)
        self.assertEqual(response['status'], '工作人员已回复')
        status, item = self.request('GET', f'/api/items/{item_id}')
        self.assertEqual(status, 200)
        self.assertEqual(item['feedback'], '工作人员稍后前来了解情况。')
        self.assertEqual(item['events'][0]['actor'], '工作人员')
        self.assertEqual(item['events'][0]['action'], '工作人员回复')

    def test_delete_item_removes_item_and_cascades_options_and_image(self):
        status, result = self.request('POST', '/api/items', {
            'direction': '下达',
            'category': '指令与代办事项',
            'content': '这是一条将被删除的虚构事项。',
            'event_time': '2099-10-14T15:00',
            'location': '一楼活动室',
            'reminder_enabled': True,
            'image_upload': self.image_upload(),
            'image_source': 'staff_upload',
            'options': [
                {'label': '上午场', 'kind': 'accept'},
                {'label': '下午场', 'kind': 'accept'},
            ],
        })
        self.assertEqual(status, 200, result)
        item_id = result['id']
        status, _ = self.request('DELETE', f'/api/items/{item_id}')
        self.assertEqual(status, 200)
        status, _ = self.request('GET', f'/api/items/{item_id}')
        self.assertEqual(status, 404)
        with sqlite3.connect(server.DB) as con:
            self.assertIsNone(con.execute('SELECT 1 FROM items WHERE id=?', (item_id,)).fetchone())
            self.assertIsNone(con.execute('SELECT 1 FROM item_options WHERE item_id=?', (item_id,)).fetchone())
            self.assertIsNone(con.execute('SELECT 1 FROM item_images WHERE item_id=?', (item_id,)).fetchone())
        con.close()

    def test_delete_missing_or_invalid_item_returns_404(self):
        for path in ('/api/items/999999', '/api/items/abc'):
            status, _ = self.request('DELETE', path)
            self.assertEqual(status, 404)


if __name__ == '__main__':
    unittest.main()
