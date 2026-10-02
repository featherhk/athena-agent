"""Integer USD microcredit ledger. Reservations and settlement share the DB worker.

Unknown outcomes retain their holds; restarting never grants money back or replays
a provider request. Operator reconciliation is explicit and audited.
"""
from .errors import ServiceError
from .store import now, uid


async def initialize_credits(store):
    def create(db):
        db.executescript("""
        CREATE TABLE IF NOT EXISTS credit_accounts (
          user_id TEXT PRIMARY KEY REFERENCES users(id), enabled INTEGER NOT NULL DEFAULT 0,
          balance_micros INTEGER NOT NULL DEFAULT 0 CHECK(balance_micros>=0));
        CREATE TABLE IF NOT EXISTS credit_entries (
          id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
          operation_key TEXT NOT NULL UNIQUE, delta_micros INTEGER NOT NULL,
          reason TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS credit_requests (
          id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
          fingerprint TEXT NOT NULL, reserved_micros INTEGER NOT NULL CHECK(reserved_micros>=0),
          charged_micros INTEGER, status TEXT NOT NULL, usage TEXT,
          created_at TEXT NOT NULL, finished_at TEXT);
        CREATE INDEX IF NOT EXISTS credit_pending ON credit_requests(user_id,status);
        """)
    await store.call(create)


class Credits:
    def __init__(self, store):
        self.store = store

    async def balance(self, user_id):
        def read(db):
            row = db.execute("SELECT * FROM credit_accounts WHERE user_id=?", (user_id,)).fetchone()
            held = db.execute("SELECT COALESCE(SUM(reserved_micros),0) FROM credit_requests WHERE user_id=? AND status IN ('reserved','uncertain')", (user_id,)).fetchone()[0]
            balance = row['balance_micros'] if row else 0
            return {'currency': 'USD', 'enabled': bool(row and row['enabled']),
                    'balance_micros': balance, 'held_micros': held,
                    'available_micros': max(0, balance-held), 'recharge_enabled': False}
        return await self.store.call(read)

    async def grant(self, user_id, amount, operation_key, reason):
        if type(amount) is not int or amount <= 0 or not reason.strip():
            raise ValueError('Positive integer microcredits and a reason are required')
        def change(db):
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute("SELECT * FROM credit_entries WHERE operation_key=?", (operation_key,)).fetchone()
            if prior:
                if prior['user_id'] != user_id or prior['delta_micros'] != amount:
                    raise ValueError('Grant idempotency conflict')
                return False
            db.execute("INSERT INTO credit_accounts(user_id,enabled,balance_micros) VALUES (?,1,?) ON CONFLICT(user_id) DO UPDATE SET enabled=1,balance_micros=balance_micros+excluded.balance_micros", (user_id,amount))
            db.execute("INSERT INTO credit_entries VALUES (?,?,?,?,?,?)", (uid(),user_id,operation_key,amount,reason,now()))
            return True
        return await self.store.call(change)

    async def reserve(self, user_id, request_id, amount, fingerprint):
        if type(amount) is not int or amount < 1:
            raise ValueError('Reservation must be positive')
        def change(db):
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM credit_requests WHERE id=?", (request_id,)).fetchone():
                raise ServiceError('request_already_submitted', 'This request was already submitted; check its existing result.',409)
            row = db.execute("SELECT * FROM credit_accounts WHERE user_id=?", (user_id,)).fetchone()
            held = db.execute("SELECT COALESCE(SUM(reserved_micros),0) FROM credit_requests WHERE user_id=? AND status IN ('reserved','uncertain')", (user_id,)).fetchone()[0]
            if not row or not row['enabled'] or row['balance_micros']-held < amount:
                raise ServiceError('account_balance_insufficient', 'Your account has no available credit for this request.',402)
            db.execute("INSERT INTO credit_requests VALUES (?,?,?,?,NULL,'reserved',NULL,?,NULL)", (request_id,user_id,fingerprint,amount,now()))
        await self.store.call(change)

    async def settle(self, request_id, amount, usage='{}'):
        if type(amount) is not int or amount < 0:
            raise ValueError('Invalid charge')
        def change(db):
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM credit_requests WHERE id=?", (request_id,)).fetchone()
            if not row:
                raise ValueError('Unknown reservation')
            if row['status'] == 'settled':
                if row['charged_micros'] != amount:
                    raise ValueError('Settlement conflict')
                return False
            if amount > row['reserved_micros']:
                # A violated cost ceiling needs operator review, never overspend.
                db.execute("UPDATE credit_requests SET status='uncertain',usage=? WHERE id=?", (usage,request_id))
                return False
            db.execute("UPDATE credit_accounts SET balance_micros=balance_micros-? WHERE user_id=?", (amount,row['user_id']))
            db.execute("UPDATE credit_requests SET status='settled',charged_micros=?,usage=?,finished_at=? WHERE id=?", (amount,usage,now(),request_id))
            db.execute("INSERT INTO credit_entries VALUES (?,?,?,?,?,?)", (uid(),row['user_id'],'request:'+request_id,-amount,'provider usage',now()))
            return True
        return await self.store.call(change)

    async def uncertain(self, request_id):
        await self.store.execute("UPDATE credit_requests SET status='uncertain' WHERE id=? AND status='reserved'", (request_id,))
