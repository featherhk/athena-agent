"""Account and metering control plane, isolated from all agent execution.

Run behind TLS. This process never imports Engine or executes user code. Operator
keys exist only here; workers and desktop clients send their own account tokens.
"""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlparse

import httpx
import anyio
from fastapi import FastAPI, Request, HTTPException
from fastapi import Path as APIPath
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from .auth import Auth, token_hash
from .config import require_free_threading
from .credits import Credits, initialize_credits
from .errors import ServiceError, UserError
from .store import Store, now, uid
from .payment_provider import RechargeConfig, load_recharge_config
from .recharge import Recharge, initialize_recharge


@dataclass(frozen=True)
class GatewayConfig:
    data_dir: Path
    api_key: str = field(default='', repr=False)
    api_base: str = 'https://api.deepseek.com'
    model: str = 'deepseek-flash'
    # Integer microdollars per million tokens. Operator-reviewed peak tariff.
    input_rate: int = 300_000
    cached_rate: int = 6_000
    output_rate: int = 1_200_000
    max_output_tokens: int = 16_384
    max_body_bytes: int = 2_000_000
    recharge: RechargeConfig | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls):
        path = os.environ.get('ATHENA_GATEWAY_KEY_FILE')
        return cls(Path(os.environ['ATHENA_GATEWAY_DATA_DIR']).expanduser(),
                   Path(path).expanduser().read_text().strip() if path else '',
                   os.environ.get('ATHENA_GATEWAY_PROVIDER_URL','https://api.deepseek.com'),
                   os.environ.get('ATHENA_GATEWAY_MODEL','deepseek-flash'),
                   recharge=load_recharge_config(os.environ.get('ATHENA_RECHARGE_CONFIG')))


class Credentials(BaseModel):
    username: str = Field(min_length=2,max_length=40,pattern=r'^[\w\-]+$')
    password: str = Field(min_length=8,max_length=128)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1,max_length=128)
    new_password: str = Field(min_length=8,max_length=128)


class RechargeOrder(BaseModel):
    model_config = ConfigDict(extra='forbid')
    amount_micros: StrictInt = Field(gt=0, le=1_000_000_000)
    idempotency_key: str = Field(min_length=16, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')


class RequestBodyLimit:
    """Bound bytes before FastAPI parses JSON, including chunked requests."""
    def __init__(self, app, maximum):
        self.app, self.maximum = app, maximum

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] not in {'POST', 'PUT', 'PATCH'}:
            return await self.app(scope, receive, send)
        path = scope['path']
        maximum = (4 * 1024 * 1024 if path == '/v1/voice/transcribe' else
                   65_536 if path == '/v1/payments/btcpay/webhook' else
                   16_384 if path.startswith(('/v1/auth/', '/v1/account/')) else self.maximum)
        body = bytearray()
        try:
            async with asyncio.timeout(30):
                while True:
                    event = await receive()
                    if event['type'] == 'http.disconnect':
                        return
                    body.extend(event.get('body', b''))
                    if len(body) > maximum:
                        return await JSONResponse({'code': 'request_too_large'}, status_code=413)(scope, receive, send)
                    if not event.get('more_body'):
                        break
        except TimeoutError:
            return await JSONResponse({'code': 'request_timeout'}, status_code=408)(scope, receive, send)
        delivered = False
        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
            return await receive()
        await self.app(scope, bounded_receive, send)


def charge(config, prompt, output, cached=0):
    return ((prompt-cached)*config.input_rate + cached*config.cached_rate + output*config.output_rate + 999_999)//1_000_000


def usage_charge(config, usage):
    if not isinstance(usage,dict):
        return None
    prompt, output = usage.get('prompt_tokens'), usage.get('completion_tokens')
    details = usage.get('prompt_tokens_details') or {}
    cached = usage.get('prompt_cache_hit_tokens',details.get('cached_tokens',0) if isinstance(details,dict) else 0)
    if any(type(v) is not int or v < 0 or v > 10_000_000 for v in (prompt,output,cached)) or cached > prompt:
        return None
    return charge(config,prompt,output,cached)


