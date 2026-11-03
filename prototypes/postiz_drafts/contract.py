"""Narrow subset of pinned Postiz v2.25.3; no generated UI or provider calls."""
import hashlib
import html
import json
import re

WRITE = 'schedulePostTool'
READBACK = 'postsListTool'
# Required upstream field, frozen metadata rather than a user scheduling input.
DATE = '2026-01-01T00:00:00Z'
WINDOW = {'startDate': '2025-12-31T00:00:00', 'endDate': '2026-01-02T00:00:00'}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def payload_for(text, channel):
    if not isinstance(text, str) or not 0 < len(text) <= 3000:
        raise ValueError('Unsupported final copy')
    content = ''.join('<p>' + html.escape(line, quote=True) + '</p>' for line in text.splitlines())
    return {'socialPost': [{'integrationId': channel, 'isPremium': False,
        'date': DATE, 'shortLink': False, 'type': 'draft',
        'postsAndComments': [{'content': content, 'attachments': []}], 'settings': []}]}


def validate_payload(payload, channel):
    if not isinstance(payload, dict) or set(payload) != {'socialPost'}:
        raise ValueError('Draft payload denied')
    posts = payload['socialPost']
    if not isinstance(posts, list) or len(posts) != 1 or not isinstance(posts[0], dict):
        raise ValueError('Draft batch denied')
    p = posts[0]
    if (set(p) != {'integrationId', 'isPremium', 'date', 'shortLink', 'type', 'postsAndComments', 'settings'}
            or p['integrationId'] != channel or p['isPremium'] is not False
            or p['shortLink'] is not False or p['date'] != DATE
            or p['type'] != 'draft' or p['settings'] != []):
        raise ValueError('Draft operation denied')
    parts = p['postsAndComments']
    if (not isinstance(parts, list) or len(parts) != 1 or not isinstance(parts[0], dict)
            or set(parts[0]) != {'content', 'attachments'} or parts[0]['attachments'] != []
            or not isinstance(parts[0]['content'], str) or not parts[0]['content']
            or len(parts[0]['content'].encode()) > 24000
            or not re.fullmatch(r'(?:<p>[^<>]*</p>)+', parts[0]['content'])):
        raise ValueError('Draft content denied')


def envelope(result):
    if result.isError or len(result.content) != 1 or result.content[0].type != 'text':
        raise ValueError('Invalid draft response')
    value = json.loads(result.content[0].text)
    if (not isinstance(value, dict) or set(value) != {'output'}
            or (result.structuredContent is not None and result.structuredContent != value)):
        raise ValueError('Invalid draft response')
    return value['output']


def write_result(value, channel):
    if (not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict)
            or set(value[0]) != {'postId', 'integration', 'previewUrl'}
            or value[0]['integration'] != channel
            or not isinstance(value[0]['postId'], str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value[0]['postId'])):
        raise ValueError('Uncertain draft response')
    # Never expose/follow server-supplied preview URLs.
    return value[0]['postId']
