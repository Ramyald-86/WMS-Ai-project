import base64
import hashlib
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from collections import OrderedDict

import httpx
from fastapi import HTTPException


WB_BASE = "https://marketplace-api.wildberries.ru"
WB_CONTENT_BASE = "https://content-api.wildberries.ru"
OZON_BASE = "https://api-seller.ozon.ru"

_CLIENT_CACHE_LOCK = threading.Lock()
_CLIENT_CACHE_MAX = 16
_CLIENT_CACHE_TTL_SECONDS = 1800.0
_WB_CLIENT_CACHE: "OrderedDict[str, tuple[float, WBClient]]" = OrderedDict()
_OZON_CLIENT_CACHE: "OrderedDict[str, tuple[float, OzonClient]]" = OrderedDict()

# Wildberries limits FBS requests per seller account and recommends an
# interval of about 200 ms between requests. Keep a process-wide limiter so
# parallel UI actions do not create bursts.
_WB_RATE_LOCK = threading.Lock()
# WB has separate rate buckets. Assembly/supply/sticker calls are limited to about
# 200 ms between requests, while marking metadata mutations allow about 60 ms.
# Keeping those buckets separate removes artificial scan->print latency: submitting
# SGTIN no longer consumes the sticker/status slot and vice versa.
_WB_LAST_REQUEST: dict[tuple[str, str], float] = {}
_WB_FBS_MIN_INTERVAL_SECONDS = 0.205
_WB_META_WRITE_MIN_INTERVAL_SECONDS = 0.065
_WB_CONTENT_RATE_LOCK = threading.Lock()
_WB_CONTENT_LAST_REQUEST: dict[str, float] = {}
_WB_CONTENT_MIN_INTERVAL_SECONDS = 0.62


def _wb_wait_for_bucket(rate_key: str, bucket: str, minimum: float) -> None:
    key = (rate_key, bucket)
    with _WB_RATE_LOCK:
        now = time.monotonic()
        last = _WB_LAST_REQUEST.get(key, 0.0)
        wait = float(minimum) - (now - last)
        if wait > 0:
            time.sleep(wait)
        _WB_LAST_REQUEST[key] = time.monotonic()


def _wb_wait_for_slot(rate_key: str) -> None:
    """Legacy FBS assembly/sticker limiter kept as a one-argument function."""
    _wb_wait_for_bucket(rate_key, "fbs", _WB_FBS_MIN_INTERVAL_SECONDS)


def _wb_wait_for_meta_write_slot(rate_key: str) -> None:
    _wb_wait_for_bucket(rate_key, "meta_write", _WB_META_WRITE_MIN_INTERVAL_SECONDS)




def _wb_content_wait_for_slot(rate_key: str) -> None:
    # Content API currently has a tighter account limit than Marketplace FBS. Keep a
    # separate limiter so catalogue recovery can never flood the seller token.
    with _WB_CONTENT_RATE_LOCK:
        now = time.monotonic()
        last = _WB_CONTENT_LAST_REQUEST.get(rate_key, 0.0)
        wait = _WB_CONTENT_MIN_INTERVAL_SECONDS - (now - last)
        if wait > 0:
            time.sleep(wait)
        _WB_CONTENT_LAST_REQUEST[rate_key] = time.monotonic()

def _retry_after_seconds(response: httpx.Response, attempt: int) -> float:
    raw = (response.headers.get("Retry-After") or "").strip()
    try:
        if raw:
            return max(1.0, min(float(raw), 65.0))
    except ValueError:
        pass
    # Covers both short burst throttling and a temporarily exhausted minute bucket.
    return (2.0, 5.0, 15.0, 30.0)[min(attempt, 3)]


def _detail_from_response(response: httpx.Response) -> str:
    try:
        data = response.json()
        if isinstance(data, dict):
            return str(data.get("message") or data.get("detail") or data.get("error") or data)[:700]
        return str(data)[:700]
    except Exception:
        return (response.text or f"HTTP {response.status_code}")[:700]


def _raise_for_marketplace(response: httpx.Response, provider: str) -> None:
    if response.is_success:
        return
    message = _detail_from_response(response)
    if response.status_code in (401, 403):
        raise HTTPException(status_code=400, detail=f"{provider}: API-ключ не принят или у него не хватает прав. {message}")
    if response.status_code == 429:
        raise HTTPException(status_code=429, detail=f"{provider}: превышен лимит запросов. Повторите позже.")
    raise HTTPException(status_code=502, detail=f"{provider}: ошибка API {response.status_code}. {message}")


