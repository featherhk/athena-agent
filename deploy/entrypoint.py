"""One explicit process role; an agent worker never starts the account service."""
import os
import uvicorn
from athena.config import require_free_threading
require_free_threading()
if os.environ.get('ATHENA_PROCESS') == 'gateway':
    from athena.gateway import GatewayConfig,create_gateway
    application=create_gateway(GatewayConfig.from_env())
    port=4321
else:
    from athena.api import create_app
    application=create_app()
    port=4317
uvicorn.run(application,host='0.0.0.0',port=port,loop='asyncio',http='h11',access_log=False,proxy_headers=False,timeout_graceful_shutdown=10)
