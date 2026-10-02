"""Server-to-server account client. Provider secrets never enter the harness.

Local login cookies reference separate cloud bearer tokens. Tokens are not exposed
in API responses, prompts, request snapshots or execution environments.
"""
from urllib.parse import urlparse
import httpx
from .auth import Auth, token_hash
from .errors import ServiceError
from .store import now


class CloudAccounts:
    def __init__(self, store, base):
        url=urlparse(base)
        if url.scheme!='https' or url.username or url.password or url.query or url.fragment:
            raise ValueError('Cloud account endpoint requires HTTPS')
        self.store,self.base=store,base.rstrip('/')
        self.http=httpx.AsyncClient(timeout=20,follow_redirects=False)
        self.local=Auth(store,bootstrap_owner=False)

    async def initialize(self):
        await self.store.execute('CREATE TABLE IF NOT EXISTS cloud_logins (local_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), remote_token TEXT NOT NULL, created_at TEXT NOT NULL)')

    async def call(self,path,*,token=None,body=None):
        try:
            response=await self.http.request('POST' if body is not None else 'GET',self.base+path,json=body,headers={'Authorization':'Bearer '+token} if token else {})
        except httpx.HTTPError:
            raise ServiceError('cloud_unavailable','Account service is temporarily unavailable.') from None
        try:
            value=response.json()
        except ValueError:
            raise ServiceError('cloud_unavailable','Account service is temporarily unavailable.') from None
        if response.status_code>=400:
            raise ServiceError(value.get('code','cloud_unavailable'),'Account service could not complete this request.',response.status_code)
        return value

    async def authenticate(self,action,username,password):
        result=await self.call('/auth/'+action,body={'username':username,'password':password})
        remote=result['user']
        def sync(db):
            # Match only immutable cloud IDs. Never merge an unrelated local
            # account just because somebody chose the same display username.
            db.execute("INSERT INTO users(id,username,password_hash,role,created_at) VALUES (?,?,'cloud-managed','user',?) ON CONFLICT(id) DO UPDATE SET username=excluded.username",(remote['id'],remote['username'],now()))
        try:
            await self.store.call(sync)
        except Exception:
            await self.call('/auth/logout',token=result['token'],body={})
            raise ServiceError('cloud_account_conflict','Use a separate data directory for cloud-managed accounts.') from None
        cookie=await self.local.issue(remote['id'])
        await self.store.execute('INSERT INTO cloud_logins VALUES (?,?,?,?)',(token_hash(cookie),remote['id'],result['token'],now()))
        return await self.local.current(cookie),cookie

    async def token(self,user_id):
        rows=await self.store.rows('SELECT c.local_hash,c.remote_token FROM cloud_logins c JOIN auth_sessions a ON a.token_hash=c.local_hash WHERE c.user_id=? AND a.expires_at>? ORDER BY c.created_at DESC LIMIT 20',(user_id,now()))
        for row in rows:
            try:
                result=await self.call('/auth/me',token=row['remote_token'])
            except ServiceError as exc:
                if exc.http_status!=401:
                    raise
                await self.store.execute('DELETE FROM cloud_logins WHERE local_hash=?',(row['local_hash'],))
                continue
            if result['user']['id']==user_id:
                return row['remote_token']
        raise ServiceError('login_required','Please sign in.',401)

    async def current(self,cookie):
        local=await self.local.current(cookie)
        if not local:
            return None
        rows=await self.store.rows('SELECT remote_token FROM cloud_logins WHERE local_hash=?',(token_hash(cookie),))
        if not rows:
            return None
        try:
            remote=await self.call('/auth/me',token=rows[0]['remote_token'])
        except ServiceError as exc:
            if exc.http_status==401:
                def revoke(db):
                    db.execute('DELETE FROM cloud_logins WHERE local_hash=?',(token_hash(cookie),))
                    db.execute('DELETE FROM auth_sessions WHERE token_hash=?',(token_hash(cookie),))
                await self.store.call(revoke)
                return None
            raise
        return local if remote['user']['id']==local['id'] else None

    async def require_credit(self,user_id):
        result=await self.call('/auth/me',token=await self.token(user_id))
        credit=result['credit']
        if not credit['enabled'] or credit['available_micros']<=0:
            raise ServiceError('account_balance_insufficient','Your account has no available credit.',402)
        return credit

    async def logout(self,cookie):
        rows=await self.store.rows('SELECT remote_token FROM cloud_logins WHERE local_hash=?',(token_hash(cookie),))
        if rows:
            await self.call('/auth/logout',token=rows[0]['remote_token'],body={})
        await self.store.execute('DELETE FROM cloud_logins WHERE local_hash=?',(token_hash(cookie),))
        await self.store.execute('DELETE FROM auth_sessions WHERE token_hash=?',(token_hash(cookie),))
