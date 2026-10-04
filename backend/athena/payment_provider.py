"""Small, non-custodial BTCPay invoice adapter. Never creates wallets or payouts.

Contract: https://docs.btcpayserver.org/API/Greenfield/v1/
Only invoice creation/read permissions are required on the configured store.
"""
import asyncio
from dataclasses import dataclass, field
from decimal import Decimal, DecimalException, localcontext
import hashlib
import hmac
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

import httpx

from .errors import ServiceError


IDENTIFIER = re.compile(r'[A-Za-z0-9_-]{1,128}')
MAX_AMOUNT = 1_000_000_000
REQUEST_TIMEOUT_SECONDS = 20


def strict_json(raw):
    def invalid(_):
        raise ValueError('Non-finite JSON number')
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('Duplicate JSON property')
            value[key] = item
        return value
    return json.loads(raw, parse_float=Decimal, parse_constant=invalid, object_pairs_hook=unique)


def money_micros(value):
    """Accept exact provider decimal strings/numbers, never binary floats/bools."""
    if type(value) not in (str, int, Decimal) or (isinstance(value, str) and len(value) > 40):
        raise ValueError('Invalid payment amount')
    try:
        amount = Decimal(value)
        if not amount.is_finite() or len(amount.as_tuple().digits) > 40 or not 0 <= amount <= Decimal(MAX_AMOUNT) / 1_000_000:
            raise ValueError('Invalid payment amount')
        # Multiplication under Decimal's default precision can round a tiny
        # underpayment up to the quoted amount. Compare before rounding, in a
        # bounded local context that preserves every accepted significant digit.
        with localcontext() as context:
            context.prec = 50
            if amount != amount.quantize(Decimal('0.000001')):
                raise ValueError('Invalid payment amount')
            return int(amount * 1_000_000)
    except (DecimalException, OverflowError):
        raise ValueError('Invalid payment amount') from None


def https_origin(value):
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) <= 32 for c in value) or '\\' in value:
        raise ValueError('A credential-free HTTPS URL is required')
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('A credential-free HTTPS URL is required')
    port = parsed.port or 443
    if not 1 <= port <= 65535:
        raise ValueError('Invalid HTTPS port')
    return parsed.hostname.lower(), port


@dataclass(frozen=True)
class RechargeConfig:
    base_url: str
    store_id: str
    api_key: str = field(repr=False)
    webhook_secret: str = field(repr=False)
    amounts_micros: tuple[int, ...] = (5_000_000, 10_000_000)

    def __post_init__(self):
        https_origin(self.base_url)
        if urlsplit(self.base_url).path not in ('', '/') or urlsplit(self.base_url).query:
            raise ValueError('Payment base URL must be an HTTPS origin')
        if not isinstance(self.store_id, str) or not IDENTIFIER.fullmatch(self.store_id):
            raise ValueError('Invalid payment store')
        for secret in (self.api_key, self.webhook_secret):
            if not isinstance(secret, str) or not 1 <= len(secret) <= 4096 or any(ord(c) < 32 for c in secret):
                raise ValueError('Invalid payment credential')
        if not self.amounts_micros or len(self.amounts_micros) > 20 or any(type(v) is not int or not 0 < v <= MAX_AMOUNT for v in self.amounts_micros):
            raise ValueError('Invalid recharge amounts')
        if len(set(self.amounts_micros)) != len(self.amounts_micros):
            raise ValueError('Duplicate recharge amounts')

    @property
    def scope(self):
        return hashlib.sha256((self.base_url.rstrip('/') + '\n' + self.store_id).encode()).hexdigest()

    def checkout_url(self, value):
        if https_origin(value) != https_origin(self.base_url):
            raise ValueError('Unexpected payment checkout origin')
        return value


def load_recharge_config(filename):
    if not filename:
        return None
    path = Path(filename).expanduser()
    try:
        with path.open('rb') as handle:
            raw = handle.read(16_385)
        if len(raw) > 16_384:
            raise ValueError('Payment configuration too large')
        values = strict_json(raw)
        allowed = {'provider', 'base_url', 'store_id', 'api_key_file', 'webhook_secret_file', 'amounts_micros'}
        if not isinstance(values, dict) or values.keys() - allowed or values.get('provider', 'btcpay') != 'btcpay':
            raise ValueError('Invalid payment configuration')
        def secret(name):
            secret_path = Path(values[name]).expanduser()
            if not secret_path.is_absolute():
                secret_path = path.parent / secret_path
            with secret_path.open('r', encoding='utf-8') as handle:
                value = handle.read(4097).strip()
            return value
        return RechargeConfig(values['base_url'].rstrip('/'), values['store_id'], secret('api_key_file'),
                              secret('webhook_secret_file'), tuple(values['amounts_micros']))
    except (OSError, UnicodeError, TypeError, KeyError, ValueError):
        # Credential paths, values and provider response bodies never reach logs.
        raise ValueError('Recharge configuration is invalid or unreadable') from None


class BTCPayProvider:
    def __init__(self, config):
        self.config = config
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5), follow_redirects=False)

    async def close(self):
        await self.http.aclose()

    async def request(self, method, path, *, body=None, params=None):
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                async with self.http.stream(method, self.config.base_url.rstrip('/') + path,
                                            headers={'Authorization': 'token ' + self.config.api_key},
                                            json=body, params=params) as response:
                    if response.status_code != 200:
                        raise ValueError('Payment provider rejected request')
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 262_144:
                            raise ValueError('Payment response too large')
                    return strict_json(data)
        except (httpx.HTTPError, TimeoutError, ValueError, UnicodeError):
            raise ServiceError('recharge_provider_unavailable', 'Payment service is temporarily unavailable.', 503) from None

    async def create(self, order_id, amount_micros):
        # Greenfield does not promise create idempotency. The durable order owns
        # the single POST attempt; an unknown outcome is reconciled by GET only.
        return await self.request('POST', f'/api/v1/stores/{self.config.store_id}/invoices', body={
            'amount': format(Decimal(amount_micros) / 1_000_000, 'f'), 'currency': 'USD',
            'metadata': {'orderId': order_id, 'athenaRecharge': 'v1'},
            'checkout': {'paymentTolerance': 0, 'redirectAutomatically': False},
        })

    async def invoice(self, invoice_id):
        if not isinstance(invoice_id, str) or not IDENTIFIER.fullmatch(invoice_id):
            raise ValueError('Invalid invoice identifier')
        return await self.request('GET', '/api/v1/invoices/' + invoice_id)

    async def find(self, order_id):
        return await self.request('GET', f'/api/v1/stores/{self.config.store_id}/invoices',
                                  params={'orderId': order_id, 'take': 2})

    def event(self, raw, signature):
        expected = 'sha256=' + hmac.new(self.config.webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not re.fullmatch(r'sha256=[0-9a-f]{64}', signature) or not hmac.compare_digest(expected, signature):
            raise ServiceError('payment_signature_invalid', 'Invalid payment notification.', 401)
        value = strict_json(raw)
        if not isinstance(value, dict) or value.get('storeId') != self.config.store_id:
            raise ServiceError('payment_notification_invalid', 'Invalid payment notification.', 400)
        for key in ('deliveryId', 'invoiceId'):
            if not isinstance(value.get(key), str) or not IDENTIFIER.fullmatch(value[key]):
                raise ServiceError('payment_notification_invalid', 'Invalid payment notification.', 400)
        if not isinstance(value.get('type'), str) or len(value['type']) > 80:
            raise ServiceError('payment_notification_invalid', 'Invalid payment notification.', 400)
        return value
