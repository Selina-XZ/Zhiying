import base64
import binascii
import json
import os
import re
import threading
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ARK_BASE_URL = 'https://ark.cn-beijing.volces.com/api/v3'
DEFAULT_IMAGE_MODEL = 'doubao-seedream-5-0-flash-260915'
DEFAULT_TEXT_MODEL = 'doubao-seed-2-1-lite-260915'
MAX_PROVIDER_RESPONSE_BYTES = 3_000_000
MAX_GENERATED_IMAGE_BYTES = 1_572_864
MAX_REPLY_CHARS = 3000
MODEL_PATTERN = re.compile(r'^[A-Za-z0-9._:-]{1,200}$')
GENERATION_LOCK = threading.Lock()

ZHIYING_SYSTEM_PROMPT = (
    '你是"知应"，社区养老服务的数字人助手，不是豆包，也不是字节跳动的通用助手。'
    '服务对象是老年人。请始终用普通话、短句、亲切自然的语气，一次只说一个重点，不要长篇大论；'
    '先听清老人说的话，再慢慢确认，不抢话，不代替老人做决定。'
    '介绍自己时说：我是知应，社区里帮助您听明白事情、做好选择的助手。'
    '关于事项和提醒：只讲清工作人员下达的正文、时间、地点和老人可以怎么选择；'
    '"确认收到"不等于老人接受了安排，也不等于事情办完；老人没有回应时不算同意，不要替老人说"已经同意"；'
    '提醒只有真的保存成功才是提醒，不要承诺一个不存在的闹钟。'
    '关于日常生活和照护，你可以提供一般性、安全的健康常识：'
    '提醒按时吃医生已经开好的药、提醒多喝水、饮食清淡、适当活动、防跌倒防烫伤、天冷保暖、规律作息；'
    '可以安抚情绪、陪老人说话，也可以在老人需要时提醒他联系家人、社区或医生。'
    '关于健康和求助，你必须守住以下红线：'
    '老人说身体不舒服时，只按原话转交工作人员，不诊断病情、不判断病因、不给用药建议，也不说老人得了什么病；'
    '不推荐、不更改任何药物或剂量，不提供医疗结论；'
    '老人求助时只说会生成工作人员待办，不能说已经帮老人办好。'
    '关于真实性：只讲工作人员提供的虚构演示内容和事实，不编造时间、地点、人物或承诺；'
    '不确定时就说可以帮老人转告工作人员，不假装知道；不要自称豆包、字节跳动或任何外部模型。'
)


class DoubaoError(Exception):
    def __init__(self, code, message, status=502):
        super().__init__(message)
        self.code = code
        self.status = status


class DoubaoNotConfigured(DoubaoError):
    def __init__(self, capability='图片'):
        super().__init__('not_configured', f'豆包{capability}能力尚未配置', 503)


def _settings():
    api_key = os.environ.get('ARK_API_KEY', '').strip()
    image_model = os.environ.get('ARK_IMAGE_MODEL', DEFAULT_IMAGE_MODEL).strip()
    text_model = os.environ.get('ARK_TEXT_MODEL', DEFAULT_TEXT_MODEL).strip()
    return api_key, image_model, text_model


def _valid_model(value):
    return bool(MODEL_PATTERN.fullmatch(value or ''))


def capability_status():
    api_key, image_model, text_model = _settings()
    return {
        'image_ready': bool(api_key and _valid_model(image_model)),
        'image_model': image_model if _valid_model(image_model) else '',
        'text_ready': bool(api_key and _valid_model(text_model)),
        'text_model': text_model if _valid_model(text_model) else '',
    }


def _provider_error(status):
    if status in {401, 403}:
        return DoubaoError('authentication_failed', '豆包鉴权失败，请检查服务端配置', 502)
    if status == 429:
        return DoubaoError('rate_limited', '豆包请求过于频繁，请稍后重试', 503)
    if status == 400:
        return DoubaoError('request_rejected', '豆包拒绝了本次请求', 502)
    if status >= 500:
        return DoubaoError('provider_unavailable', '豆包服务暂时不可用', 503)
    return DoubaoError('provider_error', '豆包请求失败', 502)


