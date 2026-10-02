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


def register_component_routes(app, exists):
    @app.post("/api/sessions/{session_id}/components/{component_id}/report")
    async def report(session_id: str, component_id: str, body: ComponentReport, request: Request):
        await exists(session_id, request)
        try:
            return await app.state.engine.tools.registry.report_component(session_id, component_id, **body.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid or unknown component observation") from exc
