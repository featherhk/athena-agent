"""Real gateway HTTP + SQLite, with only the external payment/model transports fake.

No wallet, real invoice, payment, credential or operator account is used here.
"""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from decimal import Decimal
import hashlib
import hmac
import json

import httpx
import pytest

from athena.gateway import GatewayConfig, create_gateway
from athena.payment_provider import RechargeConfig, load_recharge_config, money_micros
from athena.recharge import Recharge, initialize_recharge


KEY = 'synthetic-btcpay-key'
SECRET = 'synthetic-btcpay-webhook-secret'
BASE = '/v1/account/recharge'
WEBHOOK = '/v1/payments/btcpay/webhook'


class PaymentServer:
    def __init__(self):
        self.calls = []
        self.invoices = {}
        self.fail_after_create = False
        self.get_error = False
        self.created = 0

    async def handle(self, request):
        self.calls.append(request)
        assert request.url.host == 'payments.example'
        assert request.headers['authorization'] == 'token ' + KEY
        if request.method == 'POST':
            assert request.url.path == '/api/v1/stores/test-store/invoices'
            data = json.loads(request.content)
            assert data['checkout']['paymentTolerance'] == 0
            assert set(data['metadata']) == {'orderId', 'athenaRecharge'}
            self.created += 1
            identifier = f'invoice-{self.created}'
            value = {'id': identifier, 'storeId': 'test-store', 'amount': data['amount'], 'paidAmount': '0',
                     'currency': data['currency'], 'metadata': data['metadata'], 'status': 'New',
                     'additionalStatus': 'None', 'checkoutLink': f'https://payments.example/i/{identifier}'}
            self.invoices[identifier] = value
            # Exercise concurrency after durable POST admission.
            await asyncio.sleep(0)
            if self.fail_after_create:
                raise httpx.ReadTimeout('synthetic lost creation response', request=request)
            return httpx.Response(200, json=value)
        if self.get_error:
            return httpx.Response(503, json={'private': KEY})
        if request.url.path == '/api/v1/stores/test-store/invoices':
            values = [row for row in self.invoices.values() if row['metadata'].get('orderId') == request.url.params['orderId']]
            return httpx.Response(200, json=values[:2])
        identifier = request.url.path.removeprefix('/api/v1/invoices/')
        return httpx.Response(200, json=self.invoices[identifier])

    def settled(self, identifier='invoice-1'):
        invoice = self.invoices[identifier]
        invoice.update(status='Settled', paidAmount=invoice['amount'])
        return invoice

    def event(self, *, identifier='invoice-1', delivery='delivery-1', kind='InvoiceSettled', **changes):
        invoice = self.invoices[identifier]
        value = {'deliveryId': delivery, 'webhookId': 'test-webhook', 'isRedelivery': False,
                 'originalDeliveryId': delivery, 'type': kind, 'storeId': 'test-store',
                 'invoiceId': identifier, 'metadata': dict(invoice['metadata']),
                 'manuallyMarked': False, 'overPaid': False, **changes}
        return json.dumps(value, separators=(',', ':')).encode()


def signed(raw):
    return {'BTCPay-Sig': 'sha256=' + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest(), 'Content-Type': 'application/json'}


@asynccontextmanager
async def stack(tmp_path, *, enabled=True):
    payment = RechargeConfig('https://payments.example', 'test-store', KEY, SECRET, (5_000_000, 10_000_000)) if enabled else None
    app = create_gateway(GatewayConfig(tmp_path, api_key='synthetic-model-key', recharge=payment))
    external = PaymentServer()
    model_calls = []
    def model(request):
        model_calls.append(request)
        value = {'choices': [{'delta': {'content': 'Paid account is ready.'}, 'finish_reason': 'stop'}],
                 'usage': {'prompt_tokens': 10, 'completion_tokens': 5}}
        return httpx.Response(200, text='data: ' + json.dumps(value) + '\n\ndata: [DONE]\n\n')
    async with app.router.lifespan_context(app):
        await app.state.http.aclose()
        app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(model))
        if enabled:
            await app.state.recharge.provider.http.aclose()
            app.state.recharge.provider.http = httpx.AsyncClient(transport=httpx.MockTransport(external.handle))
        user, token = await app.state.auth.register('recharge-user', 'synthetic-password')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='https://athena.test',
                                     headers={'Authorization': 'Bearer ' + token}) as client:
            yield app, client, user, external, model_calls