def create_gateway(config: GatewayConfig):
    endpoint = urlparse(config.api_base)
    if endpoint.scheme != 'https' or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
        raise ValueError('Upstream requires a credential-free HTTPS URL')

    @asynccontextmanager
    async def lifespan(app):
        require_free_threading()
        config.data_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
        from .lock import InstanceLock
        instance_lock=InstanceLock(config.data_dir/'gateway.lock')
        store = Store(config.data_dir/'accounts.sqlite3')
        await store.initialize()
        await initialize_credits(store)
        await initialize_recharge(store)
        app.state.recharge = Recharge(store,config.recharge)
        app.state.store,app.state.auth,app.state.credits = store,Auth(store,bootstrap_owner=False),Credits(store,recharge_enabled=bool(config.recharge))
        from .voice_gateway import initialize_gateway_voice
        await initialize_gateway_voice(app,store,app.state.credits)
        app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(180,connect=15),follow_redirects=False)
        # Interrupted billing must be reconciled, never refunded or replayed.
        await store.execute("UPDATE credit_requests SET status='uncertain' WHERE status='reserved'")
        try:
            yield
        finally:
            await app.state.http.aclose()
            await app.state.voice.close()
            await app.state.recharge.close()
            await store.close()
            instance_lock.close()

    app = FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None)

    @app.exception_handler(ServiceError)
    async def service_error(request,exc):
        return JSONResponse({'code':exc.code},status_code=exc.http_status)

    @app.exception_handler(ValueError)
    async def invalid(request,exc):
        return JSONResponse({'code':getattr(exc,'code','invalid_input')},status_code=400)

    @app.exception_handler(RequestValidationError)
    async def validation(request,exc):
        return JSONResponse({'code':'invalid_input'},status_code=422)

    @app.exception_handler(HTTPException)
    async def http_error(request,exc):
        return JSONResponse({'code':exc.detail},status_code=exc.status_code)

    @app.middleware('http')
    async def boundary(request,call_next):
        # No browser credentials/CORS are needed: local or hosted harness proxies
        # login; arbitrary websites cannot use this as a cookie-authenticated API.
        if request.headers.get('origin'):
            return JSONResponse({'code':'cross_site_request'},status_code=403)
        length = request.headers.get('content-length','0')
        maximum = 4*1024*1024 if request.url.path=='/v1/voice/transcribe' else config.max_body_bytes
        if not length.isdigit() or int(length)>maximum:
            return JSONResponse({'code':'request_too_large'},status_code=413)
        response = await call_next(request)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        return response

    async def user(request):
        value = request.headers.get('authorization','')
        found = await app.state.auth.current(value[7:] if value.startswith('Bearer ') else None)
        if not found:
            raise ServiceError('login_required','Please sign in.',401)
        return found

    async def auth_rate(request,username):
        # DB-backed per-peer limit also covers unknown usernames and registration.
        peer = request.client.host if request.client else 'unknown'
        from datetime import datetime,timezone,timedelta
        cutoff = (datetime.now(timezone.utc)-timedelta(minutes=15)).isoformat()
        key='gateway-peer:'+hashlib.sha256((peer+':'+username.strip().casefold()).encode()).hexdigest()
        peer_key='gateway-total:'+hashlib.sha256(peer.encode()).hexdigest()
        peer_cutoff = (datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
        def reserve(db):
            db.execute('BEGIN IMMEDIATE')
            for entry, boundary, limit in ((peer_key, peer_cutoff, 60), (key, cutoff, 10)):
                row=db.execute('SELECT * FROM auth_attempts WHERE username=?',(entry,)).fetchone()
                count=row['attempts'] if row and row['last_at']>boundary else 0
                if count>=limit:
                    raise ServiceError('login_rate_limited','Try again later.',429)
                db.execute('INSERT INTO auth_attempts VALUES (?,?,?) ON CONFLICT(username) DO UPDATE SET attempts=excluded.attempts,last_at=excluded.last_at',(entry,count+1,now()))
        await app.state.store.call(reserve)

    @app.get('/v1/health')
    async def health():
        return {'ok':True,'configured':bool(config.api_key)}

    @app.post('/v1/auth/register')
    async def register(request:Request,body:Credentials):
        await auth_rate(request,body.username)
        account,token=await app.state.auth.register(body.username,body.password)
        return {'user':account,'token':token,'credit':await app.state.credits.balance(account['id'])}

    @app.post('/v1/auth/login')
    async def login(request:Request,body:Credentials):
        await auth_rate(request,body.username)
        account,token=await app.state.auth.login(body.username,body.password)
        return {'user':account,'token':token,'credit':await app.state.credits.balance(account['id'])}

    @app.get('/v1/auth/me')
    async def me(request:Request):
        account=await user(request)
        return {'user':account,'credit':await app.state.credits.balance(account['id'])}

    @app.post('/v1/auth/logout')
    async def logout(request:Request):
        await user(request)
        await app.state.store.execute('DELETE FROM auth_sessions WHERE token_hash=?',(token_hash(request.headers['authorization'][7:]),))
        return {'ok':True}

    @app.post('/v1/account/password')
    async def password(request:Request,body:PasswordChange):
        from .accounts import change_password
        account=await user(request)
        await change_password(app.state.store,account['id'],request.headers['authorization'][7:],body.current_password,body.new_password)
        return {'ok':True}

    @app.get('/v1/models')
    async def models(request:Request):
        await user(request)
        return {'data':[{'id':config.model,'object':'model'}]}

    @app.get('/v1/account/recharge')
    async def recharge_options(request:Request):
        account=await user(request)
        return await app.state.recharge.overview(account['id'])

    @app.post('/v1/account/recharge')
    async def recharge_create(request:Request,body:RechargeOrder):
        account=await user(request)
        return await app.state.recharge.create(account['id'],body.amount_micros,body.idempotency_key)

    @app.get('/v1/account/recharge/{order_id}')
    async def recharge_status(request:Request,order_id:str=APIPath(min_length=1,max_length=80,pattern=r'^[A-Za-z0-9_-]+$')):
        account=await user(request)
        return await app.state.recharge.get(account['id'],order_id)

    @app.post('/v1/payments/btcpay/webhook')
    async def recharge_notification(request:Request):
        return await app.state.recharge.webhook(await request.body(),request.headers.get('BTCPay-Sig',''))

    @app.post('/v1/chat/completions')
    async def complete(request:Request):
        account=await user(request)
        raw=bytearray()
        async for part in request.stream():
            raw.extend(part)
            if len(raw)>config.max_body_bytes:
                raise ServiceError('request_too_large','Request too large.',413)
        try:
            payload=json.loads(raw)
        except (ValueError,UnicodeError):
            raise UserError('invalid_input','Invalid JSON') from None
        if not isinstance(payload,dict) or payload.get('model')!=config.model:
            raise ServiceError('model_unavailable','Model unavailable.',400)
        allowed={'model','messages','tools','tool_choice','stream','stream_options','max_tokens','max_completion_tokens','temperature','top_p','thinking','reasoning_effort','parallel_tool_calls'}
        limit=payload.get('max_tokens',payload.get('max_completion_tokens',config.max_output_tokens))
        if {'max_tokens','max_completion_tokens'}<=payload.keys() or payload.keys()-allowed or type(limit) is not int or not 1<=limit<=config.max_output_tokens or not isinstance(payload.get('messages'),list):
            raise UserError('invalid_input','Unsupported request')
        if not config.api_key:
            raise ServiceError('provider_not_configured','Service unavailable.')
        payload['stream']=True
        payload['stream_options']={'include_usage':True}
        if 'max_completion_tokens' not in payload:
            payload['max_tokens']=limit
        request_id=request.headers.get('idempotency-key') or uid()
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,100}',request_id):
            raise UserError('invalid_input','Invalid request identifier')
        # Byte-count ceiling is deliberately conservative; tool framing overhead
        # is reserved separately. Only provider-reported usage is charged.
        amount=charge(config,len(raw)+8192,limit)
        await app.state.credits.reserve(account['id'],request_id,max(1,amount),hashlib.sha256(raw).hexdigest())
        upstream=None
        try:
            upstream=await app.state.http.send(app.state.http.build_request('POST',config.api_base.rstrip('/')+'/chat/completions',json=payload,headers={'Authorization':'Bearer '+config.api_key}),stream=True)
            if upstream.status_code>=400:
                # Only a rejected HTTP request is known not to have generated.
                await upstream.aclose()
                await app.state.credits.settle(request_id,0)
                raise ServiceError('provider_unavailable','Service unavailable.',503)
        except ServiceError:
            raise
        except (httpx.HTTPError,asyncio.CancelledError):
            await app.state.credits.uncertain(request_id)
            raise ServiceError('provider_unavailable','Service unavailable.',503) from None

        async def relay():
            usage=None
            try:
                async for line in upstream.aiter_lines():
                    if line.startswith('data:'):
                        text=line[5:].strip()
                        if text=='[DONE]':
                            # A client closes as soon as it sees DONE. Commit
                            # billing first, independent of an upstream EOF.
                            cost=usage_charge(config,usage)
                            with anyio.CancelScope(shield=True):
                                if cost is not None:
                                    await app.state.credits.settle(request_id,cost,json.dumps(usage))
                                else:
                                    await app.state.credits.uncertain(request_id)
                            yield (line+'\n\n').encode()
                            return
                        else:
                            try:
                                value=json.loads(text)
                                if isinstance(value,dict) and value.get('usage'):
                                    usage=value['usage']
                            except ValueError:
                                pass
                    yield (line+'\n').encode()
            finally:
                with anyio.CancelScope(shield=True):
                    await upstream.aclose()
                    await app.state.credits.uncertain(request_id)
        return StreamingResponse(relay(),media_type='text/event-stream',headers={'X-Athena-Request-ID':request_id,'X-Accel-Buffering':'no'})

    from .voice_gateway import register_gateway_voice_routes
    register_gateway_voice_routes(app,user)
    app.add_middleware(RequestBodyLimit, maximum=config.max_body_bytes)
    return app


if __name__=='__main__':
    import uvicorn
    uvicorn.run(create_gateway(GatewayConfig.from_env()),host=os.environ.get('ATHENA_GATEWAY_HOST','127.0.0.1'),port=int(os.environ.get('ATHENA_GATEWAY_PORT','4321')),loop='asyncio',http='h11',access_log=False)
