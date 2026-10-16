import base64
import binascii
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

import doubao

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get('ZHIYING_DB_PATH', ROOT / 'data' / 'app.db'))
PORT = int(os.environ.get('ZHIYING_PORT', '8765'))
STATIC_FILES = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/index.html': ('index.html', 'text/html; charset=utf-8'),
    '/avatar-test.html': ('avatar-test.html', 'text/html; charset=utf-8'),
    '/avatar.js': ('avatar.js', 'text/javascript; charset=utf-8'),
    '/avatar.css': ('avatar.css', 'text/css; charset=utf-8'),
}
ALLOWED_CATEGORIES = {
    '上传': ['症状描述', '求助与代办申请', '自由内容'],
    '下达': ['指令与代办事项', '反诈宣传', '自由内容'],
}
IMAGE_KEYS = {'', 'auto', 'activity', 'location', 'fraud', 'notice'}
IMAGE_MIME_TYPES = {'image/png', 'image/jpeg', 'image/webp'}
IMAGE_SOURCES = {'staff_upload', 'doubao'}
MAX_IMAGE_BYTES = 1572864
MAX_ITEM_REQUEST_BYTES = 2200000
RESPONSE_ACTIONS = {
    'acknowledge': {'确认收到', '稍后决定', '需要帮助'},
    'attendance': {'确认收到', '参加', '稍后决定', '拒绝', '需要帮助'},
}
ACTION_STATUS = {
    '确认收到': '已确认收到',
    '参加': '已选择参加',
    '稍后决定': '稍后决定',
    '拒绝': '已拒绝',
    '需要帮助': '需要帮助',
}
OPTION_KINDS = {'accept', 'acknowledge', 'reject', 'later', 'help', 'custom'}
OPTION_STATUS = {
    'accept': lambda label: f'已选择：{label}',
    'acknowledge': lambda label: '已确认收到',
    'reject': lambda label: '已拒绝',
    'later': lambda label: '稍后决定',
    'help': lambda label: '需要帮助',
    'custom': lambda label: f'已选择：{label}',
}
REMINDER_ACTIVE_STATUSES = {'scheduled', 'due', 'presented', 'overdue', 'overdue_presented'}
REMINDER_RESPONSE_ACTIONS = {'acknowledge', 'snooze', 'help'}
REMINDER_OFFSETS = {10, 60, 1440}
REMINDER_MAX_PRESENTATIONS = 2
REMINDER_RETRY_MINUTES = 2
REMINDER_UNCLAIMED_GRACE_MINUTES = 15
ITEM_COLUMNS = {
    'event_time': "TEXT NOT NULL DEFAULT ''",
    'location': "TEXT NOT NULL DEFAULT ''",
    'image_key': "TEXT NOT NULL DEFAULT ''",
    'response_mode': "TEXT NOT NULL DEFAULT 'acknowledge'",
    'reminder_enabled': 'INTEGER NOT NULL DEFAULT 0',
    'selected_option_id': 'INTEGER',
    'explanation': "TEXT NOT NULL DEFAULT ''",
    'explanation_source': "TEXT NOT NULL DEFAULT ''",
}
ITEM_IMAGE_COLUMNS = {
    'source': "TEXT NOT NULL DEFAULT 'staff_upload'",
}
AVATAR_RUNTIME_FIELDS = {
    'session_id', 'page_state', 'connection_state', 'render_state',
    'speech_state', 'asr_state', 'current_item_id', 'last_action',
    'container_width', 'container_height', 'canvas_count', 'canvas_layout',
    'render_node_count', 'render_layout',
}
AVATAR_PAGE_STATES = {'elder_active', 'staff_view', 'hidden', 'closing'}
AVATAR_CONNECTION_STATES = {'disconnected', 'connecting', 'connected', 'error'}
AVATAR_RENDER_STATES = {
    'uninitialized', 'init', 'rendering', 'pausing', 'paused',
    'resumed', 'stopped', 'unavailable',
}
AVATAR_SPEECH_STATES = {'idle', 'requesting', 'speaking', 'interrupted', 'error'}
AVATAR_ASR_STATES = {
    'idle', 'requesting-permission', 'starting', 'listening', 'stopping', 'failed',
}
AVATAR_RUNTIME_STALE_SECONDS = 30
AVATAR_RUNTIME_LOCK = threading.Lock()
AVATAR_RUNTIMES = {}


def now_local():
    return datetime.now().replace(microsecond=0)


def iso_time(value):
    return value.replace(microsecond=0).isoformat(timespec='seconds')


