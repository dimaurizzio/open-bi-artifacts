"""API del panel Conocimiento: leer, proponer, aprobar o rechazar, deprecar, sincronizar y exportar."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..knowledge.okf_export import ExportError
from ..knowledge.service import KnowledgeError, KnowledgeService
from ..knowledge.service import service as default_service

_STATUS = {"missing": 404, "forbidden": 403}


def _error(e: Exception) -> JSONResponse:
    code = _STATUS.get(getattr(e, "code", ""), 400)
    return JSONResponse({"detail": str(e)}, status_code=code)


class Proposal(BaseModel):
    target_type: str
    target_ref: str
    kind: str
    content: str
    visibility: str = "private"
    sql: str | None = None
    sources: list[Any] = []
    stale_after: str | None = None
    supersedes_id: str | None = None


class Review(BaseModel):
    action: str  # approve | reject
    note: str | None = None


class Note(BaseModel):
    note: str | None = None


def build_router(current_user: Callable[[Request], str], svc: KnowledgeService = default_service) -> APIRouter:
    r = APIRouter(prefix="/api/knowledge")

    @r.get("")
    async def overview(request: Request):
        """Tablas del catálogo y conceptos documentados, con conteos por estado."""
        return svc.overview(current_user(request))

    @r.get("/entries")
    async def entries(
        request: Request,
        target: str | None = None,
        q: str | None = None,
        status: str | None = None,
        mine: bool = False,
        all: bool = False,  # noqa: A002 - incluye rechazadas y deprecadas
    ):
        user = current_user(request)
        if q:
            found = svc.search(q, user, limit=50)
            return {"entries": found["entries"], "catalog": found["catalog"]}
        return {"entries": svc.list(user, status=status, target=target, mine=mine, include_inactive=all)}

    @r.get("/target/{ref:path}")
    async def target(ref: str, request: Request):
        return svc.lookup(ref, current_user(request))

    @r.post("/entries")
    async def propose(body: Proposal, request: Request):
        try:
            return await svc.propose(body.model_dump(), current_user(request), proposed_via="manual")
        except KnowledgeError as e:
            return _error(e)

    @r.post("/entries/{entry_id}/review")
    async def review(entry_id: str, body: Review, request: Request):
        if body.action not in ("approve", "reject"):
            return JSONResponse({"detail": "action es 'approve' o 'reject'."}, status_code=400)
        try:
            return await svc.review(entry_id, current_user(request), body.action == "approve", body.note)
        except KnowledgeError as e:
            return _error(e)

    @r.post("/entries/{entry_id}/deprecate")
    async def deprecate(entry_id: str, body: Note, request: Request):
        try:
            return svc.deprecate(entry_id, current_user(request), body.note)
        except KnowledgeError as e:
            return _error(e)

    @r.get("/entries/{entry_id}/history")
    async def history(entry_id: str, request: Request):
        try:
            return svc.history(entry_id, current_user(request))
        except KnowledgeError as e:
            return _error(e)

    @r.post("/catalog/sync")
    async def sync(request: Request):
        if not svc.is_curator(current_user(request)):
            return JSONResponse({"detail": "Solo un curador sincroniza el catálogo."}, status_code=403)
        return await svc.sync_catalog()

    @r.post("/export")
    async def export(request: Request):
        if not svc.is_curator(current_user(request)):
            return JSONResponse({"detail": "Solo un curador exporta el bundle."}, status_code=403)
        try:
            return svc.export()
        except ExportError as e:
            return _error(e)

    return r
