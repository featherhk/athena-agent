"""One-owner, non-retrying RPC over the native parent's private stdio pipes."""
import asyncio
import json
import sys
import time
import uuid


class NativeHost:
    def __init__(self, reader, emit):
        self.reader, self.emit = reader, emit
        self.pending = {}
        self.connected = True
        self.task = asyncio.create_task(self._read())

    @classmethod
    async def stdio(cls):
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader(limit=1_048_576)
        await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer)
        # Nonblocking, ordered writes. A full parent pipe must not freeze Stop
        # or the event loop when a long Unicode text value is being sent.
        transport, _ = await loop.connect_write_pipe(asyncio.Protocol, sys.stdout.buffer)
        def emit(value):
            if transport.is_closing() or transport.get_write_buffer_size() > 262144:
                raise ConnectionError('Native write channel unavailable.')
            transport.write((json.dumps(value, ensure_ascii=False) + '\n').encode())
        host = cls(reader, emit)
        host.transport = transport
        return host

    async def _read(self):
        try:
            while raw := await self.reader.readline():
                value = json.loads(raw)
                if not isinstance(value, dict) or value.get('event') != 'computer_result':
                    continue
                future = self.pending.get(value.get('id'))
                if future is not None and not future.done():
                    future.set_result(value.get('result'))
        except (ValueError, TypeError, OSError):
            pass
        finally:
            self.connected = False
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(ConnectionError('Native host connection closed; outcome unknown.'))

    async def call(self, args, *, timeout=12):
        if not self.connected:
            raise ConnectionError('Native host is disconnected.')
        identifier = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[identifier] = future
        try:
            self.emit({'event': 'computer_request', 'id': identifier,
                       'expires_at': time.time() + timeout - 1, 'args': args})
            # A lost response may follow a completed action. Never reconnect/replay.
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(identifier, None)
            if future.cancelled() and self.connected:
                try:
                    self.emit({'event': 'computer_cancel', 'id': identifier})
                except (OSError, ConnectionError):
                    self.connected = False

    async def close(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        if transport := getattr(self, 'transport', None):
            transport.close()