@contextmanager
def connect():
    con = sqlite3.connect(DB, timeout=5)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys = ON')
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def initialize():
    DB.parent.mkdir(parents=True, exist_ok=True)
    with connect() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY, direction TEXT NOT NULL,
            category TEXT NOT NULL, content TEXT NOT NULL,
            status TEXT NOT NULL, feedback TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
        columns = {row['name'] for row in con.execute('PRAGMA table_info(items)')}
        for name, definition in ITEM_COLUMNS.items():
            if name not in columns:
                con.execute(f'ALTER TABLE items ADD COLUMN {name} {definition}')
        con.execute('''CREATE TABLE IF NOT EXISTS item_events (
            id INTEGER PRIMARY KEY,
            item_id INTEGER NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE CASCADE)''')
        con.execute('''CREATE TABLE IF NOT EXISTS item_options (
            id INTEGER PRIMARY KEY,
            item_id INTEGER NOT NULL,
            label TEXT NOT NULL,
            kind TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE CASCADE,
            UNIQUE(item_id, sort_order))''')
        con.execute('''CREATE TABLE IF NOT EXISTS item_images (
            item_id INTEGER PRIMARY KEY,
            mime_type TEXT NOT NULL,
            filename TEXT NOT NULL,
            data BLOB NOT NULL,
            source TEXT NOT NULL DEFAULT 'staff_upload',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE CASCADE)''')
        image_columns = {
            row['name'] for row in con.execute('PRAGMA table_info(item_images)')
        }
        for name, definition in ITEM_IMAGE_COLUMNS.items():
            if name not in image_columns:
                con.execute(f'ALTER TABLE item_images ADD COLUMN {name} {definition}')
        con.execute('''CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY,
            item_id INTEGER NOT NULL,
            option_id INTEGER NOT NULL,
            scheduled_for TEXT NOT NULL,
            status TEXT NOT NULL,
            presentation_count INTEGER NOT NULL DEFAULT 0,
            next_action_at TEXT,
            due_at TEXT,
            last_presented_at TEXT,
            resolved_at TEXT,
            escalated_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE CASCADE,
            FOREIGN KEY(option_id) REFERENCES item_options(id) ON DELETE CASCADE)''')
        con.execute('''CREATE TABLE IF NOT EXISTS reminder_events (
            id INTEGER PRIMARY KEY,
            reminder_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(reminder_id) REFERENCES reminders(id) ON DELETE CASCADE)''')
        con.execute('''CREATE INDEX IF NOT EXISTS idx_item_events_item_id
                       ON item_events(item_id, id)''')
        con.execute('''CREATE INDEX IF NOT EXISTS idx_item_options_item_id
                       ON item_options(item_id, sort_order)''')
        con.execute('''CREATE INDEX IF NOT EXISTS idx_reminders_state
                       ON reminders(status, next_action_at, scheduled_for)''')
        con.execute('''CREATE INDEX IF NOT EXISTS idx_reminders_item_id
                       ON reminders(item_id, id)''')
        con.execute('''CREATE INDEX IF NOT EXISTS idx_reminder_events_reminder_id
                       ON reminder_events(reminder_id, id)''')


def text_field(data, name, maximum, required=False):
    value = str(data.get(name, '') or '').strip()
    if required and not value:
        raise ValueError(f'{name}不能为空')
    if len(value) > maximum:
        raise ValueError(f'{name}过长')
    return value


def parse_time(value, field_name='时间'):
    if not value or len(value) > 32 or 'T' not in value:
        raise ValueError(f'{field_name}格式不正确')
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f'{field_name}格式不正确') from error


def validate_event_time(value):
    if value:
        parse_time(value, '时间')


def positive_int(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f'{name}不正确')
    return value


def boolean_field(data, name):
    value = data.get(name, False)
    if not isinstance(value, bool):
        raise ValueError(f'{name}不正确')
    return value


def empty_avatar_runtime():
    return {
        'session_id': '',
        'page_state': 'closing',
        'connection_state': 'disconnected',
        'render_state': 'uninitialized',
        'speech_state': 'idle',
        'asr_state': 'idle',
        'current_item_id': None,
        'last_action': '尚无老人端数字人会话',
        'container_width': 0,
        'container_height': 0,
        'canvas_count': 0,
        'canvas_layout': '',
        'render_node_count': 0,
        'render_layout': '',
        'updated_at': '',
    }


def reset_avatar_runtime():
    with AVATAR_RUNTIME_LOCK:
        AVATAR_RUNTIMES.clear()


def runtime_integer(data, name, maximum):
    value = data.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise ValueError(f'{name}不正确')
    return value


def update_avatar_runtime(data, now=None):
    unknown = set(data) - AVATAR_RUNTIME_FIELDS
    if unknown:
        raise ValueError('运行状态包含不允许的字段')
    missing = AVATAR_RUNTIME_FIELDS - set(data)
    if missing:
        raise ValueError('运行状态字段不完整')

    session_id = str(data.get('session_id', '') or '').strip()
    if not session_id or len(session_id) > 64:
        raise ValueError('会话标识不正确')
    page_state = str(data.get('page_state', '') or '').strip()
    connection_state = str(data.get('connection_state', '') or '').strip()
    render_state = str(data.get('render_state', '') or '').strip()
    speech_state = str(data.get('speech_state', '') or '').strip()
    asr_state = str(data.get('asr_state', '') or '').strip()
    if page_state not in AVATAR_PAGE_STATES:
        raise ValueError('页面状态不正确')
    if connection_state not in AVATAR_CONNECTION_STATES:
        raise ValueError('连接状态不正确')
    if render_state not in AVATAR_RENDER_STATES:
        raise ValueError('渲染状态不正确')
    if speech_state not in AVATAR_SPEECH_STATES:
        raise ValueError('播报状态不正确')
    if asr_state not in AVATAR_ASR_STATES:
        raise ValueError('语音识别状态不正确')

    item_id = data.get('current_item_id')
    if item_id is not None:
        item_id = positive_int(item_id, '事项编号')
    last_action = str(data.get('last_action', '') or '').strip()
    if len(last_action) > 300:
        raise ValueError('最近动作过长')
    current = now or now_local()
    value = {
        'session_id': session_id,
        'page_state': page_state,
        'connection_state': connection_state,
        'render_state': render_state,
        'speech_state': speech_state,
        'asr_state': asr_state,
        'current_item_id': item_id,
        'last_action': last_action,
        'container_width': runtime_integer(data, 'container_width', 10000),
        'container_height': runtime_integer(data, 'container_height', 10000),
        'canvas_count': runtime_integer(data, 'canvas_count', 20),
        'canvas_layout': text_field(data, 'canvas_layout', 800),
        'render_node_count': runtime_integer(data, 'render_node_count', 40),
        'render_layout': text_field(data, 'render_layout', 1600),
        'updated_at': iso_time(current),
    }
    with AVATAR_RUNTIME_LOCK:
        AVATAR_RUNTIMES[session_id] = value
        ordered = sorted(
            AVATAR_RUNTIMES.values(),
            key=lambda runtime: runtime.get('updated_at', ''),
            reverse=True,
        )
        if len(ordered) > 20:
            keep = {runtime['session_id'] for runtime in ordered[:20]}
            for stored_session_id in list(AVATAR_RUNTIMES):
                if stored_session_id not in keep:
                    del AVATAR_RUNTIMES[stored_session_id]
    return avatar_runtime_snapshot(current)


