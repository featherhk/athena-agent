"""Lossless event storage. The public event vocabulary and sequence stay stable."""
import json
import zlib

CHECKPOINT_INTERVAL = 32
MAX_DECODE_BYTES = 32 * 1024 * 1024
PAGE_CHARS = 2 * 1024 * 1024
JSON = 'json'
ZLIB = 'json-zlib-v1'
PREFIX = 'message-prefix-v1'


def _json(value):
    return json.dumps(value, ensure_ascii=False)


def _checkpoint(raw):
    encoded = raw.encode('utf-8')
    if len(encoded) > MAX_DECODE_BYTES:
        raise ValueError('Message event exceeds its recoverable storage limit')
    if len(encoded) >= 512:
        return ZLIB, zlib.compress(encoded, level=3)
    return JSON, raw


def _message_id(payload):
    stream = payload.get('id') if isinstance(payload, dict) else None
    return stream if isinstance(stream, str) and 0 < len(stream) <= 80 else None


def _encode_message(raw, payload, head=None):
    encoding, stored = _checkpoint(raw)
    base, depth = None, 0
    if head and head['depth'] < CHECKPOINT_INTERVAL - 1:
        previous = json.loads(head['payload'])
        if all(isinstance(payload.get(field), str) and isinstance(previous.get(field), str)
               and payload[field].startswith(previous[field]) for field in ('content', 'reasoning')):
            delta = {**payload, 'content': payload['content'][len(previous['content']):],
                     'reasoning': payload['reasoning'][len(previous['reasoning']):]}
            patch = _json({'content_start': len(previous['content']), 'reasoning_start': len(previous['reasoning']), 'value': delta})
            if len(patch.encode()) + 32 < len(stored if isinstance(stored, bytes) else stored.encode()):
                encoding, stored, base, depth = PREFIX, patch, head['seq'], head['depth'] + 1
    return encoding, stored, base, depth


def append_event(db, session_id, kind, payload, created_at):
    raw = _json(payload)
    stream = _message_id(payload) if kind == 'message' else None
    head = None
    if stream:
        head = db.execute('SELECT * FROM message_event_heads WHERE session_id=? AND stream_id=?', (session_id, stream)).fetchone()
    encoding, stored, base, depth = _encode_message(raw, payload, head) if kind == 'message' else (JSON, raw, None, 0)
    cursor = db.execute('INSERT INTO events(session_id,type,payload,created_at,encoding,base_seq) VALUES (?,?,?,?,?,?)',
                       (session_id, kind, stored, created_at, encoding, base))
    sequence = cursor.lastrowid
    if stream:
        db.execute('INSERT INTO message_event_heads VALUES (?,?,?,?,?) ON CONFLICT(session_id,stream_id) DO UPDATE SET seq=excluded.seq,payload=excluded.payload,depth=excluded.depth',
                   (session_id, stream, sequence, raw, depth))
    return {'seq': sequence, 'session_id': session_id, 'type': kind, 'payload': payload, 'created_at': created_at}


def _inflate(raw):
    if not isinstance(raw, bytes):
        raise ValueError('Invalid compressed event payload')
    decoder = zlib.decompressobj()
    text = decoder.decompress(raw, MAX_DECODE_BYTES + 1)
    if len(text) > MAX_DECODE_BYTES or not decoder.eof or decoder.unused_data:
        raise ValueError('Invalid or oversized compressed event payload')
    return text.decode('utf-8')


def payload_text(db, row, cache=None, depth=0):
    """Recover the exact original JSON text, including old noncanonical JSON."""
    cache = {} if cache is None else cache
    if row['seq'] in cache:
        return cache[row['seq']]
    if depth >= CHECKPOINT_INTERVAL:
        raise ValueError('Event checkpoint chain is too long')
    encoding = row['encoding']
    if encoding == JSON:
        text = row['payload']
    elif encoding == ZLIB:
        text = _inflate(row['payload'])
    elif encoding == PREFIX:
        if row['type'] != 'message' or type(row['base_seq']) is not int or row['base_seq'] >= row['seq']:
            raise ValueError('Invalid event checkpoint reference')
        base = db.execute('SELECT * FROM events WHERE seq=? AND session_id=? AND type=\'message\'',
                          (row['base_seq'], row['session_id'])).fetchone()
        if base is None:
            raise ValueError('Missing event checkpoint in this conversation')
        before = json.loads(payload_text(db, base, cache, depth + 1))
        patch = json.loads(row['payload'])
        value = patch['value'].copy()
        if before.get('id') != value.get('id'):
            raise ValueError('Event checkpoint belongs to another message')
        for field in ('content', 'reasoning'):
            if not isinstance(before.get(field), str) or not isinstance(value.get(field), str) or len(before[field]) != patch[field + '_start']:
                raise ValueError('Event checkpoint offset mismatch')
            value[field] = before[field] + value[field]
        text = _json(value)
    else:
        raise ValueError('Unsupported event encoding')
    if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_DECODE_BYTES:
        raise ValueError('Invalid or oversized event payload')
    cache[row['seq']] = text
    return text


def read_events(db, session_id, after=0, limit=500):
    rows = db.execute('SELECT * FROM events WHERE session_id=? AND seq>? ORDER BY seq LIMIT ?',
                      (session_id, after, limit)).fetchall()
    result, cache, characters = [], {}, 0
    for row in rows:
        raw = payload_text(db, row, cache)
        if result and characters + len(raw) > PAGE_CHARS:
            break
        result.append({key: row[key] for key in ('seq', 'session_id', 'type', 'created_at')} | {'payload': json.loads(raw)})
        characters += len(raw)
    return result


def archive_legacy_batch(db, limit=500):
    """Safe to resume: each batch changes only losslessly encoded message events.

    Each batch starts new bounded checkpoint chains. Noncanonical JSON always
    gets an independent checkpoint to retain original whitespace and escaping.
    No history or source row is deleted, and a partial archive is readable.
    Caller supplies a transaction and controls when disk compaction is suitable.
    """
    rows = db.execute("SELECT seq,session_id,payload FROM events WHERE type='message' AND encoding='json' AND length(payload)>=512 ORDER BY seq LIMIT ?", (limit,)).fetchall()
    before = after = 0
    heads, cache = {}, {}
    for row in rows:
        raw, value = row['payload'], json.loads(row['payload'])
        original = raw.encode('utf-8')
        stream = _message_id(value)
        key = (row['session_id'], stream)
        canonical = stream is not None and raw == _json(value)
        encoding, packed, base, depth = _encode_message(raw, value, heads.get(key) if canonical else None)
        db.execute('UPDATE events SET payload=?,encoding=?,base_seq=? WHERE seq=? AND encoding=\'json\'', (packed, encoding, base, row['seq']))
        recovered = payload_text(db, db.execute('SELECT * FROM events WHERE seq=?', (row['seq'],)).fetchone(), cache)
        if recovered.encode('utf-8') != original:
            raise ValueError('Event archival verification failed')
        if canonical:
            heads[key] = {'seq': row['seq'], 'payload': raw, 'depth': depth}
        before += len(original)
        after += len(packed if isinstance(packed, bytes) else packed.encode('utf-8'))
    return {'events': len(rows), 'before_bytes': before, 'after_bytes': after}