async def create(client, key='same-order-key-123456', amount=5_000_000):
    return await client.post(BASE, json={'amount_micros': amount, 'idempotency_key': key})


async def deliver(client, external, **changes):
    raw = external.event(**changes)
    return await client.post(WEBHOOK, content=raw, headers=signed(raw))


async def test_disabled_and_unauthenticated_are_closed(tmp_path):
    async with stack(tmp_path, enabled=False) as (app, client, user, external, _):
        assert (await client.get(BASE)).json() == {'enabled': False, 'amounts_micros': [], 'currency': 'USD', 'orders': []}
        assert (await create(client)).status_code == 503
        assert not (await app.state.credits.balance(user['id']))['recharge_enabled']
        assert not await app.state.store.rows('SELECT * FROM recharge_orders')
        assert not external.calls
        assert (await client.get(BASE, headers={'Authorization': ''})).status_code == 401
        assert (await client.post(WEBHOOK, content=b'{}')).status_code == 503


async def test_create_is_idempotent_under_twenty_concurrent_requests(tmp_path):
    async with stack(tmp_path) as (app, client, user, external, _):
        results = await asyncio.gather(*(create(client) for _ in range(20)))
        assert {result.status_code for result in results} == {200}
        assert len({result.json()['id'] for result in results}) == 1
        assert external.created == 1
        order = (await client.get(BASE)).json()['orders'][0]
        assert order['status'] == 'pending'
        assert order['checkout_url'] == 'https://payments.example/i/invoice-1'
        assert len(await app.state.store.rows('SELECT * FROM recharge_orders')) == 1
        conflict = await create(client, amount=10_000_000)
        assert conflict.status_code == 409 and conflict.json()['code'] == 'recharge_conflict'
        assert external.created == 1
        assert KEY not in json.dumps(order) and SECRET not in json.dumps(order)


@pytest.mark.parametrize('amount', [True, 5_000_000.0, '5000000', 0, -1, 1_000_000_001])
async def test_http_input_does_not_coerce_money(tmp_path, amount):
    async with stack(tmp_path) as (app, client, _, external, _):
        assert (await create(client, amount=amount)).status_code == 422
        assert not external.calls
        assert not await app.state.store.rows('SELECT * FROM recharge_orders')


async def test_removed_preset_does_not_hide_an_existing_idempotent_order(tmp_path):
    async with stack(tmp_path) as (app, client, _, external, _):
        first = (await create(client)).json()
        app.state.recharge.config = replace(app.state.recharge.config, amounts_micros=(10_000_000,))
        assert (await create(client)).json() == first
        rejected = await create(client, key='another-order-key-123')
        assert rejected.status_code == 400
        assert rejected.json()['code'] == 'recharge_amount_unavailable'
        assert external.created == 1


async def test_twenty_duplicate_settlements_enable_same_login_and_charge_model_once(tmp_path):
    async with stack(tmp_path) as (app, client, user, external, models):
        payload = {'model': 'deepseek-flash', 'messages': [{'role': 'user', 'content': 'hello'}], 'max_tokens': 16}
        model_headers = {'Idempotency-Key': 'funded-model-request-123'}
        assert (await client.post('/v1/chat/completions', json=payload, headers=model_headers)).status_code == 402
        assert not models
        order = (await create(client)).json()
        external.settled()
        raw = external.event()
        replies = await asyncio.gather(*(client.post(WEBHOOK, content=raw, headers=signed(raw)) for _ in range(20)))
        assert {reply.status_code for reply in replies} == {200}
        assert (await client.get(BASE + '/' + order['id'])).json()['status'] == 'credited'
        entries = await app.state.store.rows("SELECT * FROM credit_entries WHERE operation_key LIKE 'recharge:%'")
        assert len(entries) == 1 and entries[0]['delta_micros'] == 5_000_000
        assert entries[0]['user_id'] == user['id']
        assert len(await app.state.store.rows('SELECT * FROM recharge_receipts')) == 1
        assert (await app.state.credits.balance(user['id']))['enabled']
        response = await client.post('/v1/chat/completions', json=payload, headers=model_headers)
        assert response.status_code == 200 and 'Paid account is ready.' in response.text
        assert len(models) == 1
        assert (await client.post('/v1/chat/completions', json=payload, headers=model_headers)).status_code == 409
        assert len(models) == 1
        # Neither stale events nor repeated delivery can regress credited state.
        external.invoices['invoice-1'].update(status='New', paidAmount='0')
        assert (await deliver(client, external, delivery='old-created-event', kind='InvoiceCreated')).status_code == 200
        assert (await client.get(BASE)).json()['orders'][0]['status'] == 'credited'
        assert len(await app.state.store.rows('SELECT * FROM recharge_receipts')) == 1