def avatar_runtime_snapshot(now=None):
    current = now or now_local()
    with AVATAR_RUNTIME_LOCK:
        runtimes = [dict(value) for value in AVATAR_RUNTIMES.values()]
    if not runtimes:
        value = empty_avatar_runtime()
        value['age_seconds'] = None
        value['fresh'] = False
        value['elder_active'] = False
        return value

    decorated = []
    for value in runtimes:
        age_seconds = None
        updated_at = value.get('updated_at', '')
        if updated_at:
            try:
                age_seconds = max(
                    0,
                    int((current - datetime.fromisoformat(updated_at)).total_seconds()),
                )
            except ValueError:
                age_seconds = None
        fresh = bool(
            age_seconds is not None
            and age_seconds <= AVATAR_RUNTIME_STALE_SECONDS
            and value.get('page_state') != 'closing'
        )
        value['age_seconds'] = age_seconds
        value['fresh'] = fresh
        value['elder_active'] = fresh and value.get('page_state') == 'elder_active'
        decorated.append(value)

    def priority(value):
        page_priority = {
            'elder_active': 3,
            'hidden': 2,
            'staff_view': 1,
            'closing': 0,
        }.get(value.get('page_state'), 0)
        return (
            int(value['fresh']),
            page_priority,
            value.get('updated_at', ''),
        )

    return max(decorated, key=priority)


reset_avatar_runtime()


def validate_options(raw_options):
    if not isinstance(raw_options, list) or not 1 <= len(raw_options) <= 3:
        raise ValueError('请设置1到3个回应选项')
    options = []
    labels = set()
    for index, raw in enumerate(raw_options):
        if not isinstance(raw, dict):
            raise ValueError('回应选项格式不正确')
        label = text_field(raw, 'label', 24, required=True)
        kind = str(raw.get('kind', '')).strip()
        if kind not in OPTION_KINDS:
            raise ValueError('回应选项含义不正确')
        normalized = label.casefold()
        if normalized in labels:
            raise ValueError('回应选项不能重复')
        labels.add(normalized)
        options.append({'label': label, 'kind': kind, 'sort_order': index})
    return options


def validate_image_upload(raw_upload):
    if raw_upload in (None, ''):
        return None
    if not isinstance(raw_upload, dict):
        raise ValueError('上传图片格式不正确')
    if set(raw_upload) != {'filename', 'mime_type', 'data_base64'}:
        raise ValueError('上传图片字段不正确')
    filename = text_field(raw_upload, 'filename', 120, required=True)
    mime_type = str(raw_upload.get('mime_type', '') or '').strip().lower()
    if mime_type not in IMAGE_MIME_TYPES:
        raise ValueError('只支持PNG、JPEG或WebP图片')
    encoded = str(raw_upload.get('data_base64', '') or '').strip()
    if not encoded:
        raise ValueError('上传图片内容为空')
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError('上传图片编码不正确') from error
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError('上传图片大小必须在1.5 MiB以内')
    valid_signature = (
        mime_type == 'image/png' and data.startswith(b'\x89PNG\r\n\x1a\n')
        or mime_type == 'image/jpeg' and data.startswith(b'\xff\xd8\xff')
        or mime_type == 'image/webp'
        and len(data) >= 12
        and data.startswith(b'RIFF')
        and data[8:12] == b'WEBP'
    )
    if not valid_signature:
        raise ValueError('上传图片内容与文件类型不一致')
    return {'filename': filename, 'mime_type': mime_type, 'data': data}


def validate_ai_facts(data):
    category = str(data.get('category', '') or '').strip()
    if category not in ALLOWED_CATEGORIES['下达']:
        raise ValueError('豆包只处理工作人员下达内容')
    content = text_field(data, 'content', 2000, required=True)
    event_time = text_field(data, 'event_time', 32)
    location = text_field(data, 'location', 200)
    if category == '指令与代办事项':
        if event_time:
            validate_event_time(event_time)
    elif event_time or location:
        raise ValueError('宣传讲解和自定义内容只允许设置正文与图片')
    return {
        'category': category,
        'content': content,
        'event_time': event_time,
        'location': location,
    }


