import asyncio
import pytest
from athena.credits import Credits, initialize_credits
from athena.auth import Auth
from athena.errors import ServiceError
from athena.store import Store


@pytest.fixture
async def ledger(tmp_path):
    store=Store(tmp_path/'accounts.sqlite3')
    await store.initialize()
    await initialize_credits(store)
    user,_=await Auth(store,bootstrap_owner=False).register('credit-user','test-password')
    yield store,Credits(store),user['id']
    await store.close()


async def test_new_accounts_have_no_model_access(ledger):
    _,credits,user=ledger
    assert (await credits.balance(user))['available_micros']==0
    with pytest.raises(ServiceError) as error:
        await credits.reserve(user,'request-a',1,'payload')
    assert error.value.http_status==402


async def test_concurrent_reservations_cannot_double_spend(ledger):
    _,credits,user=ledger
    assert await credits.grant(user,100,'grant-once','test credit')
    assert not await credits.grant(user,100,'grant-once','test credit')
    results=await asyncio.gather(*(credits.reserve(user,f'request-{i}',60,'payload') for i in range(12)),return_exceptions=True)
    assert sum(value is None for value in results)==1
    assert (await credits.balance(user))['available_micros']==40


async def test_uncertain_holds_survive_reopen_and_settle_once(ledger):
    store,credits,user=ledger
    await credits.grant(user,100,'grant','test credit')
    await credits.reserve(user,'request-a',60,'payload')
    await credits.uncertain('request-a')
    reopened=Credits(store)
    assert (await reopened.balance(user))['available_micros']==40
    with pytest.raises(ServiceError):
        await reopened.reserve(user,'request-a',60,'payload')
    assert await reopened.settle('request-a',17)
    assert not await reopened.settle('request-a',17)
    assert (await reopened.balance(user))['available_micros']==83
    entries=await store.rows("SELECT * FROM credit_entries WHERE operation_key='request:request-a'")
    assert len(entries)==1


async def test_invalid_report_keeps_hold(ledger):
    _,credits,user=ledger
    await credits.grant(user,100,'grant','test credit')
    await credits.reserve(user,'request-a',60,'payload')
    assert not await credits.settle('request-a',61)
    assert (await credits.balance(user))['available_micros']==40


async def test_independent_connections_share_atomic_budget(ledger):
    store,credits,user=ledger
    await credits.grant(user,100,'grant','test credit')
    other=Store(store.path)
    try:
        second=Credits(other)
        results=await asyncio.gather(*( (credits if i%2 else second).reserve(user,f'parallel-{i}',60,'body') for i in range(20)),return_exceptions=True)
        assert sum(result is None for result in results)==1
        assert (await credits.balance(user))['held_micros']==60
    finally:
        await other.close()
