# FC_R145F13_STORAGE_ACTIVE_BOXES_ONLY
# FC_R145F12_UNIFIED_STORAGE_TSD_EXISTING_BOX_SAFE
import secrets
import os
import logging
import resource
import threading
from pathlib import Path

import anyio

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.core.auth import read_session_token, session_principal
from app.core.config import settings
from app.core.database import get_db
from app.routers.auth import router as auth_router
from app.routers.audit import router as audit_router
from app.routers.imports import router as imports_router
from app.routers.inventory import router as inventory_router
from app.routers.layout import router as layout_router
from app.routers.marketplaces import repair_picking_chz_label_statuses, repair_manual_sent_regressions, router as marketplaces_router
from app.routers.masterdata import router as masterdata_router
from app.routers.printing import router as printing_router
from app.routers.receiving import router as receiving_router
from app.routers.tsd import router as tsd_router
from app.routers.tsd_mobile import router as tsd_mobile_router
from app.routers.tsd_work import router as tsd_work_router
from app.routers.warehouse import router as warehouse_router
from app.routers.workflow import router as workflow_router
from app.routers.warehouse_ui import router as warehouse_ui_router
from app.routers.box_intake import router as box_intake_router
from app.routers.stock_actions import router as stock_actions_router
from app.routers.receiving_native import router as receiving_native_router
from app.routers.pvz import router as pvz_router, portal_router as client_portal_router
from app.routers.r97_local import router as r97_local_router
from app.routers.r105_order_supply import router as r105_order_supply_router
from app.routers.r106_admin import router as r106_admin_router
from app.routers.r136_labels import router as r136_labels_router
from app.routers.r108_stock_transfer import router as r108_stock_transfer_router
from app.routers.transit_stock import router as transit_stock_router
from app.routers.r138_pvz_fast_search import router as r138_pvz_fast_search_router
from app.routers.r145f43_pvz_fast import router as r145f43_pvz_fast_router
from app.routers.r145f44_receiving_ultra import router as r145f44_receiving_ultra_router
from app.routers.r145f45_receiving_hot import router as r145f45_receiving_hot_router
from app.routers.r145f46_receiving_rescue import router as r145f46_receiving_rescue_router
from app.routers.r141_defect_storage import router as r141_defect_storage_router, install_schema as install_defect_storage_schema
from app.routers.r142_pvz_batch import router as r142_pvz_batch_router, install_schema as install_pvz_batch_schema
from app.routers.r143_virtual_receiving import router as r143_virtual_receiving_router
from app.routers.r143_client_receipts import router as r143_client_receipts_router
from app.routers.fbo import router as fbo_router
from app.routers.storage import router as storage_router, install_schema as install_storage_schema
from app.routers.r145f19_finance import router as finance_router, compat_router as r145f19_router, install_schema as install_finance_schema
from app.routers.r145f20_finance_ui import router as r145f20_finance_ui_router
from app.routers.r145f22_warehouse import router as r145f22_warehouse_router, install_schema as install_r145f22_schema
from app.routers.r145f23_fbs_export import router as r145f23_fbs_export_router
from app.routers.r145f24_history import router as r145f24_history_router
from app.routers.r145f20_defect_bulk import router as r145f20_defect_bulk_router, install_schema as install_r145f20_defect_schema
from app.routers.reliability import agent_router as reliability_agent_router, router as reliability_router
from app.routers.platform_fulfillments import router as platform_fulfillments_router
from app.routers.tenant import router as tenant_router
from app.services.client_location_guard import install_client_location_guard
from app.services.db_pool_tuning import db_pool_status, tune_db_pool
from app.services.runtime_reliability import background_lane_status, install_runtime_schema, normalize_station_code, station_token, verify_station_token
from app.services.stock_reservations import backfill_legacy_reservations, install_stock_reservation_schema
from app.services.stock_outbox_worker import start_stock_outbox_worker
from app.services.wb_sale_reservation_worker import start_wb_sale_reservation_worker
from app.services.transit_stock import install_schema as install_transit_schema
from app.services.fulfillment_platform import install_schema as install_fulfillment_platform_schema
from app.services.tenant_host import request_host, resolve_tenant_host
from app.services.tenant_scope import (
    attach_scope_to_request,
    is_tenant_isolated,
    load_fulfillment_scope,
    path_skips_scope,
    validate_request_against_scope,
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DOWNLOADS_DIR = BASE_DIR / "downloads"
PRINT_AGENT_ARCHIVE = DOWNLOADS_DIR / "FulfillCore_Print_Agent_1.1.8_STABLE_117_CORE_FAST.zip"
PRINT_AGENT_INSTRUCTION = DOWNLOADS_DIR / "Инструкция_FulfillCore_Print_Agent_1.1.7_полная.docx"

install_client_location_guard()

# FC_K9R60_WB_PAUSE_ZERO_DIRECT_GUARD: stop-sales zero is foreground verified; durable queue remains fallback.
# FC_K9R61_WORKFLOW_TRANSITION_CANONICAL_GUARD: picking and SENT transitions verify logical credential siblings.
# FC_K9R62_WAREHOUSE_SPLIT_RECEIPTS_TSD_ADMIN: main/FBS split, receiving batches and admin-closed warehouse TSD picks.
# FC_R127_FBS_REMOVE_STABILITY_CLIENT_PLACES: chunked FBS reset, stale-hold cleanup, async WB publication, client storage places.
# FC_R128_BOX_REPRINT_SELECTED: reprint QR for one or many existing boxes without changing stock or placement.
# FC_R129_BULK_BOX_MOVE_FAST: optimistic selected-box move + safe batch API; location deletes refresh locally without UI errors.
# FC_R131_HARD_PRODUCT_LABEL_58X40: product barcode labels use one fixed 58x40 mm physical page on every workstation.
# FC_R133_TSD_RECEIVER_SEPARATE_RESULTS: one receipt number may be shared, but drafts, posting and results remain separated by receiver name.
# FC_R135F1_PVZ_ACTIVE_CARD_ARTICLE_SEARCH_SAFE: clickable PVZ card + article lookup reuse existing local-products API; r97_local.py is not replaced.
# FC_R136_LOCAL_CARD_AUTOPRINT_LABEL_FIT: local Product Base cards open for manual SKUs; local barcode and box auto-print use exact 58x40 mm agent images.
# FC_R137_TRANSIT_WRITE_OFF_REPLACE_QR: partner dispatch writes off reservation-safe warehouse stock exactly once; transit boxes can be replaced and use QR labels.
# FC_R138_PVZ_INSTANT_ARTICLE_LOOKUP: PVZ article search uses direct indexed local SKU lookup; no WB API and no full product-catalog scan.
# FC_R139_FAST_STOCK_WRITEOFF_PVZ: exact-place manual write-off uses one validation pass; r138 PVZ fast article lookup bundled.
# FC_R140F1_TSD_BARCODE_GUARD: unknown TSD barcodes require checksum + repeat scan + explicit confirmation before a new local SKU can be created.
# FC_R141_DEFECT_STORAGE_SCAN_BOX_LABEL: separate defect custody, PVZ cell scanning/public box names, and full-size 58x40 Print Agent box labels.
# FC_R142_PVZ_BATCH_PRINT_TRANSIT_FIX: one PVZ receipt across many boxes with per-unit scan timestamps; location-style full-size box labels; BRAK box names; transit admin repair actions.
# FC_R145F2_PVZ_UNIFIED_STORAGE_DEFECT_CUSTOM_R142_SAFE: unified PVZ storage scan; direct cell without box; exact-name normal and defect box creation; r143/r144 bundled.
# FC_R145F3_PVZ_RECEIPT_NAME_WORKER_DELETE_R145F2_SAFE: manual receipt name, per-worker draft ownership, bottom accepted-items list and safe draft deletion.
# FC_R145F4_PVZ_DEFECT_WORKER_DELETE_R145F2_R145F3_SAFE: defect receipt employee/name, visible accepted defect items and soft-delete correction flow.
# FC_R145F5_PVZ_RU_LAYOUT_AUTOFOCUS_R145F2_R145F3_R145F4_SAFE: storage scan RU keyboard correction and product-barcode autofocus.
# FC_R145F17_PVZ_RECEIPT_EXCEL_SPLIT: per-receipt full PVZ Excel + separate defect export; r145f16 cumulative features preserved.
# FC_R145F16_TRANSIT_SCANNED_BOX_STORAGE_ONLY_R145F13_R145F14_R145F15_SAFE: r145f14 storage cells-as-boxes bundled; unknown transit scans become unique transit-local products without touching MAIN/FBS.
# FC_R145F18_TRANSIT_REPAIR_PVZ_RU_SCAN: empty transit boxes/batches can be repaired from desktop/TSD; wrong TSD box can be cleared; PVZ product barcode RU layout auto-fix.
# FC_R145F6_TRANSIT_SUPPLIER_HISTORY_TRANSFER_R145F2_R145F5_SAFE: transit history/search, supplier+lot ecosystem and explicit TRANSIT <-> MAIN/FBS movements.
# FC_R143_CELL_DIRECT_VIRTUAL_RECEIVING_CLIENT_REPORTS: cell receiving needs no box; virtual ПРИЕМКА shows only current loose stock; seller portal exports recent receipts.

# FC_R145F7_TSD_BOX_SUPPLIER_ASSEMBLY_SAFE
# FC_R145F8_FBO_SELLER_TSD_ASSEMBLY_R145F6_SAFE: r145f7 bundled from exact live r145f6/r112 TSD; FBO packing/labels; seller receipt grouping, defect box and stocks Excel fix.
# FC_R145F10F1_HOST_PRECHECK_NO_OPENPYXL
# FC_R145F10_XLSX_EXPORT_SAFETY_R145F6_R145F8_R145F9_SAFE: timezone/control-character safe Excel exports for defect/PVZ/transit/FBO/seller/warehouse.
# FC_R145F9_NAV_PRINT_SETTINGS_R145F6_R145F8_SAFE: resilient navigation for Receiving/Assembly/Defect/Print settings; print mode centralized in Print settings.
app = FastAPI(
    title=settings.app_name,
    version="0.9.4.15g5a8k9r145f54",
    description=(
        "FulfillCore MVP: товар, ячейки, короба, остатки, операции, Excel, WB/Ozon, "
        "сборочные листы, упаковка ШК → ЧЗ → этикетка, восстановление печати активного заказа WB, "
        "сборочные листы по всем заказам статуса без обязательной смены статуса, фильтр по брендам, "
        "безопасное удаление пустых адресов и коробов с сохранением истории, отмена ошибочной приёмки, "
        "контроль размещения остатков FBS, QR-этикетки адресов 58×40, пошаговая навигация по адресам, "
        "история ячейки, единая PDF-печать QR адресов 58×40 через браузер и Print Agent, "
        "печать выбранных адресов и диапазона ячеек, пакетная ручная приёмка, "
        "проверка товара до проведения, защита от задвоенного ШК, исправленные карточки и безопасное "
        "удаление ошибочного остатка, автоматическое поточное сканирование ручной приёмки без Enter, "
        "двухуровневая история приёмок, печать до 1000 ШК коробов поставки, "
        "печать ШК открытой поставки без закрытия и привязки к доставке, "
        "диагностика подключения и выбор склада Ozon, создание Ozon FBS-отгрузки "
        "из проверенных упакованных отправлений, массовая приёмка первого столбца "
        "как физического короба, создание пустых коробов при палетной приёмке до их заполнения, "
        "актуальный маршрут добавления заказов в поставку WB, восстановление после API 404, "
        "автовосстановление локальной SKU-привязки по точному ШК карточки WB выбранного клиента, "
        "диагностика складов WB, резервирование заказов между рабочими местами, распределённые блокировки WB, "
        "серверные профили рабочих мест, Print Agent 1.1.8 на стабильном ядре 1.1.7 с прямой TSPL-печатью, exactly-once, опросом очереди 150 мс и hot-reload токена, "
        "адаптивное сканирование, настоящий резерв сборочных листов, возврат отменённых заказов в ПРИЕМКА, "
        "пакетная QR-печать адресов без внешнего PDF-merger, автоматическое освобождение ЧЗ только при подтверждённой отмене WB до печати этикетки, "
        "двухэтапная структурная Excel-приёмка секция → палета → короб → товар с автоматическим созданием структуры и проверкой перед остатками, "
        "динамический сборочный лист без неиспользуемых колонок и гибкий конструктор секций, ячеек, палет и палеточных мест, "
        "единый серверный реестр поставок WB для нескольких компьютеров, создание пустых поставок, надёжное добавление заказов и подтверждаемое закрытие поставок, "
        "устойчивое сохранение выбранного клиента и физического склада, быстрый неблокирующий запуск рабочего места и защита от зависания интерфейса при временно медленных локальных запросах; "
        "снижение дублирующего polling упаковки без замедления заказов и публикации FBS-остатков; ПВЗ-приёмка, отдельный учёт брака, клиентский кабинет остатков и подготовка PDF ШК/ЧЗ к печати; раздельные локальные «Карточки WB» и «База товаров», мгновенный поиск по локальному индексу, дополняющий импорт WB, ПВЗ внутри приёмки и ускоренная Excel-история склада с отдельной датой; общая выгрузка всей истории склада с отдельными «Дата/время», «Дата» и «Название приёмки», без фонового timeout при открытии истории; загрузка локальных SKU из карточек WB в «Базу товаров» без изменения остатков и фоновая Excel-выгрузка истории без браузерного timeout с кэшем готового файла; карточный интерфейс Базы товаров с локальным удалением ошибочных карточек, отсечением неподтверждённых технических SKU и отдельной колонкой «Дата» сразу после «Дата/время» для фильтра Excel; печать ШК всех уникальных поставок выбранных упакованных заказов и обязательное предложение распечатать ШК поставок после успешного перевода отгрузки WB в доставку; сборочные задания FBS перенесены в «Справочники» с отдельным полем номера поставки/задания, безопасным списанием по Excel и готовым маршрутом по ячейкам; ручное добавление SKU/ячейки из «Справочников» скрыто, а ошибочный склад можно безопасно удалить из активного списка без уничтожения истории; восстановлено явное выделение позиций в Основном складе для массового перевода в FBS без Excel, добавлен обратный перевод выбранных позиций FBS в Основной и безопасный перевод целого стеллажа/полки/ячейки вместе с дочерними адресами между MAIN и FBS без изменения физического остатка и без затрагивания резервов/активной сборки; браузерная приёмка ПВЗ показывает удобную карточку товара из локальной Базы товаров с фото только когда оно уже сохранено локально, без обращения к WB и без изменений TSD; ПВЗ-приёмки отдельно маркируются в общей истории, а повторная выгрузка всей истории склада использует валидируемый по изменениям склада готовый Excel-кэш и быстрое ZIP-сжатие; TSD-приёмка принимает товар только в заранее созданный или принятый активный короб; неизвестный короб не создаётся автоматически, а раздел «Короба» умеет массово создавать и печатать диапазон QR-кодов коробов. Товарная автопечать жёстко фиксирована на физическом размере 58×40 мм независимо от локальных настроек ширины/высоты рабочего места. В браузерной ПВЗ-приёмке карточка найденного товара открывается в полный локальный просмотр, а возврат без читаемого ШК можно найти по артикулу и выбрать точный размер/ШК из уже существующей локальной Базы товаров без изменения серверного роутера Базы товаров. Ручные карточки в Базе товаров открываются как полноценные локальные карточки; их ШК можно печатать через Print Agent без WB-кабинета или через браузер. Автопечать товарных ШК и названий коробов формируется как точная этикетка 58×40 мм при 203 dpi с увеличенным читаемым текстом и без растягивания на несколько этикеток. Транзитная отправка партнёру списывает только свободный физический остаток точно один раз, не расходует резервы, поддерживает замену короба без потери состава и печатает QR короба 58×40. Поиск возврата ПВЗ по артикулу выполняется через прямой индекс локальных SKU без построения полного каталога и без обращения к WB; выбор размера/ШК не ждёт загрузки полной карточки. TSD-приёмка больше не присваивает случайный неизвестный цифровой код товару: известные ШК работают мгновенно, а новый ШК проходит контрольную цифру GTIN, проверку на артикул/код места, повторное физическое сканирование того же кода и отдельное подтверждение перед созданием локального SKU."
    ),
)


@app.middleware("http")
async def ui_no_cache(request: Request, call_next):
    response = await call_next(request)
    if request.url.path in {"/", "/login", "/help/print-agent"} or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    # Dynamic API responses may contain cabinet data and must not be cached.
    response.headers["Cache-Control"] = "no-store"
    return response


@app.middleware("http")
async def single_profile_auth(request: Request, call_next):
    path = request.url.path
    public = path in {
        "/login",
        "/auth/login",
        "/health",
        "/health/ready",
        "/tsd/install",
        "/tsd/login",
        "/tsd/app-info",
        "/tsd/install-qr.png",
        "/downloads/tsd/android.apk",
        "/client",
        "/client/login",
        "/tenant/current",
        "/tenant/resolve",
        "/internal/caddy-ask",
    } or path.startswith("/static/") or path.startswith("/client/") or path.startswith("/health")
    if public:
        return await call_next(request)

    if path.startswith("/printing/agent/"):
        supplied = request.headers.get("x-print-agent-token", "")
        header_station_raw = request.headers.get("x-print-agent-station") or ""
        query_station_raw = request.query_params.get("station_code") or ""
        header_station = normalize_station_code(header_station_raw) if header_station_raw else None
        query_station = normalize_station_code(query_station_raw) if query_station_raw else None
        if header_station and query_station and header_station != query_station:
            return JSONResponse({"detail": "PACK-код токена не совпадает с запрошенным рабочим местом"}, status_code=403)
        station = header_station or query_station or ""
        # Backward compatibility: installed 1.1.x agents keep working with the legacy
        # master token. New 1.2.x agents use a station-bound HMAC token so PACK-01
        # cannot poll PACK-02 even after a configuration mistake.
        legacy_ok = bool(settings.print_agent_token and secrets.compare_digest(supplied, settings.print_agent_token))
        station_ok = bool(station and verify_station_token(settings.print_agent_token, station, supplied))
        if legacy_ok or station_ok:
            request.state.print_agent_station = station
            return await call_next(request)
        return JSONResponse({"detail": "Неверный токен Print Agent или код рабочего места"}, status_code=401)

    session = read_session_token(request.cookies.get("fc_session"), settings.app_secret_key)
    if session:
        principal = session_principal(session)
        username = str(session.get("u") or "")
        if principal == "platform" and username == settings.admin_username:
            # Unchanged apex admin access; also allowed on tenant hosts for support.
            request.state.username = username
            request.state.auth_principal = "platform"
            request.state.auth_role = str(session.get("role") or "PLATFORM_ADMIN")
            request.state.auth_fulfillment_id = None
            request.state.auth_user_id = None
            return await call_next(request)

        if principal == "fc":
            resolution = getattr(request.state, "tenant_resolution", None)
            host_fid = str(getattr(request.state, "fulfillment_id", "") or "")
            session_fid = str(session.get("fid") or "")
            # FC sessions are valid only on their own fulfillment host.
            if not resolution or resolution.kind not in {"subdomain", "custom_domain"}:
                return JSONResponse(
                    {"detail": "Сессия сотрудника фулфилмента не действует на основном домене", "code": "FC_SESSION_APEX"},
                    status_code=403,
                )
            if not session_fid or session_fid != host_fid:
                return JSONResponse(
                    {"detail": "Сессия принадлежит другому фулфилменту", "code": "FC_SESSION_MISMATCH"},
                    status_code=403,
                )
            if path.startswith("/platform/"):
                return JSONResponse(
                    {"detail": "Platform API доступен только администратору платформы", "code": "PLATFORM_ONLY"},
                    status_code=403,
                )
            request.state.username = username
            request.state.auth_principal = "fc"
            request.state.auth_role = str(session.get("role") or "")
            request.state.auth_fulfillment_id = session_fid
            request.state.auth_user_id = str(session.get("uid") or "") or None
            return await call_next(request)

    wants_html = request.method == "GET" and "text/html" in request.headers.get("accept", "")
    if wants_html:
        return RedirectResponse(url="/tsd/login" if path.startswith("/tsd") else "/login", status_code=303)
    return JSONResponse({"detail": "Требуется вход"}, status_code=401)


@app.middleware("http")
async def enforce_fulfillment_data_scope(request: Request, call_next):
    """On tenant hosts, allow only warehouses/clients linked to that fulfillment.

    Must run after resolve_fulfillment_tenant (registered before it so Starlette
    LIFO executes resolve first). Apex / DEFAULT hosts are not isolated.
    """
    attach_scope_to_request(request, None)
    path = request.url.path
    if path_skips_scope(path) or path.startswith("/printing/agent/"):
        return await call_next(request)
    if not is_tenant_isolated(request):
        return await call_next(request)

    fulfillment_id = getattr(request.state, "fulfillment_id", None)
    if not fulfillment_id:
        return await call_next(request)

    body = b""
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        body = await request.body()

        async def _replay():
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request(request.scope, _replay)

    generator = get_db()
    db = next(generator)
    try:
        scope = load_fulfillment_scope(db, fulfillment_id)
        attach_scope_to_request(request, scope)
        try:
            validate_request_against_scope(request, scope, body, db)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    except Exception as exc:
        logging.getLogger(__name__).warning("fulfillment scope enforce failed: %s", exc)
    finally:
        try:
            generator.close()
        except Exception:
            pass
    return await call_next(request)


@app.middleware("http")
async def resolve_fulfillment_tenant(request: Request, call_next):
    """Attach fulfillment context from Host / X-Forwarded-Host.

    Registered after auth middleware so it executes first (Starlette LIFO) and can
    reject unknown/suspended tenant hosts before login redirects.
    """
    path = request.url.path
    skip_db = path.startswith("/health") or path.startswith("/static/")
    host = request_host(request.headers.get("host"), request.headers.get("x-forwarded-host"))
    request.state.tenant_host = host
    request.state.fulfillment_id = None
    request.state.fulfillment_code = None
    request.state.tenant_resolution = None

    if skip_db:
        return await call_next(request)

    generator = get_db()
    db = next(generator)
    try:
        resolution = resolve_tenant_host(db, host)
        request.state.tenant_resolution = resolution
        request.state.fulfillment_id = resolution.fulfillment_id
        request.state.fulfillment_code = resolution.fulfillment_code
        if not resolution.allowed and path != "/internal/caddy-ask":
            status = 403 if resolution.fulfillment is not None else 404
            return JSONResponse(
                {
                    "detail": resolution.reason or "Хост фулфилмента недоступен",
                    "host": resolution.host,
                    "subdomain": resolution.subdomain,
                    "kind": resolution.kind,
                },
                status_code=status,
            )
    except Exception as exc:
        # Tenant routing must not take down the warehouse app on DB blips.
        logging.getLogger(__name__).warning("tenant host resolve failed for %s: %s", host, exc)
    finally:
        try:
            generator.close()
        except Exception:
            pass
    return await call_next(request)


app.include_router(auth_router)
app.include_router(audit_router)
from app.routers.r145f25_receiving import router as r145f25_router
from sqlalchemy.exc import DBAPIError
from fastapi.responses import JSONResponse

@app.exception_handler(DBAPIError)
async def fc25_scope_error(request,exc):
    if 'FC_SCOPE:' in str(getattr(exc,'orig','')):
        return JSONResponse(status_code=409,content={'detail':'\u041c\u0435\u0441\u0442\u043e \u0437\u0430\u043d\u044f\u0442\u043e \u0434\u0440\u0443\u0433\u0438\u043c \u043a\u043b\u0438\u0435\u043d\u0442\u043e\u043c \u0438\u043b\u0438 \u043d\u0435 \u0441\u043e\u0432\u043f\u0430\u0434\u0430\u0435\u0442 \u0441\u043a\u043b\u0430\u0434/\u043a\u043b\u0438\u0435\u043d\u0442 \u0442\u043e\u0432\u0430\u0440\u0430. \u0418\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u044f \u043e\u0442\u043c\u0435\u043d\u0435\u043d\u044b. \u041e\u0431\u043d\u043e\u0432\u0438\u0442\u0435 \u0434\u0430\u043d\u043d\u044b\u0435 \u0438 \u0432\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0434\u0440\u0443\u0433\u043e\u0435 \u043c\u0435\u0441\u0442\u043e.','code':'CLIENT_WAREHOUSE_SCOPE'})
    if getattr(getattr(exc,'orig',None),'sqlstate',None) in ('55P03','40P01','40001'):
        return JSONResponse(status_code=409,content={'detail':'\u041c\u0435\u0441\u0442\u043e \u0441\u0435\u0439\u0447\u0430\u0441 \u0438\u0437\u043c\u0435\u043d\u044f\u0435\u0442\u0441\u044f. \u041e\u0431\u043d\u043e\u0432\u0438\u0442\u0435 \u043f\u0440\u0438\u0451\u043c\u043a\u0443 \u0438 \u043f\u043e\u0432\u0442\u043e\u0440\u0438\u0442\u0435 \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u0435.','code':'SCOPE_BUSY'})
    import logging
    logging.getLogger(__name__).error('Database request failed',exc_info=exc)
    return JSONResponse(status_code=500,content={'detail':'\u041e\u0448\u0438\u0431\u043a\u0430 \u0431\u0430\u0437\u044b \u0434\u0430\u043d\u043d\u044b\u0445. \u041f\u0440\u043e\u0432\u0435\u0440\u044c\u0442\u0435 \u0436\u0443\u0440\u043d\u0430\u043b \u0441\u0435\u0440\u0432\u0435\u0440\u0430.'})

app.include_router(r145f25_router)
from app.routers.r145f26_cards import router as r145f26_cards_router
app.include_router(r145f26_cards_router)
from app.routers.r145f27_receiving import router as r145f27_receiving_router
app.include_router(r145f27_receiving_router)
from app.routers.r145f29_tsd_receiving import router as r145f29_tsd_receiving_router
app.include_router(r145f29_tsd_receiving_router)
from app.routers.r145f30_receiving import router as r145f30_receiving_router
app.include_router(r145f30_receiving_router)
from app.routers.r145f31_tsd_box import router as r145f31_tsd_box_router
app.include_router(r145f31_tsd_box_router)
from app.routers.r145f32_receiving import router as r145f32_receiving_router
app.include_router(r145f32_receiving_router)
from app.routers.r145f33_tsd_finalize import router as r145f33_tsd_finalize_router
app.include_router(r145f33_tsd_finalize_router)
from app.routers.r145f35_receiving_finalize import router as r145f35_receiving_finalize_router
app.include_router(r145f35_receiving_finalize_router)
from app.routers.r145f36_receiving_recovery import router as r145f36_receiving_recovery_router
app.include_router(r145f36_receiving_recovery_router)
from app.routers.r145f37_receiving_clean import router as r145f37_receiving_clean_router
app.include_router(r145f37_receiving_clean_router)
from app.routers.r145f39_receiving import router as r145f39_receiving_router
app.include_router(r145f39_receiving_router)
from app.routers.r145f41_receiving import router as r145f41_receiving_router
app.include_router(r145f41_receiving_router)
from app.routers.r145f42_receiving import router as r145f42_receiving_router
from app.routers.r145f50_receiving import router as r145f50_receiving_router
from app.routers.r145f51_receiving import router as r145f51_receiving_router
from app.routers.r145f52_receiving import router as r145f52_receiving_router
app.include_router(r145f42_receiving_router)
app.include_router(masterdata_router)
app.include_router(inventory_router)
app.include_router(layout_router)
app.include_router(imports_router)
app.include_router(marketplaces_router)
app.include_router(printing_router)
app.include_router(receiving_router)
app.include_router(tsd_router)
app.include_router(tsd_mobile_router)
app.include_router(tsd_work_router)
app.include_router(warehouse_router)
app.include_router(workflow_router)
app.include_router(warehouse_ui_router)
app.include_router(box_intake_router)
app.include_router(stock_actions_router)
app.include_router(receiving_native_router)
app.include_router(pvz_router)
app.include_router(r97_local_router)
app.include_router(r105_order_supply_router)
app.include_router(r106_admin_router)
app.include_router(r136_labels_router)
app.include_router(r108_stock_transfer_router)
app.include_router(transit_stock_router)
app.include_router(r138_pvz_fast_search_router)
app.include_router(r145f43_pvz_fast_router)
app.include_router(r145f44_receiving_ultra_router)
app.include_router(r145f45_receiving_hot_router)
app.include_router(r145f46_receiving_rescue_router)
app.include_router(r141_defect_storage_router)
app.include_router(r142_pvz_batch_router)
app.include_router(r143_virtual_receiving_router)
app.include_router(r143_client_receipts_router)
app.include_router(fbo_router)
app.include_router(storage_router)
app.include_router(finance_router)
app.include_router(r145f19_router)
app.include_router(r145f20_finance_ui_router)
app.include_router(r145f20_defect_bulk_router)
app.include_router(r145f22_warehouse_router)
app.include_router(r145f23_fbs_export_router)
app.include_router(r145f24_history_router)
app.include_router(client_portal_router)
app.include_router(reliability_router)
app.include_router(reliability_agent_router)
app.include_router(tenant_router)
app.include_router(platform_fulfillments_router)
app.include_router(r145f50_receiving_router)
app.include_router(r145f51_receiving_router)
app.include_router(r145f52_receiving_router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def web_app():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/login", include_in_schema=False)
def login_page():
    return FileResponse(STATIC_DIR / "login.html")


@app.get("/client/login", include_in_schema=False)
def client_login_page():
    return FileResponse(STATIC_DIR / "client-portal-login.html")


@app.get("/client", include_in_schema=False)
def client_portal_page():
    return FileResponse(STATIC_DIR / "client-portal.html")


@app.get("/tsd", include_in_schema=False)
def tsd_app():
    return FileResponse(STATIC_DIR / "tsd.html")


@app.get("/help/print-agent", include_in_schema=False)
def print_agent_help_page():
    return FileResponse(STATIC_DIR / "print-agent.html")


@app.get("/help/print-agent/token", include_in_schema=False)
def print_agent_token(station_code: str | None = None):
    """Reveal the agent token only inside an authenticated FulfillCore session.

    The token is never embedded in the downloadable archive, Word instruction or
    static HTML and is explicitly marked non-cacheable by the response.
    """
    token = str(settings.print_agent_token or "").strip()
    if not token:
        raise HTTPException(status_code=409, detail="PRINT_AGENT_TOKEN не настроен на сервере")
    station = normalize_station_code(station_code) if station_code else None
    value = station_token(token, station) if station else token
    return JSONResponse(
        {"token": value, "station_code": station, "station_bound": bool(station)},
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache"},
    )


@app.get("/downloads/print-agent/archive", include_in_schema=False)
def download_print_agent_archive():
    return FileResponse(
        PRINT_AGENT_ARCHIVE,
        media_type="application/zip",
        filename=PRINT_AGENT_ARCHIVE.name,
    )


@app.get("/downloads/print-agent/instruction", include_in_schema=False)
def download_print_agent_instruction():
    return FileResponse(
        PRINT_AGENT_INSTRUCTION,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=PRINT_AGENT_INSTRUCTION.name,
    )


# FC_THREADPOOL_STABILITY_09415G4_20260903
@app.on_event("startup")
async def configure_sync_route_capacity():
    """Keep ordinary UI routes responsive under many concurrent sync requests.

    This changes only Starlette/AnyIO worker-thread capacity. Database concurrency is capped independently by SQLAlchemy QueuePool. The
    conservative default prevents request bursts from creating dozens of native thread
    stacks/allocator arenas while preserving parallel warehouse work.
    """
    try:
        requested = int(str(os.getenv("FC_THREADPOOL_TOKENS", "24")).strip())
    except Exception:
        requested = 24
    requested = max(16, min(48, requested))
    limiter = anyio.to_thread.current_default_thread_limiter()
    before = int(limiter.total_tokens)
    limiter.total_tokens = requested
    print(f"FulfillCore sync thread capacity: {before} -> {requested}")


class _HotPollAccessFilter(logging.Filter):
    """Drop only successful ultra-hot polling access lines. Errors remain visible.

    Uvicorn formats access records from args=(client, method, path, http_version, status).
    Thousands of 200 responses from Print Agent/progress used measurable CPU and disk I/O.
    """
    _QUIET_PREFIXES = ("/printing/agent/next", "/marketplaces/wb/packing/progress")

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            args = record.args
            path = str(args[2]) if isinstance(args, tuple) and len(args) >= 5 else ""
            status = int(args[4]) if isinstance(args, tuple) and len(args) >= 5 else 0
            if status < 400 and any(path.startswith(prefix) for prefix in self._QUIET_PREFIXES):
                return False
        except Exception:
            pass
        return True


@app.on_event("startup")
def configure_hot_poll_logging():
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(x, _HotPollAccessFilter) for x in logger.filters):
        logger.addFilter(_HotPollAccessFilter())


def _proc_status_value(name: str) -> int | None:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith(name + ":"):
                return int(line.split()[1])
    except Exception:
        return None
    return None


@app.get("/health/runtime")
async def health_runtime():
    """Cheap process-level diagnostics for leak/regression checks."""
    rss_kb = _proc_status_value("VmRSS")
    peak_kb = _proc_status_value("VmHWM")
    return {
        "status": "ok",
        "version": app.version,
        "rss_mb": round(rss_kb / 1024, 1) if rss_kb is not None else None,
        "peak_rss_mb": round(peak_kb / 1024, 1) if peak_kb is not None else None,
        "threads": threading.active_count(),
        "ru_maxrss_kb": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
    }


# FC_K9R59_BACKGROUND_HEALTH: no DB round-trip; exposes only in-process background pressure.
@app.get("/health/background")
async def health_background():
    return {"status": "ok", "version": app.version, **background_lane_status()}


@app.on_event("startup")
def reliability_startup():
    # Additive DDL is executed once at application start, outside warehouse requests.
    # Failure is logged but does not make an otherwise working FulfillCore unavailable;
    # the reliability service keeps a lazy fallback for recovery.
    generator = get_db()
    db = next(generator)
    try:
        pool_result = tune_db_pool(db)
        print(f"FulfillCore DB pool tuning: {pool_result}")
        install_runtime_schema(db)
        install_stock_reservation_schema(db)
        from app.services.stock_core_v2 import ensure_stock_core_v2_schema
        ensure_stock_core_v2_schema(db)
        result = backfill_legacy_reservations(db)
        if result.get("created"):
            print(f"FulfillCore stock reservation backfill: {result}")
        repaired_packed = repair_picking_chz_label_statuses(db)
        repaired_sent = repair_manual_sent_regressions(db)
        if repaired_packed or repaired_sent:
            db.commit()
            if repaired_packed:
                print(f"FulfillCore CHZ+label PACKED repair: {repaired_packed}")
            if repaired_sent:
                print(f"FulfillCore manual SENT regression repair: {repaired_sent}")
        if start_wb_sale_reservation_worker():
            print("FulfillCore WB sale reservation worker: started")
    except Exception as exc:
        print(f"FulfillCore reliability schema warning: {exc}")
    finally:
        try:
            generator.close()
        except Exception:
            pass


@app.on_event("startup")
def transit_warehouse_startup():
    # Transit custody is a separate additive schema and never changes MAIN/FBS stock.
    generator = get_db()
    db = next(generator)
    try:
        install_transit_schema(db)
        db.commit()
        print("FulfillCore transit warehouse schema: ready")
    except Exception as exc:
        db.rollback()
        print(f"FulfillCore transit warehouse schema warning: {exc}")
    finally:
        try:
            generator.close()
        except Exception:
            pass


@app.on_event("startup")
def pvz_receiving_batch_startup():
    generator = get_db()
    db = next(generator)
    try:
        install_pvz_batch_schema(db)
        db.commit()
        print("FulfillCore PVZ batch receiving schema: ready")
    except Exception as exc:
        db.rollback()
        print(f"FulfillCore PVZ batch receiving schema warning: {exc}")
    finally:
        try:
            generator.close()
        except Exception:
            pass


@app.on_event("startup")
def defect_storage_startup():
    # Separate custody only: no MAIN/FBS inventory rows are created or changed here.
    generator = get_db()
    db = next(generator)
    try:
        install_defect_storage_schema(db)
        db.commit()
        print("FulfillCore defect storage schema: ready")
    except Exception as exc:
        db.rollback()
        print(f"FulfillCore defect storage schema warning: {exc}")
    finally:
        try:
            generator.close()
        except Exception:
            pass


@app.on_event("startup")
def unified_storage_startup():
    generator = get_db()
    db = next(generator)
    try:
        install_storage_schema(db)
        db.commit()
        print("FulfillCore unified storage schema: ready")
    except Exception as exc:
        db.rollback()
        print(f"FulfillCore unified storage schema warning: {exc}")
    finally:
        try:
            generator.close()
        except Exception:
            pass


@app.on_event("startup")
def r145f19_finance_startup():
    generator = get_db()
    db = next(generator)
    try:
        install_finance_schema(db)
        db.commit()
        print("FulfillCore r145f19 finance/category schema: ready")
    except Exception as exc:
        db.rollback()
        print(f"FulfillCore r145f19 finance schema warning: {exc}")
    finally:
        try:
            generator.close()
        except Exception:
            pass


@app.on_event("startup")
def r145f20_defect_bulk_startup():
    generator = get_db()
    db = next(generator)
    try:
        install_r145f20_defect_schema(db)
        print("FulfillCore r145f20 defect bulk schema: ready")
    except Exception:
        db.rollback()
        raise
    finally:
        generator.close()


@app.on_event("startup")
def r145f22_receipt_cancel_startup():
    generator = get_db()
    db = next(generator)
    try:
        install_r145f22_schema(db)
    except Exception:
        db.rollback()
        raise
    finally:
        generator.close()


@app.on_event("startup")
def physical_accounting_startup():
    # Legacy CHZ/backfill failures must not disable stock completion.
    from app.services.stock_sync import install_hooks
    from app.services.stock_recovery import start_worker
    install_hooks()
    start_worker()
    if start_stock_outbox_worker():
        print("FulfillCore stock outbox worker: started")


@app.get("/health/stock-accounting")
def stock_accounting_health():
    from app.services.stock_recovery import worker_status
    return {"version": app.version, **worker_status()}


@app.get("/health/db-pool")
def health_db_pool():
    generator = get_db()
    db = next(generator)
    try:
        return {"version": app.version, **db_pool_status(db)}
    finally:
        generator.close()


@app.get("/health/ready")
def health_ready():
    """Readiness check: API process + a real database round-trip."""
    generator = get_db()
    db = next(generator)
    try:
        db.execute(text("SELECT 1"))
        from app.services.stock_recovery import worker_status
        return {"status": "ready", "version": app.version, "database": "ok",
                "stock_accounting_running": worker_status()["running"]}
    finally:
        generator.close()


@app.get("/health")
async def health():
    # Cheap event-loop liveness endpoint. It deliberately avoids the shared AnyIO
    # worker-thread pool, so a burst of sync warehouse/WB requests cannot hide a live API.
    return {"status": "ok", "version": app.version}

# FC_K9R55_DB_HOTPATH_INDEXES_SAFE: additive PostgreSQL hot-path indexes are installed concurrently by release installer; business data/logic unchanged.
# FC_K9R56_CONTAINER_MOVE_RESERVATION_SAFE: whole-box move follows NEW address reservations, warns for pallet-only active picks, and blocks exact-box active picking reservations.

# FC_K9R57_CONTAINER_MOVE_TIMEOUT_GUARD: indexed reservation preview, transaction-scoped move lock, bounded row-lock wait, and nonblocking UI refresh.
# FC_K9R58_SENT_STATUS_STICKY_GUARD: PACKED->SENT is logical-credential-wide and monotonic except authoritative WB terminal truth.

# FC_K9R59_BACKGROUND_DB_PRESSURE_GUARD: broad background DB work is serialized/yielding; operator hot paths stay independent.


@app.get('/warehouse-workflow', include_in_schema=False)
def warehouse_workflow_page():
    return FileResponse(STATIC_DIR / 'warehouse-workflow.html', headers={'Cache-Control': 'no-store'})

@app.get('/receiving-terminal', include_in_schema=False)
def receiving_terminal_page():
    return FileResponse(STATIC_DIR / 'receiving-terminal.html', headers={'Cache-Control': 'no-store'})


@app.on_event("startup")
def r145f26_card_schema_startup():
    from app.services.r145f26_cards import install_schema
    generator = get_db()
    db = next(generator)
    try:
        install_schema(db)
    except Exception:
        db.rollback()
        raise
    finally:
        generator.close()


@app.on_event("startup")
def r145f27_receiving_startup():
    from app.services.r145f27_receiving import install_schema
    from app.services.r145f27_labels import install_label_patch
    generator=get_db();db=next(generator)
    try:
        install_schema(db)
        install_label_patch()
    except Exception:
        db.rollback();raise
    finally:
        generator.close()


@app.on_event("startup")
def fulfillment_platform_startup():
    # Additive multi-fulfillment tables. Failure must not take down warehouse operations.
    generator = get_db()
    db = next(generator)
    try:
        result = install_fulfillment_platform_schema(db)
        print(f"FulfillCore fulfillment platform schema: ready ({result})")
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        print(f"FulfillCore fulfillment platform schema warning: {exc}")
    finally:
        try:
            generator.close()
        except Exception:
            pass


# FC_R145F44_STRICT_BOX_ULTRA_SYNC

# FC_R145F45_INSTANT_DRAFT_HOT_SYNC

# FC_R145F46_RECEIVING_RESCUE_ZERO_WAIT

# FC_R145F47_STABLE_BOX_LIST_ACTIVE_UI

# FC_R145F48_NESTED_BOX_CONTENT_STABLE

# FC_R145F50_RECEIVING_STABLE_WORKING

# FC_R145F51_BROWSER_FIRST_RECEIVING

# FC_R145F52_BROWSER_MASTER_RECOVERY_CARD
