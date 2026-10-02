import os
import uvicorn
from .api import create_app
from .config import require_free_threading

require_free_threading()
# One dispatcher per database. Free-threaded workers handle synchronous work.
uvicorn.run(create_app(), host="127.0.0.1", port=int(os.environ.get("ATHENA_PORT", "4317")), loop="asyncio", http="h11", access_log=False)
