import json
import httpx
from athena.gateway import GatewayConfig,create_gateway,usage_charge


async def test_gateway_auth_credit_secret_boundary_and_idempotency(tmp_path):
    config=GatewayConfig(tmp_path,api_key='operator-only-test-key')
    app=create_gateway(config)
    received=[]
    def provider(request):
        received.append(request)
        data={'choices':[{'delta':{'content':'hello'},'finish_reason':'stop'}],
              'usage':{'prompt_tokens':20,'completion_tokens':5,'prompt_cache_hit_tokens':10}}
        return httpx.Response(200,text='data: '+json.dumps(data)+'\n\ndata: [DONE]\n\n')
    async with app.router.lifespan_context(app):
        await app.state.http.aclose()
        app.state.http=httpx.AsyncClient(transport=httpx.MockTransport(provider))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='https://cloud.test') as client:
            registration=await client.post('/v1/auth/register',json={'username':'david','password':'test-password'})
            account=registration.json()
            assert account['user']['role']=='user'
            assert account['credit']['available_micros']==0
            headers={'Authorization':'Bearer '+account['token'],'Idempotency-Key':'one-request-only-123'}
            payload={'model':'deepseek-flash','messages':[{'role':'user','content':'hello'}],'max_tokens':100}
            assert (await client.post('/v1/chat/completions',json=payload,headers=headers)).status_code==402
            assert not received
            await app.state.credits.grant(account['user']['id'],10_000_000,'david-preview','User authorized test credit')
            response=await client.post('/v1/chat/completions',json=payload,headers=headers)
            assert response.status_code==200 and 'hello' in response.text
            assert received[0].headers['authorization']=='Bearer operator-only-test-key'
            assert 'operator-only-test-key' not in response.text+registration.text
            assert (await client.post('/v1/chat/completions',json=payload,headers=headers)).status_code==409
            assert len(received)==1
            balance=(await client.get('/v1/auth/me',headers=headers)).json()['credit']
            assert balance['available_micros']<10_000_000 and balance['held_micros']==0
            assert (await client.post('/v1/auth/logout',json={},headers=headers)).status_code==200
            assert (await client.get('/v1/auth/me',headers=headers)).status_code==401


async def test_gateway_rejects_model_changes_and_missing_auth(tmp_path):
    app=create_gateway(GatewayConfig(tmp_path,api_key='test-key'))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='https://cloud.test') as client:
            assert (await client.post('/v1/chat/completions',json={})).status_code==401
            result=(await client.post('/v1/auth/register',json={'username':'other','password':'test-password'})).json()
            headers={'Authorization':'Bearer '+result['token']}
            assert (await client.post('/v1/chat/completions',json={'model':'arbitrary-expensive-model'},headers=headers)).status_code==400
            assert (await client.get('/v1/auth/me',headers={**headers,'Origin':'https://evil.test'})).status_code==403


def test_unknown_or_invalid_usage_is_not_a_free_success(tmp_path):
    config=GatewayConfig(tmp_path)
    for usage in ({},{'prompt_tokens':1,'completion_tokens':2,'prompt_cache_hit_tokens':5},{'prompt_tokens':True,'completion_tokens':2}):
        assert usage_charge(config,usage) is None


async def test_chunked_auth_body_is_bounded_before_json_parsing(tmp_path):
    app=create_gateway(GatewayConfig(tmp_path))
    async def chunks():
        for _ in range(20):
            yield b' ' * 1024
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='https://cloud.test') as client:
            response=await client.post('/v1/auth/register',content=chunks())
            assert response.status_code==413
            assert not await app.state.store.rows('SELECT * FROM users')


async def test_aggregate_auth_limit_covers_rotating_usernames(tmp_path,monkeypatch):
    app=create_gateway(GatewayConfig(tmp_path))
    async with app.router.lifespan_context(app):
        called=[]
        async def register(username,password):
            called.append(username)
            return {'id':username},'test-token'
        monkeypatch.setattr(app.state.auth,'register',register)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='https://cloud.test') as client:
            for index in range(61):
                response=await client.post('/v1/auth/register',json={'username':f'person{index}','password':'test-password'})
                assert response.status_code==(200 if index<60 else 429)
            assert len(called)==60


async def test_two_output_limits_cannot_bypass_reservation(tmp_path):
    app=create_gateway(GatewayConfig(tmp_path,api_key='test-key'))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='https://cloud.test') as client:
            result=(await client.post('/v1/auth/register',json={'username':'tester','password':'test-password'})).json()
            headers={'Authorization':'Bearer '+result['token']}
            response=await client.post('/v1/chat/completions',json={'model':'deepseek-flash','messages':[], 'max_tokens':1,'max_completion_tokens':1000000},headers=headers)
            assert response.status_code==400
            assert not await app.state.store.rows('SELECT * FROM credit_requests')