async def test_unknown_create_uses_only_read_recovery_and_survives_restart(tmp_path):
    async with stack(tmp_path) as (app, client, user, external, _):
        external.fail_after_create = True
        first = await create(client)
        assert first.status_code == 200 and first.json()['status'] == 'unknown'
        identifier = first.json()['id']
        assert first.json()['checkout_url'] is None
        assert (await create(client)).json()['id'] == identifier
        assert external.created == 1
        blocked_new_key = await create(client, key='fresh-but-unsafe-key-123')
        assert blocked_new_key.status_code == 409
        assert blocked_new_key.json()['code'] == 'recharge_review_required'
        await initialize_recharge(app.state.store)
        await app.state.recharge.close()
        app.state.recharge = Recharge(app.state.store, app.state.recharge.config)
        await app.state.recharge.provider.http.aclose()
        app.state.recharge.provider.http = httpx.AsyncClient(transport=httpx.MockTransport(external.handle))
        restored = (await client.get(BASE + '/' + identifier)).json()
        assert restored['status'] == 'pending' and restored['checkout_url']
        assert external.created == 1
        assert any(call.url.params.get('orderId') == identifier for call in external.calls)


async def test_webhook_recovers_unknown_creation_without_new_invoice(tmp_path):
    async with stack(tmp_path) as (app, client, user, external, _):
        external.fail_after_create = True
        order = (await create(client)).json()
        external.settled()
        assert (await deliver(client, external)).status_code == 200
        assert (await client.get(BASE + '/' + order['id'])).json()['status'] == 'credited'
        assert (await app.state.credits.balance(user['id']))['balance_micros'] == 5_000_000
        assert external.created == 1


async def test_cancel_after_admission_commit_preserves_unknown_without_post_replay(tmp_path, monkeypatch):
    async with stack(tmp_path) as (app, client, user, external, _):
        committed = asyncio.Event()
        original = app.state.store.call
        async def delayed(operation):
            result = await original(operation)
            if getattr(operation, '__name__', '') == 'reserve':
                committed.set()
                await asyncio.Event().wait()
            return result
        monkeypatch.setattr(app.state.store, 'call', delayed)
        work = asyncio.create_task(app.state.recharge.create(user['id'], 5_000_000, 'cancel-after-commit-123'))
        await asyncio.wait_for(committed.wait(), 2)
        work.cancel()
        with pytest.raises(asyncio.CancelledError):
            await work
        monkeypatch.setattr(app.state.store, 'call', original)
        rows = await app.state.store.rows('SELECT * FROM recharge_orders')
        assert len(rows) == 1 and rows[0]['status'] == 'unknown'
        assert external.created == 0
        assert (await create(client, key='cancel-after-commit-123')).json()['status'] == 'unknown'
        assert (await client.get(BASE + '/' + rows[0]['id'])).json()['status'] == 'unknown'
        assert external.created == 0
        assert len(external.calls) == 1 and external.calls[0].method == 'GET'