def generate_ai_image(data):
    facts = validate_ai_facts(data)
    return {'image_upload': doubao.generate_image(facts)}


def generate_voice_reply(data):
    text = text_field(data, 'text', 2000, required=True)
    return {'reply': doubao.generate_chat_reply(text)}


def create_item(con, data):
    direction = data.get('direction')
    category = data.get('category')
    if direction not in ALLOWED_CATEGORIES or category not in ALLOWED_CATEGORIES[direction]:
        raise ValueError('事项类型不正确')
    content = text_field(data, 'content', 2000, required=True)
    if direction != '下达' and any(
        data.get(name) not in (None, '')
        for name in ('image_upload', 'image_source')
    ):
        raise ValueError('老人上传内容不能附带事项图片')
    if any(name in data for name in ('explanation', 'explanation_source')):
        raise ValueError('豆包只用于辅助生成图片，不接受模型讲解字段')
    event_time = ''
    location = ''
    image_key = ''
    response_mode = 'acknowledge'
    reminder_enabled = False
    options = []
    image_upload = None
    image_source = ''

    if direction == '下达':
        image_key = str(data.get('image_key', '')).strip()
        if image_key not in IMAGE_KEYS:
            raise ValueError('图片类型不正确')
        image_upload = validate_image_upload(data.get('image_upload'))
        if image_upload:
            image_key = ''
            image_source = str(data.get('image_source', 'staff_upload') or '').strip()
            if image_source not in IMAGE_SOURCES:
                raise ValueError('图片来源不正确')
        else:
            if data.get('image_source') not in (None, ''):
                raise ValueError('没有图片时不能设置图片来源')
            if not image_key:
                image_key = 'auto'

        if category == '指令与代办事项':
            event_time = text_field(data, 'event_time', 32)
            location = text_field(data, 'location', 200)
            validate_event_time(event_time)
            reminder_enabled = boolean_field(data, 'reminder_enabled')
            if reminder_enabled and not event_time:
                raise ValueError('启用提醒时必须填写事项时间')

            if 'options' in data:
                options = validate_options(data['options'])
                response_mode = 'options'
                if reminder_enabled and not any(
                    option['kind'] == 'accept' for option in options
                ):
                    raise ValueError('启用提醒时至少需要一个可设置提醒的选项')
            else:
                response_mode = str(data.get('response_mode', 'acknowledge')).strip()
                if response_mode not in RESPONSE_ACTIONS:
                    raise ValueError('回应方式不正确')
                if response_mode == 'attendance':
                    if not event_time or not location or not image_key:
                        raise ValueError('选择事项需要时间、地点和审核图片')
        else:
            forbidden = (
                data.get('event_time') not in (None, '')
                or data.get('location') not in (None, '')
                or data.get('reminder_enabled') not in (None, False)
                or 'options' in data
                or data.get('response_mode') not in (None, '', 'acknowledge')
            )
            if forbidden:
                raise ValueError('宣传讲解和自定义内容只允许设置正文与图片')

    status = '待工作人员处理' if direction == '上传' else '待老人回应'
    cur = con.execute(
        '''INSERT INTO items(
               direction, category, content, status,
               event_time, location, image_key, response_mode,
               reminder_enabled)
           VALUES(?,?,?,?,?,?,?,?,?)''',
        (
            direction, category, content, status,
            event_time, location, image_key, response_mode,
            int(reminder_enabled),
        ),
    )
    item_id = cur.lastrowid
    for option in options:
        con.execute(
            '''INSERT INTO item_options(item_id, label, kind, sort_order)
               VALUES(?,?,?,?)''',
            (item_id, option['label'], option['kind'], option['sort_order']),
        )
    if image_upload:
        con.execute(
            '''INSERT INTO item_images(item_id, mime_type, filename, data, source)
               VALUES(?,?,?,?,?)''',
            (
                item_id,
                image_upload['mime_type'],
                image_upload['filename'],
                image_upload['data'],
                image_source,
            ),
        )
    return {'id': item_id, 'status': status}


def delete_item(con, item_id):
    cur = con.execute('DELETE FROM items WHERE id=?', (item_id,))
    return cur.rowcount > 0


def cancel_active_reminders(con, item_id, reason, now=None):
    current = now or now_local()
    placeholders = ','.join('?' for _ in REMINDER_ACTIVE_STATUSES)
    rows = con.execute(
        f'''SELECT id FROM reminders
            WHERE item_id=? AND status IN ({placeholders})''',
        (item_id, *REMINDER_ACTIVE_STATUSES),
    ).fetchall()
    if not rows:
        return
    stamp = iso_time(current)
    ids = [row['id'] for row in rows]
    id_placeholders = ','.join('?' for _ in ids)
    con.execute(
        f'''UPDATE reminders SET status='cancelled', resolved_at=?, next_action_at=NULL
            WHERE id IN ({id_placeholders})''',
        (stamp, *ids),
    )
    con.executemany(
        '''INSERT INTO reminder_events(reminder_id, action, detail)
           VALUES(?, 'cancelled', ?)''',
        [(reminder_id, reason) for reminder_id in ids],
    )


