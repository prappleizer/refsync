"""Shared helpers for routers: app state access and common lookups."""

from fastapi import HTTPException, Request

from .. import store


async def require_project(request: Request, pid: str) -> dict:
    project = await store.get_project(request.app.state.db.conn, pid)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project
