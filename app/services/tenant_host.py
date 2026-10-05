"""Resolve fulfillment (tenant) from HTTP Host / X-Forwarded-Host.

Backend-only tenancy entrypoint. Does not change warehouse business routes yet;
callers read request.state set by middleware.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.fulfillment import Fulfillment
from app.services.fulfillment_platform import fulfillment_to_read

ResolutionKind = Literal["platform", "subdomain", "custom_domain", "default", "unknown"]

RESERVED_SUBDOMAINS = frozenset(
    {
        "www",
        "api",
        "admin",
        "platform",
        "app",
        "static",
        "mail",
        "ftp",
        "caddy",
        "health",
        "status",
        "cdn",
        "ns1",
        "ns2",
    }
)

_HOST_RE = re.compile(r"[^a-z0-9.-]+")


@dataclass(frozen=True)
class TenantResolution:
    kind: ResolutionKind
    host: str
    subdomain: str | None = None
    fulfillment: Fulfillment | None = None
    allowed: bool = True
    reason: str | None = None

    @property
    def fulfillment_id(self):
        return self.fulfillment.id if self.fulfillment else None

    @property
    def fulfillment_code(self) -> str | None:
        return self.fulfillment.code if self.fulfillment else None


def normalize_host(raw: str | None) -> str:
    host = (raw or "").strip().lower()
    if not host:
        return ""
    # Strip brackets for IPv6 and any trailing path leftovers.
    if host.startswith("[") and "]" in host:
        host = host[1 : host.index("]")]
    if ":" in host and not host.count(":") > 1:
        # hostname:port (not bare IPv6)
        host = host.rsplit(":", 1)[0]
    host = _HOST_RE.sub("", host).strip(".")
    return host[:255]


def platform_base_domain() -> str:
    return normalize_host(getattr(settings, "platform_base_domain", "") or "")


def platform_host_aliases() -> set[str]:
    raw = str(getattr(settings, "platform_host_aliases", "") or "")
    aliases = {normalize_host(part) for part in raw.split(",") if part.strip()}
    base = platform_base_domain()
    if base:
        aliases.add(base)
    # Local / compose defaults always treated as platform apex.
    aliases.update({"localhost", "127.0.0.1", "0.0.0.0", "api", "fulfillcore-api"})
    return {a for a in aliases if a}


def tenant_host_mode() -> str:
    mode = str(getattr(settings, "tenant_host_mode", "soft") or "soft").strip().lower()
    return mode if mode in {"off", "soft", "strict"} else "soft"


def extract_subdomain(host: str, base_domain: str) -> str | None:
    if not host or not base_domain or host == base_domain:
        return None
    suffix = "." + base_domain
    if not host.endswith(suffix):
        return None
    label = host[: -len(suffix)]
    if not label or "." in label:
        # Only one-level subdomains: fc-spb.example.ru
        return None
    return label


def is_reserved_subdomain(subdomain: str | None) -> bool:
    return bool(subdomain) and subdomain in RESERVED_SUBDOMAINS


def request_host(headers_host: str | None, forwarded_host: str | None = None) -> str:
    # Prefer the outer edge host when present (Caddy / nginx).
    forwarded = normalize_host((forwarded_host or "").split(",")[0])
    if forwarded:
        return forwarded
    return normalize_host(headers_host)


def _active_enough(fc: Fulfillment) -> tuple[bool, str | None]:
    if fc.status == "ARCHIVED":
        return False, "Фулфилмент архивирован"
    if fc.status == "SUSPENDED":
        return False, "Фулфилмент приостановлен"
    if fc.status in {"DRAFT", "ONBOARDING"}:
        return False, "Фулфилмент ещё не активирован"
    return True, None


def resolve_tenant_host(db: Session, host: str) -> TenantResolution:
    host = normalize_host(host)
    if not host:
        return TenantResolution(kind="unknown", host="", allowed=True, reason="empty_host")

    aliases = platform_host_aliases()
    base = platform_base_domain()
    mode = tenant_host_mode()

    if host in aliases:
        default = db.execute(
            select(Fulfillment).where(Fulfillment.is_default.is_(True)).order_by(Fulfillment.created_at.asc())
        ).scalars().first()
        return TenantResolution(
            kind="platform" if not default else "default",
            host=host,
            fulfillment=default,
            allowed=True,
            reason="platform_apex",
        )

    # Exact custom domain match first (works even without PLATFORM_BASE_DOMAIN).
    custom = db.execute(
        select(Fulfillment).where(func.lower(Fulfillment.custom_domain) == host)
    ).scalars().first()
    if custom:
        ok, reason = _active_enough(custom)
        return TenantResolution(
            kind="custom_domain",
            host=host,
            fulfillment=custom,
            allowed=ok if mode == "strict" else True,
            reason=None if ok else reason,
        )

    subdomain = extract_subdomain(host, base) if base else None
    if subdomain:
        if is_reserved_subdomain(subdomain):
            return TenantResolution(
                kind="platform",
                host=host,
                subdomain=subdomain,
                fulfillment=db.execute(
                    select(Fulfillment).where(Fulfillment.is_default.is_(True))
                ).scalars().first(),
                allowed=True,
                reason="reserved_subdomain",
            )
        fc = db.execute(
            select(Fulfillment).where(func.lower(Fulfillment.subdomain) == subdomain)
        ).scalars().first()
        if not fc:
            allowed = mode != "strict"
            return TenantResolution(
                kind="unknown",
                host=host,
                subdomain=subdomain,
                allowed=allowed,
                reason="unknown_subdomain",
            )
        ok, reason = _active_enough(fc)
        return TenantResolution(
            kind="subdomain",
            host=host,
            subdomain=subdomain,
            fulfillment=fc,
            allowed=ok if mode == "strict" else True,
            reason=None if ok else reason,
        )

    # Host is neither apex nor known tenant domain.
    if mode == "strict" and base:
        return TenantResolution(
            kind="unknown",
            host=host,
            allowed=False,
            reason="host_not_recognized",
        )
    return TenantResolution(kind="unknown", host=host, allowed=True, reason="no_tenant_match")


def caddy_ask_allows_host(db: Session, host: str) -> bool:
    """Used by Caddy on_demand TLS ask endpoint."""
    resolution = resolve_tenant_host(db, host)
    if resolution.kind in {"platform", "default"}:
        return True
    if resolution.kind in {"subdomain", "custom_domain"} and resolution.fulfillment:
        # Issue certs for onboarding hosts too, so admins can finish setup over HTTPS.
        return resolution.fulfillment.status != "ARCHIVED"
    return False


def resolution_public_dict(resolution: TenantResolution) -> dict[str, Any]:
    fc = resolution.fulfillment
    return {
        "kind": resolution.kind,
        "host": resolution.host,
        "subdomain": resolution.subdomain,
        "allowed": resolution.allowed,
        "reason": resolution.reason,
        "fulfillment": fulfillment_to_read(fc) if fc else None,
        "base_domain": platform_base_domain() or None,
        "mode": tenant_host_mode(),
    }


def assert_subdomain_available(db: Session, subdomain: str, *, exclude_id=None) -> None:
    from fastapi import HTTPException

    sub = normalize_host(subdomain)
    if not sub or "." in sub:
        raise HTTPException(status_code=400, detail="Некорректный subdomain")
    if is_reserved_subdomain(sub):
        raise HTTPException(status_code=400, detail=f"Subdomain '{sub}' зарезервирован платформой")
    stmt = select(Fulfillment).where(func.lower(Fulfillment.subdomain) == sub)
    if exclude_id is not None:
        stmt = stmt.where(Fulfillment.id != exclude_id)
    if db.execute(stmt).scalars().first():
        raise HTTPException(status_code=409, detail="Такой subdomain уже занят")