def respond_to_item(con, data):
    item_id = positive_int(data.get('id'), '事项编号')
    row = con.execute('SELECT * FROM items WHERE id=?', (item_id,)).fetchone()
    if row is None:
        return None
    action = data.get('action')

    if row['direction'] == '下达':
        options = con.execute(
            'SELECT * FROM item_options WHERE item_id=? ORDER BY sort_order, id',
            (item_id,),
        ).fetchall()
        if action == '选择选项':
            option_id = positive_int(data.get('option_id'), '选项编号')
            option = next((candidate for candidate in options if candidate['id'] == option_id), None)
            if option is None:
                raise ValueError('选项不属于此事项')
            status = OPTION_STATUS[option['kind']](option['label'])
            cancel_active_reminders(con, item_id, '老人重新选择了回应')
            con.execute(
                'UPDATE items SET status=?, selected_option_id=? WHERE id=?',
                (status, option_id, item_id),
            )
            con.execute(
                '''INSERT INTO item_events(item_id, actor, action, detail)
                   VALUES(?,?,?,?)''',
                (item_id, '老人', '选择回应', option['label']),
            )
            reminder_available = bool(
                row['reminder_enabled'] and row['event_time'] and option['kind'] == 'accept'
            )
            return {
                'ok': True,
                'status': status,
                'selected_option_id': option_id,
                'reminder_available': reminder_available,
            }

        if options:
            if action not in {'稍后决定', '拒绝', '需要帮助'}:
                raise ValueError('操作不适用于此事项')
        else:
            allowed = RESPONSE_ACTIONS.get(
                row['response_mode'], RESPONSE_ACTIONS['acknowledge']
            )
            if action not in allowed:
                raise ValueError('操作不适用于此事项')
        status = ACTION_STATUS[action]
        cancel_active_reminders(con, item_id, '老人更改了回应')
        con.execute(
            'UPDATE items SET status=?, selected_option_id=NULL WHERE id=?',
            (status, item_id),
        )
        con.execute(
            '''INSERT INTO item_events(item_id, actor, action)
               VALUES(?,?,?)''',
            (item_id, '老人', action),
        )
        return {'ok': True, 'status': status, 'reminder_available': False}

    if action == '工作人员回复' and row['direction'] == '上传':
        feedback = text_field(data, 'feedback', 2000, required=True)
        status = '工作人员已回复'
        con.execute(
            'UPDATE items SET feedback=?, status=? WHERE id=?',
            (feedback, status, item_id),
        )
        con.execute(
            '''INSERT INTO item_events(item_id, actor, action, detail)
               VALUES(?,?,?,?)''',
            (item_id, '工作人员', action, feedback),
        )
        return {'ok': True, 'status': status}

    raise ValueError('操作不适用于此事项')


def create_reminder(con, data, now=None):
    current = now or now_local()
    item_id = positive_int(data.get('item_id'), '事项编号')
    row = con.execute('SELECT * FROM items WHERE id=?', (item_id,)).fetchone()
    if row is None:
        return None
    if row['direction'] != '下达' or not row['reminder_enabled'] or not row['event_time']:
        raise ValueError('此事项不能设置提醒')
    if not row['selected_option_id']:
        raise ValueError('请先保存一个可设置提醒的选择')
    option = con.execute(
        'SELECT * FROM item_options WHERE id=? AND item_id=?',
        (row['selected_option_id'], item_id),
    ).fetchone()
    if option is None or option['kind'] != 'accept':
        raise ValueError('当前选择不能设置提醒')
    placeholders = ','.join('?' for _ in REMINDER_ACTIVE_STATUSES)
    existing = con.execute(
        f'''SELECT id FROM reminders
            WHERE item_id=? AND status IN ({placeholders}) LIMIT 1''',
        (item_id, *REMINDER_ACTIVE_STATUSES),
    ).fetchone()
    if existing:
        raise ValueError('此事项已有未完成的提醒')

    event_at = parse_time(row['event_time'], '事项时间')
    if 'offset_minutes' in data:
        offset = data['offset_minutes']
        if isinstance(offset, bool) or not isinstance(offset, int) or offset not in REMINDER_OFFSETS:
            raise ValueError('提醒时间方案不正确')
        scheduled = event_at - timedelta(minutes=offset)
    else:
        remind_at = text_field(data, 'remind_at', 32, required=True)
        scheduled = parse_time(remind_at, '提醒时间')
    if scheduled <= current:
        raise ValueError('提醒时间必须晚于当前时间')
    if scheduled > event_at:
        raise ValueError('提醒时间不能晚于事项时间')

    scheduled_text = iso_time(scheduled)
    cur = con.execute(
        '''INSERT INTO reminders(
               item_id, option_id, scheduled_for, status, next_action_at)
           VALUES(?,?,?,'scheduled',?)''',
        (item_id, option['id'], scheduled_text, scheduled_text),
    )
    reminder_id = cur.lastrowid
    con.execute(
        '''INSERT INTO reminder_events(reminder_id, action, detail)
           VALUES(?, 'scheduled', ?)''',
        (reminder_id, f'计划提醒时间：{scheduled_text}'),
    )
    con.execute(
        '''INSERT INTO item_events(item_id, actor, action, detail)
           VALUES(?, '知应提醒助手', '提醒已设置', ?)''',
        (item_id, scheduled_text),
    )
    return {
        'id': reminder_id,
        'item_id': item_id,
        'status': 'scheduled',
        'scheduled_for': scheduled_text,
    }