@pytest.mark.parametrize('change', [
    {'amount': '6'}, {'currency': 'EUR'}, {'storeId': 'another-store'},
    {'metadata': {'orderId': 'another-order', 'athenaRecharge': 'v1'}},
    {'metadata': {'orderId': 'correct-but-marker-missing'}},
    {'additionalStatus': 'Marked'}, {'additionalStatus': 'PaidLate'},
    {'additionalStatus': 'PaidOver'}, {'additionalStatus': 'PaidPartial'},
    {'paidAmount': '4.99'}, {'paidAmount': '5.01'}, {'amount': True},
    {'amount': 'NaN'}, {'paidAmount': 'Infinity'},
    {'status': []}, {'status': {}},
    {'paidAmount': '4.999999999999999999999999999999'},
    {'checkoutLink': 'https://attacker.example/i/pay'},
    {'checkoutLink': 'https://payments.example@attacker.example/i/pay'},
])
async def test_provider_identity_and_payment_exceptions_never_credit(tmp_path, change):
    async with stack(tmp_path) as (app, client, user, external, _):
        order = (await create(client)).json()
        raw = external.event()  # Preserve valid event; corrupt independent read.
        external.settled().update(change)
        response = await client.post(WEBHOOK, content=raw, headers=signed(raw))
        assert response.status_code == 200
        status = (await client.get(BASE + '/' + order['id'])).json()
        assert status['status'] == 'review' and status['checkout_url'] is None
        assert not await app.state.store.rows('SELECT * FROM recharge_receipts')
        assert not await app.state.store.rows('SELECT * FROM credit_entries')
        assert (await app.state.credits.balance(user['id']))['balance_micros'] == 0
        # Manual/operator review remains sticky even if subsequent API snapshots
        # appear normal. No silent new charge or new order is created.
        assert (await create(client)).json()['status'] == 'review'
        assert external.created == 1


@pytest.mark.parametrize('fields', [{'manuallyMarked': True}, {'manuallyMarked': 'false'}, {'overPaid': True}, {'overPaid': None},
                                  {'metadata': {'orderId': 'wrong', 'athenaRecharge': 'v1'}}])
async def test_webhook_exception_flags_are_not_overruled_by_settled_get(tmp_path, fields):
    async with stack(tmp_path) as (app, client, _, external, _):
        order = (await create(client)).json()
        external.settled()
        assert (await deliver(client, external, **fields)).status_code == 200
        assert (await client.get(BASE + '/' + order['id'])).json()['status'] == 'review'
        assert not await app.state.store.rows('SELECT * FROM credit_entries')


async def test_payment_settled_event_is_not_invoice_settlement(tmp_path):
    async with stack(tmp_path) as (app, client, _, external, _):
        order = (await create(client)).json()
        external.settled()
        assert (await deliver(client, external, kind='InvoicePaymentSettled')).status_code == 200
        assert not await app.state.store.rows('SELECT * FROM credit_entries')
        assert (await client.get(BASE)).json()['orders'][0]['status'] == 'confirming'
        # An independent explicit read can recover a missed InvoiceSettled event.
        assert (await client.get(BASE + '/' + order['id'])).json()['status'] == 'credited'


async def test_invalid_hmac_and_delivery_reuse_do_not_change_ledger(tmp_path):
    async with stack(tmp_path) as (app, client, _, external, _):
        await create(client)
        raw = external.event(kind='InvoiceCreated')
        for signature in ('', 'sha256=bad', '非ASCII', signed(raw)['BTCPay-Sig']):
            headers = {'BTCPay-Sig': signature} if signature != '非ASCII' else None
            if headers is None:
                # ASGI/header wire representations are ASCII, but the adapter
                # itself must also fail closed for malformed decoded values.
                with pytest.raises(Exception) as failure:
                    app.state.recharge.provider.event(raw, signature)
                assert getattr(failure.value, 'http_status', None) == 401
                continue
            response = await client.post(WEBHOOK, content=raw + b' ', headers=headers)
            assert response.status_code == 401
        assert not await app.state.store.rows('SELECT * FROM recharge_deliveries')
        assert (await client.post(WEBHOOK, content=raw, headers=signed(raw))).status_code == 200
        changed = external.event(kind='InvoiceSettled')
        response = await client.post(WEBHOOK, content=changed, headers=signed(changed))
        assert response.status_code == 409
        assert not await app.state.store.rows('SELECT * FROM credit_entries')


