import os
import uvicorn
from .api import create_app
from .config import Config, require_free_threading
from .server import AthenaServer

require_free_threading()
# One dispatcher per database. Free-threaded workers handle synchronous work.
config = Config.from_env()
AthenaServer(uvicorn.Config(create_app(config), host=config.bind_host, port=int(os.environ.get("ATHENA_PORT", "4317")),
             proxy_headers=False, loop="asyncio", http="h11", access_log=False, timeout_graceful_shutdown=5)).run()
