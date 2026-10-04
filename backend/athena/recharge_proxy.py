"""Account-scoped recharge transport; payment credentials stay in the gateway."""

from typing import Annotated

from fastapi import Path, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .errors import ServiceError


class RechargeInput(BaseModel):
    model_config = ConfigDict(extra='forbid')

    amount_micros: Annotated[StrictInt, Field(gt=0, le=1_000_000_000)]
    idempotency_key: str = Field(min_length=16, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')


def register_recharge_routes(app):
    disabled = {'enabled': False, 'amounts_micros': [], 'currency': 'USD', 'orders': []}

    async def forward(request, path, body=None):
        cloud = app.state.cloud
        if cloud is None:
            raise ServiceError('recharge_unavailable', 'Top-ups are not available.', 503)
        # Authentication middleware has already verified this local cookie with
        # the central service. No user ID or provider URL is accepted as input.
        token = await cloud.token(request.state.user['id'])
        return await cloud.call('/account/recharge' + path, token=token, body=body)

    @app.get('/api/account/recharge')
    async def options(request: Request):
        if app.state.cloud is None:
            return disabled
        try:
            return await forward(request, '')
        except ServiceError as error:
            if error.http_status != 404:
                raise
            # New desktop releases can precede gateway deployment. Only an
            # older gateway explicitly advertising disabled recharge permits
            # this fallback; never hide an enabled store's missing orders.
            cloud = app.state.cloud
            identity = await cloud.call('/auth/me', token=await cloud.token(request.state.user['id']))
            if identity.get('credit', {}).get('recharge_enabled') is False:
                return disabled
            raise error

    @app.post('/api/account/recharge')
    async def create(request: Request, body: RechargeInput):
        return await forward(request, '', body.model_dump())

    @app.get('/api/account/recharge/{order_id}')
    async def status(request: Request, order_id: Annotated[str, Path(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')]):
        return await forward(request, '/' + order_id)