class WBClient:
    """Small resilient WB API client.

    One client instance keeps one HTTP connection pool instead of opening a fresh TCP/TLS
    connection for every marketplace call. Read-only requests may be replayed after a
    transport error or a short 5xx outage. Mutations are deliberately *not* replayed when
    the response is unknown: the caller must re-read marketplace state first.
    """

    def __init__(self, token: str):
        auth = token.strip()
        if not auth.lower().startswith("bearer "):
            auth = f"Bearer {auth}"
        self.headers = {"Authorization": auth, "Content-Type": "application/json"}
        self._rate_key = hashlib.sha256(auth.encode("utf-8")).hexdigest()
        self._client = httpx.Client(
            headers=self.headers,
            timeout=httpx.Timeout(connect=4.0, read=20.0, write=20.0, pool=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=30.0),
            follow_redirects=True,
        )

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

    def __del__(self) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float = 45.0,
        retry_safe: bool = False,
    ) -> httpx.Response:
        method = str(method or "GET").upper()
        read_safe = method in {"GET", "HEAD", "OPTIONS"} or bool(retry_safe)
        max_read_attempts = 3 if read_safe else 1
        read_attempt = 0
        throttle_attempt = 0
        last_response: httpx.Response | None = None

        while True:
            # Metadata writes have their own WB limit (60 ms); do not serialize them
            # behind the 200 ms assembly/sticker bucket. Read-back stays on the safer FBS
            # bucket because only mutations are documented with the faster limit.
            if method in {"PUT", "DELETE"} and "/meta/" in path:
                _wb_wait_for_meta_write_slot(self._rate_key)
            else:
                _wb_wait_for_slot(self._rate_key)
            try:
                request_timeout = httpx.Timeout(connect=4.0, read=min(max(float(timeout), 5.0), 30.0), write=20.0, pool=5.0)
                r = self._client.request(method, WB_BASE + path, json=json, params=params, timeout=request_timeout)
            except httpx.TransportError as exc:
                read_attempt += 1
                if read_safe and read_attempt < max_read_attempts:
                    time.sleep((0.25, 0.75, 1.5)[min(read_attempt - 1, 2)])
                    continue
                if read_safe:
                    raise HTTPException(
                        status_code=504,
                        detail=f"Wildberries временно не ответил: {type(exc).__name__}. Повторите чтение/синхронизацию.",
                    ) from exc
                raise HTTPException(
                    status_code=504,
                    detail=(
                        f"Wildberries не подтвердил операцию записи: {type(exc).__name__}. "
                        "Не повторяйте запись вслепую: сначала нужно перечитать состояние заказа/поставки/остатка в WB."
                    ),
                ) from exc

            last_response = r
            if r.status_code == 429:
                wait_seconds = _retry_after_seconds(r, throttle_attempt)
                throttle_attempt += 1
                # A warehouse workstation must stay responsive. Long Retry-After values are
                # returned to the UI instead of sleeping inside the request for 15-65 sec.
                if throttle_attempt <= 3 and wait_seconds <= 5.0:
                    time.sleep(wait_seconds)
                    continue
                raise HTTPException(
                    status_code=429,
                    detail=f"Wildberries ограничил запросы. Повторите примерно через {int(max(1, wait_seconds))} сек.",
                )

            if read_safe and r.status_code in {502, 503, 504}:
                read_attempt += 1
                if read_attempt < max_read_attempts:
                    time.sleep((0.25, 0.75, 1.5)[min(read_attempt - 1, 2)])
                    continue

            _raise_for_marketplace(r, "Wildberries")
            return r

    def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        return self._request("GET", path, params=params, timeout=30.0)

    def _post(self, path: str, json: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> httpx.Response:
        return self._request("POST", path, json=json or {}, params=params)

    def _post_read(self, path: str, json: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> httpx.Response:
        """POST whose API semantics are read-only, therefore safe to retry."""
        return self._request("POST", path, json=json or {}, params=params, retry_safe=True)

    def _patch(self, path: str, json: dict[str, Any] | None = None) -> httpx.Response:
        return self._request("PATCH", path, json=json or {})

    def _put(self, path: str, json: dict[str, Any] | None = None) -> httpx.Response:
        return self._request("PUT", path, json=json or {})

    def _delete(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        return self._request("DELETE", path, params=params)

    def ping(self) -> dict[str, Any]:
        return self._get("/ping").json()

    def new_orders(self) -> list[dict[str, Any]]:
        return self._get("/api/v3/orders/new").json().get("orders", [])

    def _orders_window(self, start: datetime, end: datetime, limit: int = 1000, max_pages: int = 250) -> list[dict[str, Any]]:
        """Fetch one bounded UTC window completely.

        The old client stopped after exactly 100 pages. On high-volume cabinets that meant
        the newest part of a 30-day period could be silently missed. Small date windows keep
        pagination complete and make recent orders available first.
        """
        result: list[dict[str, Any]] = []
        next_value = 0
        for _ in range(max_pages):
            data = self._get(
                "/api/v3/orders",
                params={
                    "limit": max(1, min(limit, 1000)),
                    "next": next_value,
                    "dateFrom": int(start.timestamp()),
                    "dateTo": int(end.timestamp()),
                },
            ).json()
            batch = data.get("orders", []) or []
            result.extend(batch)
            nxt = data.get("next")
            if not batch or not nxt or nxt == next_value:
                break
            next_value = int(nxt)
        return result

    def orders_recent_first(self, days: int = 30, limit: int = 1000, window_days: int = 1) -> list[dict[str, Any]]:
        """Fetch order history newest-to-oldest in small windows and deduplicate by order ID."""
        days = max(1, min(days, 30))
        window_days = max(1, min(window_days, 3))
        now = datetime.now(timezone.utc)
        oldest = now - timedelta(days=days)
        end = now
        seen: set[str] = set()
        result: list[dict[str, Any]] = []
        while end > oldest:
            start = max(oldest, end - timedelta(days=window_days))
            for item in self._orders_window(start, end, limit=limit):
                oid = item.get("id") or item.get("orderId") or item.get("order_id")
                key = str(oid) if oid is not None else str(item)
                if key in seen:
                    continue
                seen.add(key)
                result.append(item)
            end = start
        return result

    def orders(self, days: int = 30, limit: int = 1000) -> list[dict[str, Any]]:
        # Preserve public method name, but remove the historical 100-page truncation.
        return self.orders_recent_first(days=days, limit=limit, window_days=1)

    def find_order(self, order_id: int, days: int = 30) -> dict[str, Any] | None:
        """Find one assembly order without loading an entire 30-day cabinet first.

        New orders are checked immediately. Then history is scanned one UTC day at a time,
        newest first. This is intentionally diagnostic, so it returns the first exact ID match.
        """
        wanted = str(int(order_id))
        try:
            for item in self.new_orders():
                oid = item.get("id") or item.get("orderId") or item.get("order_id")
                if oid is not None and str(oid) == wanted:
                    return item
        except HTTPException:
            pass
        now = datetime.now(timezone.utc)
        oldest = now - timedelta(days=max(1, min(days, 30)))
        end = now
        while end > oldest:
            start = max(oldest, end - timedelta(days=1))
            for item in self._orders_window(start, end, limit=1000):
                oid = item.get("id") or item.get("orderId") or item.get("order_id")
                if oid is not None and str(oid) == wanted:
                    return item
            end = start
        return None

    def order_statuses(self, order_ids: list[int]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for i in range(0, len(order_ids), 1000):
            chunk = order_ids[i:i + 1000]
            if not chunk:
                continue
            data = self._post_read("/api/v3/orders/status", {"orders": chunk}).json()
            result.extend(data.get("orders", []))
        return result

    def order_meta(self, order_ids: list[int]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for i in range(0, len(order_ids), 100):
            chunk = order_ids[i:i + 100]
            if not chunk:
                continue
            data = self._post_read("/api/marketplace/v3/orders/meta", {"orders": chunk}).json()
            result.extend(data.get("orders", []))
        return result

    def content_cards(self, *, text_search: str | None = None, limit_total: int = 20000) -> list[dict[str, Any]]:
        """Read WB product cards from the official Content API with cursor pagination.

        This is a recovery path for stale/missing cached chrtId mappings. It is intentionally
        rate-limited separately from Marketplace FBS calls and retries only this read-only POST.
        """
        limit_total = max(1, min(int(limit_total or 1), 50000))
        result: list[dict[str, Any]] = []
        cursor_updated_at: str | None = None
        cursor_nm_id: int | None = None
        seen_cursor: set[tuple[str, int]] = set()
        while len(result) < limit_total:
            page_limit = min(100, limit_total - len(result))
            cursor: dict[str, Any] = {"limit": page_limit}
            if cursor_updated_at and cursor_nm_id is not None:
                cursor["updatedAt"] = cursor_updated_at
                cursor["nmID"] = cursor_nm_id
            filters: dict[str, Any] = {"withPhoto": -1}
            if str(text_search or "").strip():
                filters["textSearch"] = str(text_search).strip()
            body = {"settings": {"sort": {"ascending": True}, "cursor": cursor, "filter": filters}}

            response: httpx.Response | None = None
            for attempt in range(3):
                _wb_content_wait_for_slot(self._rate_key)
                try:
                    response = self._client.post(
                        WB_CONTENT_BASE + "/content/v2/get/cards/list",
                        json=body,
                        timeout=httpx.Timeout(connect=4.0, read=25.0, write=15.0, pool=5.0),
                    )
                except httpx.TransportError as exc:
                    if attempt < 2:
                        time.sleep((0.35, 0.9)[attempt])
                        continue
                    raise HTTPException(status_code=504, detail=f"Wildberries Content API временно не ответил: {type(exc).__name__}") from exc
                if response.status_code in {429, 502, 503, 504} and attempt < 2:
                    wait = _retry_after_seconds(response, attempt) if response.status_code == 429 else (0.35, 0.9)[attempt]
                    if wait <= 5.0:
                        time.sleep(wait)
                        continue
                break
            if response is None:
                raise HTTPException(status_code=504, detail="Wildberries Content API временно недоступен")
            _raise_for_marketplace(response, "Wildberries Content")
            data = response.json()
            cards = list((data or {}).get("cards") or [])
            result.extend(cards[: max(0, limit_total - len(result))])
            next_cursor = (data or {}).get("cursor") or {}
            next_updated = str(next_cursor.get("updatedAt") or "").strip()
            try:
                next_nm = int(next_cursor.get("nmID"))
            except (TypeError, ValueError):
                next_nm = None
            if len(cards) < page_limit or not next_updated or next_nm is None:
                break
            key = (next_updated, next_nm)
            if key in seen_cursor:
                break
            seen_cursor.add(key)
            cursor_updated_at, cursor_nm_id = next_updated, next_nm
        return result

    def set_sgtin(self, order_id: int, sgtins: list[str]) -> None:
        self._put(f"/api/v3/orders/{order_id}/meta/sgtin", {"sgtins": sgtins})

    def delete_meta(self, order_id: int, key: str) -> None:
        self._delete(f"/api/v3/orders/{order_id}/meta", params={"key": key})

    def supplies(self, limit: int = 1000) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        next_value = 0
        for _ in range(10):
            data = self._get("/api/v3/supplies", params={"limit": limit, "next": next_value}).json()
            items.extend(data.get("supplies", []))
            nxt = data.get("next")
            if not nxt or nxt == next_value or len(items) >= 5000:
                break
            next_value = nxt
        return items

    def supply_order_ids(self, supply_id: str) -> list[int]:
        """Return assembly-order IDs assigned to a WB FBS supply.

        Wildberries disabled the legacy full supply-orders endpoint in Dec 2025.
        The current endpoint returns {"orderIds": [int, ...]}. Be tolerant of
        older/sandbox response shapes so one unusual response cannot break the UI.
        """
        data = self._get(f"/api/marketplace/v3/supplies/{supply_id}/order-ids").json()
        raw_items = data.get("orderIds") or data.get("order_ids") or data.get("orders") or []
        result: list[int] = []
        for item in raw_items:
            if isinstance(item, dict):
                item = item.get("id") or item.get("orderId") or item.get("orderID") or item.get("order_id")
            try:
                value = int(item)
            except (TypeError, ValueError):
                continue
            if value not in result:
                result.append(value)
        return result

    def create_supply(self, name: str) -> dict[str, Any]:
        return self._post("/api/v3/supplies", {"name": name}).json()

    def add_orders_to_supply(self, supply_id: str, order_ids: list[int]) -> None:
        self._patch(f"/api/marketplace/v3/supplies/{supply_id}/orders", {"orders": order_ids})

    def deliver_supply(self, supply_id: str) -> None:
        self._patch(f"/api/v3/supplies/{supply_id}/deliver")

    def order_stickers(self, order_ids: list[int]) -> list[dict[str, Any]]:
        """Return WB sticker metadata + base64 PNG exactly as the API supplies it."""
        result: list[dict[str, Any]] = []
        for i in range(0, len(order_ids), 100):
            chunk = order_ids[i:i + 100]
            if not chunk:
                continue
            r = self._post_read(
                "/api/v3/orders/stickers",
                {"orders": chunk},
                params={"type": "png", "width": 58, "height": 40},
            )
            result.extend(r.json().get("stickers", []) or [])
        return result

    def order_stickers_png(self, order_ids: list[int]) -> list[bytes]:
        r = self._post_read(
            "/api/v3/orders/stickers",
            {"orders": order_ids},
            params={"type": "png", "width": 58, "height": 40},
        )
        return [base64.b64decode(item["file"]) for item in r.json().get("stickers", []) if item.get("file")]

    def supply_barcode_png(self, supply_id: str) -> bytes:
        data = self._get(f"/api/v3/supplies/{supply_id}/barcode", params={"type": "png"}).json()
        if not data.get("file"):
            raise HTTPException(status_code=502, detail="Wildberries не вернул QR-код поставки")
        return base64.b64decode(data["file"])

    def add_supply_boxes(self, supply_id: str, amount: int) -> list[str]:
        data = self._post(f"/api/v3/supplies/{supply_id}/trbx", {"amount": amount}).json()
        return data.get("trbxIds", [])

    def supply_boxes(self, supply_id: str) -> list[str]:
        data = self._get(f"/api/v3/supplies/{supply_id}/trbx").json()
        return [x.get("id") for x in data.get("trbxes", []) if x.get("id")]

    def supply_box_stickers_png(self, supply_id: str, trbx_ids: list[str]) -> list[bytes]:
        r = self._post_read(
            f"/api/v3/supplies/{supply_id}/trbx/stickers",
            {"trbxIds": trbx_ids},
            params={"type": "png"},
        )
        return [base64.b64decode(item["file"]) for item in r.json().get("stickers", []) if item.get("file")]


class OzonClient:
    def __init__(self, client_id: str, api_key: str):
        self.headers = {
            "Client-Id": client_id,
            "Api-Key": api_key,
            "Content-Type": "application/json",
        }
        self._client = httpx.Client(
            headers=self.headers,
            timeout=httpx.Timeout(connect=4.0, read=25.0, write=20.0, pool=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=30.0),
            follow_redirects=True,
        )

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

    def __del__(self) -> None:
        self.close()

    def _post(
        self,
        path: str,
        json: dict[str, Any] | None = None,
        accept: str | None = None,
        *,
        retry_safe: bool = False,
    ) -> httpx.Response:
        headers = {}
        if accept:
            headers["Accept"] = accept
        attempts = 3 if retry_safe else 1
        for attempt in range(attempts):
            try:
                r = self._client.post(OZON_BASE + path, headers=headers or None, json=json or {})
            except httpx.TransportError as exc:
                if retry_safe and attempt + 1 < attempts:
                    time.sleep((0.25, 0.75)[min(attempt, 1)])
                    continue
                if retry_safe:
                    raise HTTPException(
                        status_code=504,
                        detail=f"Ozon временно не ответил: {type(exc).__name__}. Повторите чтение/синхронизацию.",
                    ) from exc
                raise HTTPException(
                    status_code=504,
                    detail=(
                        f"Ozon не подтвердил операцию записи: {type(exc).__name__}. "
                        "Сначала перечитайте состояние отправления, затем решайте, нужно ли повторять действие."
                    ),
                ) from exc
            if retry_safe and r.status_code in {429, 502, 503, 504} and attempt + 1 < attempts:
                wait = _retry_after_seconds(r, attempt) if r.status_code == 429 else (0.25, 0.75)[min(attempt, 1)]
                if wait <= 5.0:
                    time.sleep(wait)
                    continue
            _raise_for_marketplace(r, "Ozon")
            return r
        raise HTTPException(status_code=504, detail="Ozon временно недоступен")

    def warehouses(self) -> list[dict[str, Any]]:
        return self._post("/v1/warehouse/list", {}, retry_safe=True).json().get("result", [])

    def postings(self, statuses: list[str] | None = None, days: int = 7, limit: int = 100) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        since = now - timedelta(days=max(1, min(days, 365)))
        body = {
            "sort_dir": "asc",
            "filter": {
                "since": since.isoformat().replace("+00:00", "Z"),
                "to": (now + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
                "status": statuses or ["awaiting_packaging", "awaiting_deliver"],
            },
            "limit": max(1, min(limit, 1000)),
            "cursor": "",
            "with": {
                "analytics_data": False,
                "barcodes": True,
                "financial_data": False,
                "legal_info": False,
                "translit": False,
            },
        }
        data = self._post("/v4/posting/fbs/list", body, retry_safe=True).json()
        if "postings" in data:
            return data.get("postings", [])
        return data.get("result", {}).get("postings", [])

    def posting(self, posting_number: str) -> dict[str, Any]:
        data = self._post(
            "/v3/posting/fbs/get",
            {
                "posting_number": str(posting_number),
                "with": {
                    "analytics_data": False,
                    "barcodes": True,
                    "financial_data": False,
                    "legal_info": False,
                    "translit": False,
                },
            },
            retry_safe=True,
        ).json()
        row = data.get("result") if isinstance(data, dict) else None
        return row if isinstance(row, dict) else (data if isinstance(data, dict) else {})

    def package_labels_pdf(self, posting_numbers: list[str]) -> bytes:
        r = self._post("/v2/posting/fbs/package-label", {"posting_number": posting_numbers}, accept="application/pdf", retry_safe=True)
        content_type = (r.headers.get("content-type") or "").lower()
        if "pdf" in content_type and r.content.startswith(b"%PDF"):
            return r.content
        try:
            data = r.json()
            result = data.get("result") if isinstance(data.get("result"), dict) else {}
            content = data.get("content") or data.get("file_content") or result.get("content") or result.get("file_content")
            if content:
                if isinstance(content, str) and content.startswith("%PDF"):
                    return content.encode("latin-1", errors="ignore")
                raw = base64.b64decode(content)
                if raw.startswith(b"%PDF"):
                    return raw
        except Exception:
            pass
        if r.content.startswith(b"%PDF"):
            return r.content
        raise HTTPException(status_code=502, detail="Ozon не вернул PDF с этикетками")

    def ship(self, posting_number: str, packages: list[dict[str, Any]]) -> dict[str, Any]:
        return self._post(
            "/v4/posting/fbs/ship",
            {"posting_number": posting_number, "packages": packages, "with": {"additional_data": True}},
        ).json()


def _prune_client_cache(cache, now: float) -> None:
    stale = [key for key, (seen, _client) in cache.items() if now - float(seen) > _CLIENT_CACHE_TTL_SECONDS]
    for key in stale:
        _seen, client = cache.pop(key, (0.0, None))
        if client is not None:
            try: client.close()
            except Exception: pass
    while len(cache) > _CLIENT_CACHE_MAX:
        _key, (_seen, client) = cache.popitem(last=False)
        try: client.close()
        except Exception: pass


def shared_wb_client(token: str) -> WBClient:
    """Reuse one bounded HTTP/TLS pool per WB token instead of one pool per request."""
    normalized = str(token or '').strip()
    key = hashlib.sha256(normalized.encode('utf-8')).hexdigest()
    now = time.monotonic()
    with _CLIENT_CACHE_LOCK:
        _prune_client_cache(_WB_CLIENT_CACHE, now)
        row = _WB_CLIENT_CACHE.pop(key, None)
        if row is not None:
            client = row[1]
            _WB_CLIENT_CACHE[key] = (now, client)
            return client
        client = WBClient(normalized)
        _WB_CLIENT_CACHE[key] = (now, client)
        _prune_client_cache(_WB_CLIENT_CACHE, now)
        return client


def shared_ozon_client(client_id: str, api_key: str) -> OzonClient:
    """Reuse one bounded HTTP/TLS pool per Ozon credential pair."""
    cid = str(client_id or '').strip(); key_raw = cid + '\0' + str(api_key or '').strip()
    key = hashlib.sha256(key_raw.encode('utf-8')).hexdigest()
    now = time.monotonic()
    with _CLIENT_CACHE_LOCK:
        _prune_client_cache(_OZON_CLIENT_CACHE, now)
        row = _OZON_CLIENT_CACHE.pop(key, None)
        if row is not None:
            client = row[1]
            _OZON_CLIENT_CACHE[key] = (now, client)
            return client
        client = OzonClient(cid, api_key)
        _OZON_CLIENT_CACHE[key] = (now, client)
        _prune_client_cache(_OZON_CLIENT_CACHE, now)
        return client
