"""A bounded-latency writer; every received character survives normal teardown."""
import asyncio

FLUSH_INTERVAL = 0.05


class StreamingMessage:
    def __init__(self, store, session_id, run_id, message_id, broadcast, interval=FLUSH_INTERVAL):
        self.store, self.session_id = store, session_id
        self.run_id, self.message_id, self.broadcast = run_id, message_id, broadcast
        self.content = self.reasoning = ''
        self.saved = ('', '')
        self.closed, self.interval = False, interval
        self.wake, self.finished, self.lock = asyncio.Event(), asyncio.Event(), asyncio.Lock()
        self.worker = asyncio.create_task(self._work(), name=f'athena-stream-{message_id}')

    async def append(self, field, delta):
        if self.closed:
            raise RuntimeError('Cannot append to a closed message stream')
        if self.worker.done():
            await self.worker  # Propagate persistence failures to the source run.
        if field == 'content':
            first = not self.content
            self.content += delta
        elif field == 'reasoning':
            first = not self.reasoning
            self.reasoning += delta
        else:
            raise ValueError('Unknown stream field')
        self.wake.set()
        if first:
            await self.flush()  # Both first reasoning and first answer appear immediately.

    async def flush(self):
        async with self.lock:
            current = (self.content, self.reasoning)
            if current == self.saved:
                return
            payload = {'id': self.message_id, 'role': 'assistant', 'content': current[0],
                       'reasoning': current[1], 'status': 'streaming', 'run_id': self.run_id}
            event = await self.store.stream_message(self.session_id, payload)
            self.saved = current
            self.broadcast(event)

    async def _work(self):
        while True:
            await self.wake.wait()
            if not self.closed:
                try:
                    await asyncio.wait_for(self.finished.wait(), self.interval)
                except TimeoutError:
                    pass
            self.wake.clear()
            await self.flush()
            if self.closed:
                return

    async def close(self):
        self.closed = True
        self.finished.set()
        self.wake.set()
        # Cancellation of the model request must not cancel the final disk write.
        cancelled = False
        while not self.worker.done():
            try:
                await asyncio.shield(self.worker)
            except asyncio.CancelledError:
                cancelled = True
        self.worker.result()
        if cancelled:
            raise asyncio.CancelledError
