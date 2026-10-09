"""Authenticated browser observations for versioned component registrations."""
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field


class ComponentReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    instance: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    state: Literal["mounted", "failed"]
    has_content: bool
    in_view: bool
    error_code: Literal["script_error", "unhandled_rejection", "blocked_resource", "empty_document", "load_timeout"] | None = None


class ComponentViewInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: int = Field(ge=1)
    view_revision: int = Field(ge=0)
    view: dict


class ComponentFinishInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    version: int = Field(ge=1)


def register_component_routes(app, exists):
    @app.post('/api/sessions/{session_id}/components/{component_id}/finish')
    async def finish(session_id: str, component_id: str, body: ComponentFinishInput, request: Request):
        await exists(session_id, request)
        result = await app.state.engine.tools.registry.finish_component(session_id, component_id, body.version)
        if result['changed']:
            await app.state.engine.emit(session_id, 'refresh', {})
        return result

    @app.post("/api/sessions/{session_id}/components/{component_id}/view")
    async def layout(session_id: str, component_id: str, body: ComponentViewInput, request: Request):
        await exists(session_id, request)
        from .component_views import update_view
        return await update_view(app.state.store, session_id, component_id, body.version, body.view_revision, body.view)

    @app.post("/api/sessions/{session_id}/components/{component_id}/report")
    async def report(session_id: str, component_id: str, body: ComponentReport, request: Request):
        await exists(session_id, request)
        try:
            return await app.state.engine.tools.registry.report_component(session_id, component_id, **body.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid or unknown component observation") from exc