async def test_settlement_transaction_rolls_back_all_money_and_receipt(tmp_path, monkeypatch):
    import athena.recharge as module
    async with stack(tmp_path) as (app, client, user, external, _):
        order = (await create(client)).json()
        external.settled()
        original = module.grant_in_transaction
        def interrupted(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('synthetic crash after credit before order transition')
        monkeypatch.setattr(module, 'grant_in_transaction', interrupted)
        with pytest.raises(RuntimeError, match='synthetic crash'):
            await deliver(client, external)
        assert not await app.state.store.rows('SELECT * FROM recharge_receipts')
        assert not await app.state.store.rows('SELECT * FROM credit_entries')
        assert (await app.state.credits.balance(user['id']))['balance_micros'] == 0
        assert (await client.get(BASE)).json()['orders'][0]['status'] == 'pending'
        assert (await app.state.store.rows('SELECT applied FROM recharge_deliveries')) == [{'applied': 0}]
        monkeypatch.setattr(module, 'grant_in_transaction', original)
        assert (await deliver(client, external)).status_code == 200
        assert (await client.get(BASE)).json()['orders'][0]['status'] == 'credited'
        assert len(await app.state.store.rows('SELECT * FROM credit_entries')) == 1


async def test_recharge_keeps_existing_holds_and_does_not_reenable_disabled_account(tmp_path):
    async with stack(tmp_path) as (app, client, user, external, _):
        await app.state.credits.grant(user['id'], 1000, 'existing-test-credit', 'synthetic')
        await app.state.credits.reserve(user['id'], 'existing-model-request', 600, 'request')
        await app.state.credits.uncertain('existing-model-request')
        await app.state.store.execute('UPDATE credit_accounts SET enabled=0 WHERE user_id=?', (user['id'],))
        await create(client)
        external.settled()
        assert (await deliver(client, external)).status_code == 200
        balance = await app.state.credits.balance(user['id'])
        assert balance['balance_micros'] == 5_001_000 and balance['held_micros'] == 600
        assert balance['available_micros'] == 5_000_400 and not balance['enabled']
        assert (await app.state.store.rows("SELECT status,charged_micros FROM credit_requests")) == [{'status': 'uncertain', 'charged_micros': None}]


async def test_order_ownership_read_rate_and_provider_configuration_binding(tmp_path):
    async with stack(tmp_path) as (app, client, _, external, _):
        order = (await create(client)).json()
        _, other = await app.state.auth.register('another-user', 'synthetic-password')
        assert (await client.get(BASE + '/' + order['id'], headers={'Authorization': 'Bearer ' + other})).status_code == 404
        assert not (await client.get(BASE, headers={'Authorization': 'Bearer ' + other})).json()['orders']
        before = len(external.calls)
        replies = await asyncio.gather(*(client.get(BASE + '/' + order['id']) for _ in range(20)))
        assert {reply.status_code for reply in replies} == {200}
        assert len(external.calls) == before + 1
        app.state.recharge.config = replace(app.state.recharge.config, store_id='a-new-store')
        value = (await client.get(BASE + '/' + order['id'])).json()
        assert value['status'] == 'pending' and value['checkout_url'] is None
        assert len(external.calls) == before + 1
        app.state.recharge.config = None
        assert (await client.get(BASE)).json()['enabled'] is False
        assert (await create(client)).json()['id'] == order['id']
        assert external.created == 1


async def test_manual_or_late_resolution_after_expiry_requires_review(tmp_path):
    async with stack(tmp_path) as (app, client, user, external, _):
        order = (await create(client)).json()
        external.invoices['invoice-1']['status'] = 'Expired'
        assert (await deliver(client, external, kind='InvoiceExpired')).status_code == 200
        assert (await client.get(BASE)).json()['orders'][0]['status'] == 'expired'
        external.settled()  # Even a clean-looking later snapshot cannot erase expiry.
        assert (await deliver(client, external, delivery='later-settlement')).status_code == 200
        assert (await client.get(BASE + '/' + order['id'])).json()['status'] == 'review'
        assert (await app.state.credits.balance(user['id']))['balance_micros'] == 0


async def test_signed_wrong_store_and_malformed_event_are_rejected(tmp_path):
    async with stack(tmp_path) as (app, client, _, external, _):
        await create(client)
        for fields in ({'storeId': 'other-store'}, {'type': []}, {'type': {}}, {'invoiceId': '../other'}):
            raw = external.event(**fields)
            assert (await client.post(WEBHOOK, content=raw, headers=signed(raw))).status_code == 400
        assert not await app.state.store.rows('SELECT * FROM recharge_deliveries')
        assert not await app.state.store.rows('SELECT * FROM credit_entries')


async def test_provider_redirect_does_not_forward_credentials_or_retry_post(tmp_path):
    async with stack(tmp_path) as (app, client, _, _, _):
        called = []
        def redirect(request):
            called.append(request)
            return httpx.Response(302, headers={'Location': 'https://other.example/collect-key'})
        await app.state.recharge.provider.http.aclose()
        app.state.recharge.provider.http = httpx.AsyncClient(transport=httpx.MockTransport(redirect))
        response = await create(client)
        assert response.status_code == 200 and response.json()['status'] == 'unknown'
        assert len(called) == 1 and called[0].url.host == 'payments.example'
        assert (await create(client)).json()['id'] == response.json()['id']
        assert len(called) == 1


async def test_whole_response_deadline_releases_provider_slot(tmp_path, monkeypatch):
    import athena.payment_provider as module
    class WaitingStream(httpx.AsyncByteStream):
        def __init__(self):
            self.closed = False
        async def __aiter__(self):
            yield b'{'
            await asyncio.Event().wait()
        async def aclose(self):
            self.closed = True
    async with stack(tmp_path) as (app, client, _, _, _):
        stream = WaitingStream()
        monkeypatch.setattr(module, 'REQUEST_TIMEOUT_SECONDS', .02)
        await app.state.recharge.provider.http.aclose()
        app.state.recharge.provider.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream)))
        response = await asyncio.wait_for(create(client), 1)
        assert response.status_code == 200 and response.json()['status'] == 'unknown'
        assert stream.closed
        assert app.state.recharge.network_slots._value == 4


