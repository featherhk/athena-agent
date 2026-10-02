"""Drain long-lived client streams before Uvicorn waits for HTTP completion."""
import uvicorn


class AthenaServer(uvicorn.Server):
    async def shutdown(self, sockets=None):
        # ASGI lifespan teardown runs after HTTP draining. Waiting until teardown
        # to close SSE therefore deadlocks against Uvicorn's graceful timeout.
        engine = getattr(self.config.app.state, "engine", None)
        if engine is not None:
            engine.begin_shutdown()
        await super().shutdown(sockets=sockets)