def add_escalation(con, reminder, current, detail):
    stamp = iso_time(current)
    if reminder['escalated_at']:
        return
    con.execute(
        'UPDATE reminders SET escalated_at=? WHERE id=?',
        (stamp, reminder['id']),
    )
    con.execute(
        '''INSERT INTO reminder_events(reminder_id, action, detail)
           VALUES(?, 'escalated', ?)''',
        (reminder['id'], detail),
    )
    con.execute(
        '''INSERT INTO item_events(item_id, actor, action, detail)
           VALUES(?, '知应提醒助手', '需要工作人员跟进', ?)''',
        (reminder['item_id'], detail),
    )


def advance_reminders(con, now=None):
    current = now or now_local()
    stamp = iso_time(current)
    grace = iso_time(current + timedelta(minutes=REMINDER_UNCLAIMED_GRACE_MINUTES))
    retry_at = iso_time(current + timedelta(minutes=REMINDER_RETRY_MINUTES))
    transitions = []

    scheduled = con.execute(
        '''SELECT * FROM reminders
           WHERE status='scheduled' AND scheduled_for<=? ORDER BY id''',
        (stamp,),
    ).fetchall()
    for reminder in scheduled:
        con.execute(
            '''UPDATE reminders
               SET status='due', due_at=COALESCE(due_at, ?), next_action_at=?
               WHERE id=?''',
            (stamp, grace, reminder['id']),
        )
        con.execute(
            '''INSERT INTO reminder_events(reminder_id, action, detail)
               VALUES(?, 'due', '提醒已到期，等待页面确认展示')''',
            (reminder['id'],),
        )
        transitions.append((reminder['id'], 'due'))

    unclaimed = con.execute(
        '''SELECT * FROM reminders
           WHERE status='due' AND next_action_at IS NOT NULL AND next_action_at<=?
           ORDER BY id''',
        (stamp,),
    ).fetchall()
    for reminder in unclaimed:
        con.execute(
            "UPDATE reminders SET status='overdue', next_action_at=NULL WHERE id=?",
            (reminder['id'],),
        )
        add_escalation(
            con, reminder, current,
            '提醒到期后未确认展示；页面可能未打开，请工作人员关注。',
        )
        transitions.append((reminder['id'], 'overdue'))

    presented = con.execute(
        '''SELECT * FROM reminders
           WHERE status='presented' AND next_action_at IS NOT NULL AND next_action_at<=?
           ORDER BY id''',
        (stamp,),
    ).fetchall()
    for reminder in presented:
        if reminder['presentation_count'] < REMINDER_MAX_PRESENTATIONS:
            con.execute(
                "UPDATE reminders SET status='due', next_action_at=? WHERE id=?",
                (retry_at, reminder['id']),
            )
            con.execute(
                '''INSERT INTO reminder_events(reminder_id, action, detail)
                   VALUES(?, 'retry', '老人尚未回应，等待有限重试')''',
                (reminder['id'],),
            )
            transitions.append((reminder['id'], 'due'))
        else:
            con.execute(
                "UPDATE reminders SET status='overdue', next_action_at=NULL WHERE id=?",
                (reminder['id'],),
            )
            add_escalation(
                con, reminder, current,
                '提醒已展示但老人尚未回应；请工作人员实际跟进。',
            )
            transitions.append((reminder['id'], 'overdue'))
    return transitions


def reminder_with_context(con, reminder_id):
    reminder = con.execute(
        'SELECT * FROM reminders WHERE id=?', (reminder_id,)
    ).fetchone()
    if reminder is None:
        return None
    item = items_with_events(
        con,
        con.execute('SELECT * FROM items WHERE id=?', (reminder['item_id'],)).fetchall(),
    )[0]
    return {'reminder': dict(reminder), 'item': item}


def claim_reminder(con, data, now=None):
    current = now or now_local()
    advance_reminders(con, current)
    raw_excluded = data.get('exclude_ids', [])
    if not isinstance(raw_excluded, list) or len(raw_excluded) > 100:
        raise ValueError('已显示提醒列表不正确')
    excluded = []
    for value in raw_excluded:
        excluded.append(positive_int(value, '提醒编号'))

    params = []
    if excluded:
        placeholders = ','.join('?' for _ in excluded)
        status_clause = (
            "(status='due' OR "
            f"(status IN ('overdue','overdue_presented') AND id NOT IN ({placeholders})))"
        )
        params.extend(excluded)
    else:
        status_clause = "status IN ('due','overdue','overdue_presented')"
    reminder = con.execute(
        f'''SELECT * FROM reminders
            WHERE {status_clause}
            ORDER BY CASE status WHEN 'due' THEN 0 ELSE 1 END,
                     scheduled_for, id LIMIT 1''',
        params,
    ).fetchone()
    if reminder is None:
        return {'reminder': None}

    stamp = iso_time(current)
    if reminder['status'] == 'due':
        count = reminder['presentation_count'] + 1
        next_action = iso_time(current + timedelta(minutes=REMINDER_RETRY_MINUTES))
        con.execute(
            '''UPDATE reminders
               SET status='presented', presentation_count=?,
                   last_presented_at=?, next_action_at=?
               WHERE id=?''',
            (count, stamp, next_action, reminder['id']),
        )
        action = 'presented'
        detail = f'页面已展示第{count}次提醒'
    else:
        con.execute(
            '''UPDATE reminders
               SET status='overdue_presented', last_presented_at=?, next_action_at=NULL
               WHERE id=?''',
            (stamp, reminder['id']),
        )
        action = 'overdue_presented'
        detail = '页面补显逾期提醒；此前未确认送达或回应'
    con.execute(
        'INSERT INTO reminder_events(reminder_id, action, detail) VALUES(?,?,?)',
        (reminder['id'], action, detail),
    )
    return reminder_with_context(con, reminder['id'])