async def test_invoice_receipt_cannot_be_applied_to_second_order(tmp_path):
    async with stack(tmp_path) as (app, client, user, external, _):
        await create(client)
        external.settled()
        assert (await deliver(client, external)).status_code == 200
        second = (await create(client, key='second-payment-order-123')).json()
        external.settled('invoice-2')['id'] = 'invoice-1'
        # The provider tries to reuse a settled payment identity for a new order.
        assert (await client.get(BASE + '/' + second['id'])).json()['status'] == 'review'
        assert (await app.state.credits.balance(user['id']))['balance_micros'] == 5_000_000
        assert len(await app.state.store.rows('SELECT * FROM recharge_receipts')) == 1


def test_config_is_explicit_and_secrets_are_not_in_repr_or_errors(tmp_path):
    assert load_recharge_config(None) is None
    (tmp_path / 'api.txt').write_text(KEY)
    (tmp_path / 'webhook.txt').write_text(SECRET)
    path = tmp_path / 'operator.json'
    values = {'provider': 'btcpay', 'base_url': 'https://payments.example', 'store_id': 'test-store',
              'api_key_file': 'api.txt', 'webhook_secret_file': 'webhook.txt', 'amounts_micros': [5_000_000]}
    path.write_text(json.dumps(values))
    config = load_recharge_config(path)
    assert config.api_key == KEY and config.webhook_secret == SECRET
    assert KEY not in repr(config) and SECRET not in repr(config)
    for extra in ({'base_url': 'http://payments.example'}, {'base_url': 'https://user:password@payments.example'},
                  {'amounts_micros': [True]}, {'amounts_micros': [5_000_000.0]}, {'amounts_micros': [5, 5]},
                  {'api_key_file': 'missing-' + KEY}, {'unexpected': KEY}):
        path.write_text(json.dumps({**values, **extra}))
        with pytest.raises(ValueError) as failure:
            load_recharge_config(path)
        assert KEY not in str(failure.value) and SECRET not in str(failure.value)


@pytest.mark.parametrize('value', [True, False, 5.0, float('nan'), 'NaN', 'Infinity', '1e9999999', '-1', '0.0000001',
                                  '4.999999999999999999999999999999', '5.000000000000000000000000000001',
                                  '0.00000099999999999999999999999999999999', '1e-9999999'])
def test_provider_money_rejects_lossy_or_nonfinite_values(value):
    with pytest.raises(ValueError):
        money_micros(value)


def test_provider_money_is_exact():
    assert money_micros(Decimal('0.000001')) == 1
    assert money_micros('10.000000') == 10_000_000
    assert money_micros(5) == 5_000_000