def _post_json(path, payload, timeout, opener=None, capability='图片'):
    api_key, _, _ = _settings()
    if not api_key:
        raise DoubaoNotConfigured(capability)
    body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    request = Request(
        f'{ARK_BASE_URL}{path}',
        data=body,
        method='POST',
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
    )
    transport = opener or urlopen
    try:
        with transport(request, timeout=timeout) as response:
            raw = response.read(MAX_PROVIDER_RESPONSE_BYTES + 1)
    except HTTPError as error:
        raise _provider_error(error.code) from None
    except (URLError, TimeoutError, OSError):
        raise DoubaoError('network_error', '无法连接豆包服务', 503) from None
    if len(raw) > MAX_PROVIDER_RESPONSE_BYTES:
        raise DoubaoError('response_too_large', '豆包返回内容过大', 502)
    try:
        value = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise DoubaoError('invalid_response', '豆包返回格式不正确', 502) from None
    if not isinstance(value, dict):
        raise DoubaoError('invalid_response', '豆包返回格式不正确', 502)
    return value


def _image_prompt(facts):
    lines = [
        '请生成一张适合老年人查看的社区服务辅助图片。',
        '使用暖色、低饱和配色，构图简洁，主体清楚，低信息密度。',
        '不要生成任何文字、数字、二维码、联系方式、标识或真实人物肖像。',
        '图片只辅助理解，不能添加工作人员未提供的事实。',
        f"类别：{facts['category']}",
        f"工作人员原始正文：{facts['content']}",
    ]
    if facts.get('event_time'):
        lines.append(f"事项时间背景：{facts['event_time']}（不要在图中绘制文字或数字）")
    if facts.get('location'):
        lines.append(f"事项地点背景：{facts['location']}（不要在图中绘制文字）")
    return '\n'.join(lines)


def _decode_image(response):
    try:
        encoded = response['data'][0]['b64_json']
        data = base64.b64decode(encoded, validate=True)
    except (KeyError, IndexError, TypeError, ValueError, binascii.Error):
        raise DoubaoError('invalid_image', '豆包图片返回格式不正确', 502) from None
    if not data.startswith(b'\xff\xd8\xff'):
        raise DoubaoError('invalid_image', '豆包返回的图片格式不正确', 502)
    if len(data) > MAX_GENERATED_IMAGE_BYTES:
        raise DoubaoError('image_too_large', '豆包生成图片超过项目大小限制', 502)
    return {
        'filename': 'doubao-generated.jpg',
        'mime_type': 'image/jpeg',
        'data_base64': base64.b64encode(data).decode('ascii'),
    }


def generate_image(facts, *, opener=None):
    api_key, image_model, _ = _settings()
    if not api_key or not _valid_model(image_model):
        raise DoubaoNotConfigured()
    if not GENERATION_LOCK.acquire(blocking=False):
        raise DoubaoError('busy', '豆包正在生成上一张图片，请稍后再试', 409)
    try:
        response = _post_json(
            '/images/generations',
            {
                'model': image_model,
                'prompt': _image_prompt(facts),
                'size': '1K',
                'response_format': 'b64_json',
                'output_format': 'jpeg',
                'watermark': True,
            },
            90,
            opener,
        )
        return _decode_image(response)
    finally:
        GENERATION_LOCK.release()


def _decode_chat_reply(response):
    try:
        reply = response['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError):
        raise DoubaoError('invalid_response', '豆包返回格式不正确', 502) from None
    if not isinstance(reply, str):
        raise DoubaoError('invalid_response', '豆包返回格式不正确', 502)
    reply = reply.strip()
    if not reply:
        raise DoubaoError('empty_reply', '豆包没有返回回答', 502)
    if len(reply) > MAX_REPLY_CHARS:
        reply = reply[:MAX_REPLY_CHARS]
    return reply


def generate_chat_reply(user_text, *, opener=None):
    api_key, _, text_model = _settings()
    if not api_key or not _valid_model(text_model):
        raise DoubaoNotConfigured('对话')
    text = str(user_text or '').strip()
    if not text:
        raise DoubaoError('empty_request', '没有可生成回答的内容', 400)
    if len(text) > 2000:
        raise DoubaoError('request_too_long', '问题内容过长', 400)
    response = _post_json(
        '/chat/completions',
        {
            'model': text_model,
            'messages': [
                {'role': 'system', 'content': ZHIYING_SYSTEM_PROMPT},
                {'role': 'user', 'content': text},
            ],
            'thinking': {'type': 'disabled'},
        },
        60,
        opener,
        '对话',
    )
    return _decode_chat_reply(response)
