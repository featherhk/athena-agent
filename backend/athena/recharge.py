"""Durable service-credit orders, isolated from the agent's execution plane.

Only exact, independently read BTCPay settlement can add paid credit. No wallet,
withdrawal, refund or manual invoice-marking API is implemented here.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import time

import anyio

from .credits import grant_in_transaction
from .errors import ServiceError
from .payment_provider import BTCPayProvider, IDENTIFIER, MAX_AMOUNT, money_micros
from .store import now, uid


PUBLIC_FIELDS = ('id', 'amount_micros', 'currency', 'status', 'checkout_url', 'created_at', 'updated_at')
EVENTS = {'InvoiceCreated', 'InvoiceReceivedPayment', 'InvoicePaymentSettled', 'InvoiceProcessing',
          'InvoiceExpired', 'InvoiceSettled', 'InvoiceInvalid'}


async def initialize_recharge(store):
    def create(db):
        db.executescript("""
        CREATE TABLE IF NOT EXISTS recharge_orders (
          id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
          idempotency_key TEXT NOT NULL, amount_micros INTEGER NOT NULL CHECK(amount_micros>0),
          currency TEXT NOT NULL CHECK(currency='USD'),
          status TEXT NOT NULL CHECK(status IN ('creating','pending','confirming','credited','expired','review','unknown')),
          provider_scope TEXT NOT NULL, provider_id TEXT, checkout_url TEXT,
          diagnostic_code TEXT, refresh_after INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          UNIQUE(user_id,idempotency_key), UNIQUE(provider_scope,provider_id));
        CREATE INDEX IF NOT EXISTS recharge_user_orders ON recharge_orders(user_id,created_at);
        CREATE TABLE IF NOT EXISTS recharge_deliveries (
          provider_scope TEXT NOT NULL, delivery_id TEXT NOT NULL, body_hash TEXT NOT NULL,
          event_type TEXT NOT NULL, provider_id TEXT NOT NULL, received_at TEXT NOT NULL,
          applied INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(provider_scope,delivery_id));
        CREATE TABLE IF NOT EXISTS recharge_receipts (
          provider_scope TEXT NOT NULL, provider_id TEXT NOT NULL,
          order_id TEXT NOT NULL UNIQUE REFERENCES recharge_orders(id),
          amount_micros INTEGER NOT NULL, evidence_hash TEXT NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY(provider_scope,provider_id));
        """)
        # A POST interrupted by restart is never silently issued again.
        db.execute("UPDATE recharge_orders SET status='unknown',diagnostic_code='creation_interrupted',updated_at=? WHERE status='creating'", (now(),))
    await store.call(create)


class Recharge:
    def __init__(self, store, config=None):
        self.store, self.config = store, config
        self.provider = BTCPayProvider(config) if config else None
        self.network_slots = asyncio.Semaphore(4)

    async def close(self):
        if self.provider:
            await self.provider.close()

    def public(self, row):
        value = {key: row[key] for key in PUBLIC_FIELDS}
        if (not self.config or row['provider_scope'] != self.config.scope or
                row['status'] not in {'pending', 'confirming'}):
            value['checkout_url'] = None
        return value

    async def overview(self, user_id):
        rows = await self.store.rows('SELECT * FROM recharge_orders WHERE user_id=? ORDER BY created_at DESC,id DESC LIMIT 50', (user_id,))
        return {'enabled': bool(self.config), 'amounts_micros': sorted(self.config.amounts_micros) if self.config else [],
                'currency': 'USD', 'orders': [self.public(row) for row in rows]}

    async def owned(self, user_id, order_id):
        rows = await self.store.rows('SELECT * FROM recharge_orders WHERE id=? AND user_id=?', (order_id, user_id))
        if not rows:
            raise ServiceError('recharge_not_found', 'Order not found.', 404)
        return rows[0]

    async def create(self, user_id, amount, key):
        if type(amount) is not int or not 0 < amount <= MAX_AMOUNT or not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', key):
            raise ServiceError('invalid_input', 'Invalid recharge order.', 400)
        candidate_id = uid()
        async def unknown(identifier):
            with anyio.CancelScope(shield=True):
                await self.store.execute("UPDATE recharge_orders SET status='unknown',diagnostic_code='creation_unknown',updated_at=? WHERE id=? AND status='creating'", (now(), identifier))
        def reserve(db):
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM recharge_orders WHERE user_id=? AND idempotency_key=?', (user_id, key)).fetchone()
            if old:
                if old['amount_micros'] != amount:
                    raise ServiceError('recharge_conflict', 'This order identifier was already used.', 409)
                return dict(old), False
            if not self.config:
                raise ServiceError('recharge_unavailable', 'Recharge is not available.', 503)
            if amount not in self.config.amounts_micros:
                raise ServiceError('recharge_amount_unavailable', 'Unsupported recharge amount.', 400)
            if db.execute("SELECT 1 FROM recharge_orders WHERE user_id=? AND status IN ('creating','unknown','review')", (user_id,)).fetchone():
                raise ServiceError('recharge_review_required', 'An existing payment needs review first.', 409)
            # Bound disposable invoices without blocking same-key recovery.
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
            if db.execute('SELECT COUNT(*) FROM recharge_orders WHERE user_id=? AND created_at>?', (user_id, cutoff)).fetchone()[0] >= 10:
                raise ServiceError('recharge_rate_limited', 'Please wait before creating another order.', 429)
            identifier, stamp = candidate_id, now()
            db.execute("INSERT INTO recharge_orders(id,user_id,idempotency_key,amount_micros,currency,status,provider_scope,created_at,updated_at) VALUES (?,?,?,?,'USD','creating',?,?,?)",
                       (identifier, user_id, key, amount, self.config.scope, stamp, stamp))
            return dict(db.execute('SELECT * FROM recharge_orders WHERE id=?', (identifier,)).fetchone()), True
        try:
            row, fresh = await self.store.call(reserve)
        except asyncio.CancelledError:
            # Cancellation cannot undo a DB-worker commit whose result has not
            # reached the event loop. Queue this after admission and mark only
            # this call's candidate, never an existing same-key owner's order.
            await unknown(candidate_id)
            raise
        if not fresh:
            return self.public(row)
        try:
            async with self.network_slots:
                invoice = await self.provider.create(row['id'], amount)
            await self.apply(row['id'], invoice, allow_credit=False)
        except (ServiceError, asyncio.CancelledError) as error:
            await unknown(row['id'])
            if isinstance(error, asyncio.CancelledError):
                raise
        return self.public(await self.owned(user_id, row['id']))

    def inspect(self, row, invoice):
        if not isinstance(invoice, dict):
            raise ValueError('invalid_invoice')
        invoice_id = invoice.get('id')
        if not isinstance(invoice_id, str) or not IDENTIFIER.fullmatch(invoice_id):
            raise ValueError('invalid_invoice')
        metadata = invoice.get('metadata')
        if (invoice.get('storeId') != self.config.store_id or invoice.get('currency') != 'USD' or
                not isinstance(metadata, dict) or metadata.get('orderId') != row['id'] or metadata.get('athenaRecharge') != 'v1' or
                money_micros(invoice.get('amount')) != row['amount_micros'] or
                (row['provider_id'] and row['provider_id'] != invoice_id)):
            raise ValueError('invoice_mismatch')
        checkout = self.config.checkout_url(invoice.get('checkoutLink'))
        if invoice.get('additionalStatus') != 'None':
            raise ValueError('payment_exception')
        provider_status = invoice.get('status')
        if not isinstance(provider_status, str):
            raise ValueError('invalid_invoice_status')
        status = {'New': 'pending', 'Processing': 'confirming', 'Expired': 'expired', 'Settled': 'credited'}.get(provider_status)
        if not status:
            raise ValueError('payment_exception')
        paid = money_micros(invoice.get('paidAmount'))
        if status in {'confirming', 'credited'} and paid != row['amount_micros']:
            raise ValueError('payment_amount_mismatch')
        if status in {'pending', 'expired'} and paid:
            raise ValueError('payment_exception')
        evidence = {'id': invoice_id, 'store': self.config.store_id, 'order': row['id'], 'amount': row['amount_micros'],
                    'paid': paid, 'status': invoice['status'], 'additionalStatus': 'None'}
        return invoice_id, checkout, status, hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()

    async def apply(self, order_id, invoice, *, allow_credit, flagged=False, delivery=None):
        def commit(db):
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM recharge_orders WHERE id=?', (order_id,)).fetchone()
            if not row or not self.config or row['provider_scope'] != self.config.scope:
                return
            def delivered():
                if delivery:
                    db.execute('UPDATE recharge_deliveries SET applied=1 WHERE provider_scope=? AND delivery_id=?', (self.config.scope, delivery))
            if row['status'] in {'credited', 'review'}:
                delivered()
                return
            try:
                if flagged:
                    raise ValueError('notification_exception')
                invoice_id, checkout, status, evidence = self.inspect(row, invoice)
                if row['status'] == 'expired' and status != 'expired':
                    raise ValueError('payment_after_expiry')
                other = db.execute('SELECT id FROM recharge_orders WHERE provider_scope=? AND provider_id=? AND id<>?',
                                   (self.config.scope, invoice_id, order_id)).fetchone()
                if other:
                    raise ValueError('invoice_already_bound')
            except ValueError:
                # No payment URL survives an invalid identity, amount or receipt.
                db.execute("UPDATE recharge_orders SET status='review',checkout_url=NULL,diagnostic_code='invoice_review',updated_at=? WHERE id=?", (now(), order_id))
                delivered()
                return
            if status == 'credited' and not allow_credit:
                status = 'confirming'
            if row['status'] == 'confirming' and status == 'pending':
                status = 'confirming'
            if status == 'credited':
                db.execute('INSERT INTO recharge_receipts VALUES (?,?,?,?,?,?)',
                           (self.config.scope, invoice_id, order_id, row['amount_micros'], evidence, now()))
                # The receipt, credit entry, balance and order transition commit
                # together on the single DB worker. No await/crash window here.
                grant_in_transaction(db, row['user_id'], row['amount_micros'], 'recharge:' + order_id, 'Paid Athena service credit', enable_existing=False)
            db.execute('UPDATE recharge_orders SET provider_id=?,checkout_url=?,status=?,diagnostic_code=NULL,updated_at=? WHERE id=?',
                       (invoice_id, checkout, status, now(), order_id))
            delivered()
        await self.store.call(commit)

    async def get(self, user_id, order_id):
        row = await self.owned(user_id, order_id)
        if (not self.config or row['provider_scope'] != self.config.scope or
                row['status'] in {'creating', 'review', 'credited', 'expired'}):
            return self.public(row)
        def claim(db):
            db.execute('BEGIN IMMEDIATE')
            tick = int(time.time())
            return db.execute('UPDATE recharge_orders SET refresh_after=? WHERE id=? AND refresh_after<=?', (tick + 10, order_id, tick)).rowcount == 1
        if not await self.store.call(claim):
            return self.public(await self.owned(user_id, order_id))
        async with self.network_slots:
            if row['provider_id']:
                invoice = await self.provider.invoice(row['provider_id'])
            else:
                found = await self.provider.find(order_id)
                if not isinstance(found, list) or len(found) > 1:
                    await self.apply(order_id, None, allow_credit=False)
                    return self.public(await self.owned(user_id, order_id))
                if not found:
                    return self.public(await self.owned(user_id, order_id))
                invoice = found[0]
        await self.apply(order_id, invoice, allow_credit=True)
        return self.public(await self.owned(user_id, order_id))

    async def webhook(self, raw, signature):
        if not self.provider:
            raise ServiceError('recharge_unavailable', 'Recharge is not available.', 503)
        event = self.provider.event(raw, signature)
        if event.get('type') not in EVENTS:
            return {'ok': True}
        digest = hashlib.sha256(raw).hexdigest()
        def remember(db):
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM recharge_deliveries WHERE provider_scope=? AND delivery_id=?', (self.config.scope, event['deliveryId'])).fetchone()
            if old:
                if old['body_hash'] != digest:
                    raise ServiceError('payment_notification_conflict', 'Conflicting payment notification.', 409)
                return bool(old['applied'])
            db.execute('INSERT INTO recharge_deliveries(provider_scope,delivery_id,body_hash,event_type,provider_id,received_at) VALUES (?,?,?,?,?,?)',
                       (self.config.scope, event['deliveryId'], digest, event['type'], event['invoiceId'], now()))
            return False
        if await self.store.call(remember):
            return {'ok': True}
        async with self.network_slots:
            invoice = await self.provider.invoice(event['invoiceId'])
        metadata = invoice.get('metadata') if isinstance(invoice, dict) else None
        rows = await self.store.rows('SELECT * FROM recharge_orders WHERE provider_scope=? AND provider_id=?', (self.config.scope, event['invoiceId']))
        if not rows and isinstance(metadata, dict) and isinstance(metadata.get('orderId'), str):
            rows = await self.store.rows('SELECT * FROM recharge_orders WHERE provider_scope=? AND id=? AND provider_id IS NULL', (self.config.scope, metadata['orderId']))
        if not rows:
            # Keep unmatched signed notifications for later read-only recovery.
            return {'ok': True}
        row = rows[0]
        event_metadata = event.get('metadata')
        flagged = (not isinstance(event_metadata, dict) or event_metadata.get('orderId') != row['id'] or
                   event_metadata.get('athenaRecharge') != 'v1' or
                   not isinstance(invoice, dict) or invoice.get('id') != event['invoiceId'])
        if event['type'] == 'InvoiceSettled':
            flagged = flagged or event.get('manuallyMarked') is not False or event.get('overPaid') is not False
        elif event.get('manuallyMarked') is True:
            flagged = True
        await self.apply(row['id'], invoice, allow_credit=event['type'] == 'InvoiceSettled', flagged=flagged, delivery=event['deliveryId'])
        return {'ok': True}