def respond_to_reminder(con, data, now=None):
    current = now or now_local()
    reminder_id = positive_int(data.get('id'), '提醒编号')
    action = data.get('action')
    if action not in REMINDER_RESPONSE_ACTIONS:
        raise ValueError('提醒操作不正确')
    reminder = con.execute(
        'SELECT * FROM reminders WHERE id=?', (reminder_id,)
    ).fetchone()
    if reminder is None:
        return None
    if reminder['status'] not in {'presented', 'overdue_presented'}:
        raise ValueError('提醒当前不能执行此操作')
    stamp = iso_time(current)

    if action == 'acknowledge':
        con.execute(
            '''UPDATE reminders SET status='acknowledged', resolved_at=?, next_action_at=NULL
               WHERE id=?''',
            (stamp, reminder_id),
        )
        detail = '老人已确认看到本次提醒；不表示事项已经完成'
        status = 'acknowledged'
    elif action == 'snooze':
        scheduled = iso_time(current + timedelta(minutes=10))
        con.execute(
            '''UPDATE reminders
               SET status='scheduled', scheduled_for=?, next_action_at=?,
                   due_at=NULL, resolved_at=NULL
               WHERE id=?''',
            (scheduled, scheduled, reminder_id),
        )
        detail = f'老人选择十分钟后再提醒：{scheduled}'
        status = 'scheduled'
    else:
        con.execute(
            '''UPDATE reminders SET status='help_requested', resolved_at=?, next_action_at=NULL
               WHERE id=?''',
            (stamp, reminder_id),
        )
        detail = '老人请求工作人员帮助；系统只生成跟进记录，不表示已获得帮助'
        status = 'help_requested'
        con.execute(
            '''INSERT INTO item_events(item_id, actor, action, detail)
               VALUES(?, '老人', '提醒时请求帮助', ?)''',
            (reminder['item_id'], detail),
        )
    con.execute(
        'INSERT INTO reminder_events(reminder_id, action, detail) VALUES(?,?,?)',
        (reminder_id, action, detail),
    )
    return {'ok': True, 'id': reminder_id, 'status': status, 'detail': detail}


def items_with_events(con, rows):
    items = [dict(row) for row in rows]
    if not items:
        return items
    ids = [item['id'] for item in items]
    placeholders = ','.join('?' for _ in ids)
    events = con.execute(
        f'''SELECT id, item_id, actor, action, detail, created_at
            FROM item_events WHERE item_id IN ({placeholders}) ORDER BY id''',
        ids,
    ).fetchall()
    options = con.execute(
        f'''SELECT id, item_id, label, kind, sort_order
            FROM item_options WHERE item_id IN ({placeholders})
            ORDER BY item_id, sort_order, id''',
        ids,
    ).fetchall()
    images = con.execute(
        f'''SELECT item_id, mime_type, filename, source
            FROM item_images WHERE item_id IN ({placeholders})''',
        ids,
    ).fetchall()
    reminders = con.execute(
        f'''SELECT * FROM reminders WHERE item_id IN ({placeholders})
            ORDER BY item_id, id DESC''',
        ids,
    ).fetchall()
    reminder_ids = [reminder['id'] for reminder in reminders]
    reminder_events = []
    if reminder_ids:
        reminder_placeholders = ','.join('?' for _ in reminder_ids)
        reminder_events = con.execute(
            f'''SELECT id, reminder_id, action, detail, created_at
                FROM reminder_events
                WHERE reminder_id IN ({reminder_placeholders}) ORDER BY id''',
            reminder_ids,
        ).fetchall()

    grouped_events = {item_id: [] for item_id in ids}
    grouped_options = {item_id: [] for item_id in ids}
    grouped_images = {image['item_id']: dict(image) for image in images}
    grouped_reminders = {item_id: [] for item_id in ids}
    grouped_reminder_events = {reminder_id: [] for reminder_id in reminder_ids}
    for event in events:
        grouped_events[event['item_id']].append(dict(event))
    for option in options:
        grouped_options[option['item_id']].append(dict(option))
    for event in reminder_events:
        grouped_reminder_events[event['reminder_id']].append(dict(event))
    for reminder in reminders:
        value = dict(reminder)
        value['events'] = grouped_reminder_events[reminder['id']]
        grouped_reminders[reminder['item_id']].append(value)
    for item in items:
        item.pop('explanation', None)
        item.pop('explanation_source', None)
        item['events'] = grouped_events[item['id']]
        item['options'] = grouped_options[item['id']]
        item['reminders'] = grouped_reminders[item['id']]
        image = grouped_images.get(item['id'])
        item['image_url'] = f"/api/items/{item['id']}/image" if image else ''
        item['image_filename'] = image['filename'] if image else ''
        item['image_mime_type'] = image['mime_type'] if image else ''
        item['image_source'] = image['source'] if image else 'automatic'
        item['reminder_enabled'] = bool(item['reminder_enabled'])
    return items


