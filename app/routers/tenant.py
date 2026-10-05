"""Public tenant resolution endpoints (Host / subdomain aware)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.tenant_host import (
    caddy_ask_allows_host,
    normalize_host,
    request_host,
    resolution_public_dict,
    resolve_tenant_host,
)

router = APIRouter(tags=["Tenant"])


@router.get("/tenant/current")
def current_tenant(request: Request, db: Session = Depends(get_db)):
    """Return fulfillment resolved from the current Host.

    Prefer values already computed by middleware; fall back to a fresh DB lookup.
    """
    cached = getattr(request.state, "tenant_resolution", None)
    if cached is not None:
        return resolution_public_dict(cached)

    host = request_host(
        request.headers.get("host"),
        request.headers.get("x-forwarded-host"),
    )
    resolution = resolve_tenant_host(db, host)
    return resolution_public_dict(resolution)


@router.get("/tenant/resolve")
def resolve_tenant_host_endpoint(
    host: str = Query(min_length=1, max_length=255),
    db: Session = Depends(get_db),
):
    """Explicit host lookup (useful for admin UI / diagnostics)."""
    return resolution_public_dict(resolve_tenant_host(db, host))


@router.get("/internal/caddy-ask", include_in_schema=False)
def caddy_on_demand_ask(
    domain: str | None = Query(default=None),
    host: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Caddy on_demand TLS ask endpoint.

    Caddy calls: GET /internal/caddy-ask?domain=<host>
    200 => allow certificate, non-2xx => reject.
    """
    value = normalize_host(domain or host)
    if not value or not caddy_ask_allows_host(db, value):
        from fastapi.responses import PlainTextResponse

        return PlainTextResponse("deny", status_code=404)
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse("ok", status_code=200)
