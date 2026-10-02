import asyncio
import json

import httpx

from athena.api import create_app


async def test_help_survives_snapshot_and_resumes_once_with_authorized_answer(config):
    requests = []

    def provider(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            delta = {"tool_calls": [{"index": 0, "id": "need-return-date", "type": "function", "function": {
                "name": "runtime", "arguments": json.dumps({"op": "user_help", "args": {"questions": [
                    {"id": "return_date", "question": "What date are you returning?", "options": [{"label": "October 15"}]}
                ]}})}}]}
            finish = 'tool_calls'
        else:
            assert payload['messages'][-1]['content'] == 'October 15'
            delta, finish = {'content': 'I will use October 15 as your return date.'}, 'stop'
        data = 'data: ' + json.dumps({'choices': [{'delta': delta, 'finish_reason': finish}]}) + '\n\ndata: [DONE]\n\n'
        return httpx.Response(200, text=data, headers={'content-type': 'text/event-stream'})

    app = create_app(config)
    async with app.router.lifespan_context(app):
        engine = app.state.engine
        await engine.client.aclose()
        engine.client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1:4317') as client:
            await client.post('/api/auth/register', json={'username': 'traveler', 'password': 'test-help-password'})
            session = (await client.post('/api/sessions', json={})).json()['id']
            await client.post(f'/api/sessions/{session}/messages', json={'text': 'Find a return flight.'})
            await asyncio.wait_for(asyncio.gather(*engine.actors.values()), 5)
            snapshot = (await client.get(f'/api/sessions/{session}')).json()
            assert snapshot['runs'][0]['status'] == 'waiting_user'
            question = snapshot['help_requests'][0]
            assert question['questions'][0]['id'] == 'return_date'
            assert len(requests) == 1 and session not in engine.active_runs
            assert (await client.get('/api/sessions')).json()[0]['pending'] == 1
            path = f'/api/sessions/{session}/help/{question["id"]}/answer'
            assert (await client.post(path, json={'answers': {}})).status_code == 400
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1:4317') as other:
                await other.post('/api/auth/register', json={'username': 'stranger', 'password': 'test-help-password'})
                assert (await other.post(path, json={'answers': {'return_date': 'October 15'}})).status_code == 404
            answers = {'answers': {'return_date': 'October 15'}}
            results = await asyncio.gather(client.post(path, json=answers), client.post(path, json=answers))
            assert all(response.status_code == 200 for response in results)
            await asyncio.wait_for(asyncio.gather(*engine.actors.values()), 5)
            final = (await client.get(f'/api/sessions/{session}')).json()
            assert final['help_requests'] == [] and final['runs'][0]['status'] == 'complete'
            assert len(requests) == 2
            assert sum(message['content'] == 'October 15' for message in final['messages']) == 1
            assert (await client.post(path, json={'answers': {'return_date': 'October 16'}})).status_code == 400
