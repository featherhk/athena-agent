"""Opt-in real-provider acceptance: assert product effects in an isolated account.

Run: uv run --directory backend python ../scripts/evaluate_requirements.py --key-file PATH
Only aggregate verdicts/usage print; temporary accounts, files and request recordings
are removed. Never uses the running user's chats or performs external actions.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from athena.auth import Auth
from athena.config import Config
from athena.engine import Engine
from athena.files import initialize_files
from athena.accounts import initialize_accounts
from athena.registry import initialize_registry
from athena.preferences import read_preferences
from athena.store import Store, now
from athena.user_help import list_help
from athena.app_controls import initialize_app_controls


async def evaluate(key_file, selected_cases=None):
    key = Path(key_file).expanduser().read_text().strip()
    with TemporaryDirectory(prefix='athena-requirements-') as temporary:
        config = Config(data_dir=Path(temporary) / 'state', repo_dir=Path(__file__).resolve().parents[1], api_key=key, max_steps=12)
        config.prepare()
        store = Store(config.data_dir / 'athena.sqlite3')
        await store.initialize()
        await initialize_files(store)
        await initialize_accounts(store)
        await initialize_registry(store)
        await initialize_app_controls(store)
        engine = Engine(config, store)
        user, _ = await Auth(store).register('acceptance', 'temporary-acceptance-password')
        async def ask(text, check, setup=None, expected_status='complete'):
            session = await store.session(user_id=user['id'])
            if setup:
                await setup(session)
            run = await engine.submit(session, text)
            await asyncio.wait_for(asyncio.gather(*engine.actors.values()), 180)
            status = (await store.rows('SELECT status,error_code FROM runs WHERE id=?', (run,)))[0]
            passed = status['status'] == expected_status and await check(session)
            requests = await store.rows('SELECT usage FROM requests WHERE run_id=?', (run,))
            tokens = sum(json.loads(row['usage'] or '{}').get('total_tokens', 0) for row in requests)
            result = {'passed': passed, 'requests': len(requests), 'tokens': tokens, 'error_code': status['error_code']}
            if not passed:
                # Synthetic acceptance data only; never dump provider snapshots or credentials.
                history = await store.history(session)
                calls = []
                for row in history:
                    for call in json.loads(row['tool_calls'] or '[]'):
                        args = json.loads(call['function']['arguments'])
                        calls.append({'tool': call['function']['name'], 'op': args.get('op'),
                                      'args': {k: v for k, v in (args.get('args') or args).items() if k in ('action', 'target', 'session_id', 'name')}})
                result['actions'] = calls
                result['reply'] = next((row['content'][:600] for row in reversed(history) if row['role'] == 'assistant' and row['content']), '')
            return result
        async def language(_):
            return (await read_preferences(store, user['id']))['ui_language'] == 'zh-Hans'
        async def style(_):
            values = await read_preferences(store, user['id'])
            return values['ui_font'] == 'serif' and values['ui_palette'] == 'ocean'
        async def black(_):
            return (await read_preferences(store, user['id']))['ui_palette'] == '#000000'
        async def custom_color(_):
            return (await read_preferences(store, user['id']))['ui_palette'] == '#102337'
        async def pinned_clock(session):
            # Registration and requested placement are verifiable here. Browser
            # rendering is a separate check, never inferred from registered HTML.
            rows = await store.components(session)
            return any(row['placement'] == 'top-start' and '<script' in row['html'] and 'Date' in row['html'] for row in rows)
        async def missing_city(session):
            pending = await list_help(store, session, user['id'])
            questions = ' '.join(question['question'] for request in pending for question in request['questions'])
            return bool(pending) and any(word in questions.lower() for word in ('城市', '位置', '地区', '地点', 'city', 'location')) and not await store.components(session)
        async def developer_view(session):
            values = await read_preferences(store, user['id'])
            actions = await store.rows("SELECT payload FROM app_actions WHERE session_id=? AND action='open'", (session,))
            return values['developer_mode'] or any(json.loads(row['payload']).get('target') == 'developer' for row in actions)
        async def moving_surface(session):
            rows = await store.components(session)
            return any(row['placement'] == 'overlay' and 'Athena.observe' in row['html'] and 'requestAnimationFrame' in row['html'] for row in rows)
        async def reply(_):
            memories = await store.rows("SELECT content FROM memories WHERE owner_id=?", (f"global:{user['id']}",))
            return bool(memories) and (await read_preferences(store, user['id']))['ui_language'] == 'zh-Hans'
        async def account(_):
            return (await store.rows('SELECT display_name FROM users WHERE id=?', (user['id'],)))[0]['display_name'] == '小林'
        async def extension(session):
            definitions = await engine.tools.definitions(session)
            names = [d['function']['name'] for d in definitions if d['function']['name'].startswith('user_')]
            history = await store.history(session)
            calls = {call['id'] for row in history for call in json.loads(row['tool_calls'] or '[]') if call['function']['name'] in names}
            def contains_area(value):
                if isinstance(value, dict):
                    return any(contains_area(item) for item in value.values())
                if isinstance(value, (list, tuple)):
                    return any(contains_area(item) for item in value)
                return type(value) in (int, float) and value == 21
            for row in history:
                if row['tool_call_id'] in calls:
                    result = json.loads(row['content'])
                    if result.get('exit_code') == 0:
                        try:
                            value = json.loads(result['output'])
                        except (ValueError, KeyError):
                            continue
                        if contains_area(value):
                            return True
            return False
        async def inspection(session):
            rows = await store.history(session)
            calls = {call['id']: json.loads(call['function']['arguments']).get('op')
                     for row in rows for call in json.loads(row['tool_calls'] or '[]')
                     if call['function']['name'] == 'runtime'}
            successful = set()
            for row in rows:
                op = calls.get(row['tool_call_id'])
                if row['role'] != 'tool' or op not in {'context', 'usage'}:
                    continue
                result = json.loads(row['content'])
                if op == 'context' and result.get('measurement') == 'estimated' and type(result.get('estimated_input_tokens')) is int:
                    successful.add(op)
                if (op == 'usage' and result.get('usage_measurement') == 'provider_reported'
                        and result.get('window', {}).get('scope') == 'account'
                        and result.get('window', {}).get('seconds') == 3600
                        and 'request_count' in result.get('total', {})):
                    successful.add(op)
            return {'context', 'usage'} <= successful
        reference = await store.session('装修预算记录', user_id=user['id'])
        await store.message(reference, None, 'user', '装修预算上限为 23800 元，不含家电。')
        async def cross_chat(session):
            history = await store.history(session)
            return any(row['role'] == 'tool' and reference in row['content'] and '23800' in row['content'] for row in history) and any(row['role'] == 'assistant' and '23800' in row['content'] for row in history)
        async def navigate_chat(session):
            actions = await store.rows("SELECT payload FROM app_actions WHERE session_id=? AND action='navigate'", (session,))
            return any(json.loads(row['payload']).get('session_id') == reference for row in actions)
        prepared = {}
        async def prior_attempt(session):
            run = await store.enqueue(session, '旧草稿：只用于测试的重复结果，不再需要。')
            await store.claim(session)
            call = {'id': 'synthetic-result', 'type': 'function', 'function': {'name': 'exec', 'arguments': '{"command":"echo synthetic"}'}}
            await store.message(session, run, 'assistant', '', tool_calls=[call])
            message = await store.message(session, run, 'tool', 'Synthetic repeated test output.\n' * 600, tool_call_id=call['id'])
            await store.message(session, run, 'assistant', '旧草稿已完成。')
            await store.execute("UPDATE runs SET status='complete',finished_at=? WHERE id=?", (now(), run))
            prepared[session] = (run, message)
        async def compacted(session):
            _, message = prepared[session]
            clips = await store.rows('SELECT content FROM context_clips WHERE session_id=? AND message_id=?', (session, message))
            original = next(row for row in await store.history(session) if row['id'] == message)['content']
            return bool(clips) and len(clips[0]['content']) < len(original) and original == 'Synthetic repeated test output.\n' * 600
        async def excluded(session):
            run, _ = prepared[session]
            return run not in {row['run_id'] for row in await store.context_history(session)} and run in {row['run_id'] for row in await store.history(session)}
        try:
            cases = [
                ('application-language', '把这个应用里的按钮、菜单文字都改成简体中文，直接设置就好。', language),
                ('appearance', '把 Athena 界面改成衬线字体、海洋蓝配色。', style),
                ('black-theme', '我要黑色界面，整个应用背景改为纯黑色。', black),
                ('custom-theme', '把这个应用的背景颜色设为 #102337。', custom_color),
                ('pinned-clock', '页面左上角固定放一个显示当前时间的小钟，秒数实时走动，滚动聊天也一直看得到。', pinned_clock),
                ('developer-view', '帮我打开开发者模式，显示我的 token 和费用统计浮窗。请直接操作。', developer_view),
                ('moving-surface', '让一只小鸭在可见的聊天消息之间跳来跳去，要根据消息位置移动，滚动时也跟着变化。注册成界面效果，不要改源码。', moving_surface),
                ('reply-preference', '以后在所有对话中都用日语简短回答我，但别改应用界面语言。记住这个偏好。', reply),
                ('account-profile', '把我这个 Athena 账号的显示名字改成小林。', account),
                ('new-capability', '给你自己增加一个可复用工具：输入长和宽，返回长方形面积。用已支持的注册机制，创建一个之前没有的工具，不要改应用源码。注册后测试 3 乘 7 等于 21。', extension),
                ('self-inspection', '请查询你当前的上下文用了多少、窗口有多大，以及我账号最近一小时 token、缓存率和费用。使用真实状态，区分估算和实测，不要只是介绍怎么查。', inspection),
                ('other-conversation', '请找到我那段叫“装修预算记录”的其他会话，查一下预算上限以及是否包含家电。', cross_chat),
                ('navigate-conversation', '把我现在打开的界面切换到“装修预算记录”那条会话，直接帮我切过去，不是只读取它的内容。', navigate_chat),
                ('compact', '把当前模型上下文尽量压缩到约 1000 token，优先裁剪旧工具输出，保留可恢复原文；做完后告诉我是否达到目标。', compacted, prior_attempt),
                ('context-edit', '把前面已经完成的那一整个“旧草稿”回合从你接下来使用的模型上下文排除，聊天记录和文件要保留。实际操作，不要只是说你会忽略。', excluded, prior_attempt),
            ]
            report = {}
            for name, prompt, check, *setup in cases:
                if selected_cases and name not in selected_cases:
                    continue
                try:
                    report[name] = await ask(prompt, check, setup[0] if setup else None)
                except TimeoutError:
                    report[name] = {'passed': False, 'error_code': 'timeout'}
                    for session in list(engine.active_runs):
                        await engine.stop(session)
                print(json.dumps({name: report[name]}, ensure_ascii=False), flush=True)
            if selected_cases is None or 'missing-information' in selected_cases:
                report['missing-information'] = await ask('在页面的左上角显示今天的天气，实时更新。', missing_city, expected_status='waiting_user')
                print(json.dumps({'missing-information': report['missing-information']}, ensure_ascii=False), flush=True)
            if not report or not all(row['passed'] for row in report.values()):
                raise SystemExit(1)
        finally:
            await engine.close()
            await store.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--key-file', required=True)
    parser.add_argument('--case', action='append', dest='cases', help='Run only the named case (repeatable).')
    args = parser.parse_args()
    asyncio.run(evaluate(args.key_file, args.cases))