class ReminderWorker(threading.Thread):
    def __init__(self, interval_seconds=5):
        super().__init__(name='zhiying-reminder-agent', daemon=True)
        self.interval_seconds = interval_seconds
        self.stop_event = threading.Event()

    def run(self):
        while not self.stop_event.is_set():
            try:
                with connect() as con:
                    advance_reminders(con)
            except sqlite3.Error as error:
                print(f'知应提醒助手数据库错误：{error}', flush=True)
            self.stop_event.wait(self.interval_seconds)

    def stop(self):
        self.stop_event.set()


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, value):
        body = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def reply_bytes(self, code, body, content_type):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/api/avatar-runtime':
            return self.reply(200, avatar_runtime_snapshot())
        if path == '/api/ai/status':
            return self.reply(200, doubao.capability_status())
        if path == '/api/items':
            with connect() as con:
                rows = con.execute('SELECT * FROM items ORDER BY id DESC').fetchall()
                items = items_with_events(con, rows)
            return self.reply(200, items)
        if path.startswith('/api/items/') and path.endswith('/image'):
            raw_id = path.removeprefix('/api/items/').removesuffix('/image').rstrip('/')
            if not raw_id.isdigit() or int(raw_id) <= 0:
                return self.reply(404, {'error': '图片不存在'})
            with connect() as con:
                image = con.execute(
                    'SELECT mime_type, data FROM item_images WHERE item_id=?',
                    (int(raw_id),),
                ).fetchone()
            if image is None:
                return self.reply(404, {'error': '图片不存在'})
            return self.reply_bytes(200, image['data'], image['mime_type'])
        if path.startswith('/api/items/'):
            raw_id = path.removeprefix('/api/items/')
            if not raw_id.isdigit() or int(raw_id) <= 0:
                return self.reply(404, {'error': '事项不存在'})
            with connect() as con:
                rows = con.execute('SELECT * FROM items WHERE id=?', (int(raw_id),)).fetchall()
                items = items_with_events(con, rows)
            if not items:
                return self.reply(404, {'error': '事项不存在'})
            return self.reply(200, items[0])
        static_file = STATIC_FILES.get(path)
        if static_file is None:
            return self.reply(404, {'error': '页面不存在'})
        filename, content_type = static_file
        body = (ROOT / filename).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            path = urlsplit(self.path).path
            length = int(self.headers.get('Content-Length', '0'))
            maximum = MAX_ITEM_REQUEST_BYTES if path == '/api/items' else 20000
            if not 0 < length <= maximum:
                return self.reply(400, {'error': '内容长度不正确'})
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('请求格式不正确')
            if path == '/api/avatar-runtime':
                return self.reply(200, update_avatar_runtime(data))
            if path == '/api/ai/image':
                return self.reply(200, generate_ai_image(data))
            if path == '/api/voice/chat':
                return self.reply(200, generate_voice_reply(data))
            with connect() as con:
                if path == '/api/items':
                    result = create_item(con, data)
                elif path == '/api/respond':
                    result = respond_to_item(con, data)
                    if result is None:
                        return self.reply(404, {'error': '事项不存在'})
                elif path == '/api/reminders':
                    result = create_reminder(con, data)
                    if result is None:
                        return self.reply(404, {'error': '事项不存在'})
                elif path == '/api/reminders/claim':
                    result = claim_reminder(con, data)
                elif path == '/api/reminders/respond':
                    result = respond_to_reminder(con, data)
                    if result is None:
                        return self.reply(404, {'error': '提醒不存在'})
                else:
                    return self.reply(404, {'error': '接口不存在'})
            return self.reply(200, result)
        except doubao.DoubaoError as error:
            return self.reply(error.status, {'error': str(error), 'code': error.code})
        except (ValueError, TypeError, json.JSONDecodeError, sqlite3.IntegrityError) as error:
            message = str(error) if isinstance(error, ValueError) else '输入有误，请检查内容和操作'
            return self.reply(400, {'error': message})

    def do_DELETE(self):
        path = urlsplit(self.path).path
        if not path.startswith('/api/items/'):
            return self.reply(404, {'error': '接口不存在'})
        raw_id = path.removeprefix('/api/items/').rstrip('/')
        if not raw_id.isdigit() or int(raw_id) <= 0:
            return self.reply(404, {'error': '事项不存在'})
        with connect() as con:
            deleted = delete_item(con, int(raw_id))
        if not deleted:
            return self.reply(404, {'error': '事项不存在'})
        return self.reply(200, {'deleted': int(raw_id)})


if __name__ == '__main__':
    initialize()
    with connect() as connection:
        advance_reminders(connection)
    worker = ReminderWorker()
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    worker.start()
    print(f'知应： http://127.0.0.1:{PORT}', flush=True)
    try:
        server.serve_forever()
    finally:
        server.shutdown()
        server.server_close()
        worker.stop()
        worker.join(timeout=2)
