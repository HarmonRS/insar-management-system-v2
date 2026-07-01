from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import DinsarProductionRunItemORM, DinsarTaskItemORM, ResultProductORM
from ..utils import normalize_satellite_family


DINSAR_CATALOG_NAME = "dinsar"
CURRENT_DINSAR_ENGINE_ORDER = ("sarscape", "landsar", "pyint")
LEGACY_DINSAR_ENGINE_CODES = {"isce2"}

DEFAULT_PROFILE_BY_ENGINE = {
    "sarscape": "custom6",
    "landsar": "lt1_dinsar",
    "pyint": "lt1_gamma_dinsar",
}

S1_PROFILE_BY_ENGINE = {
    "pyint": "s1_gamma_dinsar",
}


def normalize_dinsar_engine_code(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text == "gamma":
        return "pyint"
    return text


def is_current_dinsar_engine(value: Any) -> bool:
    return normalize_dinsar_engine_code(value) in CURRENT_DINSAR_ENGINE_ORDER


def _flatten_text_tokens(value: Any, output: List[str]) -> None:
    if value is None:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _flatten_text_tokens(key, output)
            _flatten_text_tokens(item, output)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            _flatten_text_tokens(item, output)
        return
    text = str(value).strip().lower()
    if text:
        output.append(text)


def infer_dinsar_data_family(*values: Any) -> str:
    tokens: List[str] = []
    for value in values:
        _flatten_text_tokens(value, tokens)
    joined = " ".join(tokens)
    normalized = normalize_satellite_family(joined)
    if normalized == "s1" or "sentinel" in joined or "sentinel-1" in joined or joined.startswith("s1"):
        return "s1"
    if normalized == "lt1" or "lt1" in joined or "陆探" in joined:
        return "lt1"
    return normalized or "unknown"


def allowed_engines_for_data_family(data_family: str) -> set[str]:
    normalized = str(data_family or "").strip().lower()
    if normalized == "s1":
        return {"pyint"}
    return set(CURRENT_DINSAR_ENGINE_ORDER)


def default_profile_for_engine(engine_code: str, data_family: str = "unknown") -> str:
    engine = normalize_dinsar_engine_code(engine_code)
    family = str(data_family or "").strip().lower()
    if family == "s1" and engine in S1_PROFILE_BY_ENGINE:
        return S1_PROFILE_BY_ENGINE[engine]
    return DEFAULT_PROFILE_BY_ENGINE.get(engine, engine)


def _timestamp(value: Optional[datetime]) -> float:
    if value is None:
        return 0.0
    try:
        return value.timestamp()
    except Exception:
        return 0.0


def _product_status(product: ResultProductORM) -> str:
    status = str(product.status or "").strip().upper()
    health = str(product.health_status or "").strip().upper()
    if status in {"READY", "COMPLETED", "SUCCESS"} and health not in {"ERROR", "FAILED"}:
        return "ready"
    if status in {"FAILED", "ERROR"} or health in {"ERROR", "FAILED"}:
        return "failed"
    if status:
        return status.lower()
    return "ready"


def _status_timestamp(value: Optional[datetime]) -> float:
    return _timestamp(value)


def _latest_failed_attempt(attempts: Iterable[DinsarProductionRunItemORM]) -> Optional[DinsarProductionRunItemORM]:
    latest: Optional[DinsarProductionRunItemORM] = None
    for attempt in attempts:
        if str(attempt.status or "").strip().upper() != "FAILED":
            continue
        if latest is None or (
            _status_timestamp(attempt.ended_at or attempt.updated_at or attempt.created_at),
            attempt.id or 0,
        ) > (
            _status_timestamp(latest.ended_at or latest.updated_at or latest.created_at),
            latest.id or 0,
        ):
            latest = attempt
    return latest


def serialize_engine_result(
    *,
    engine_code: str,
    data_family: str,
    product: Optional[ResultProductORM],
    failed_attempt: Optional[DinsarProductionRunItemORM] = None,
    allowed: bool,
    legacy: bool = False,
) -> Dict[str, Any]:
    engine = normalize_dinsar_engine_code(engine_code)
    if legacy:
        status = "legacy"
        can_dispatch = False
        skip_reason = "legacy_engine"
    elif not allowed:
        status = "blocked"
        can_dispatch = False
        skip_reason = "unsupported_data_family"
    elif product is None and failed_attempt is not None:
        status = "failed"
        can_dispatch = True
        skip_reason = "production_failed"
    elif product is None:
        status = "missing"
        can_dispatch = True
        skip_reason = None
    else:
        status = _product_status(product)
        can_dispatch = status in {"missing", "failed"}
        skip_reason = "result_exists" if status == "ready" else None

    return {
        "engine_code": engine,
        "allowed": bool(allowed),
        "legacy": bool(legacy),
        "status": status,
        "profile_code": (
            product.profile_code
            if product is not None and product.profile_code
            else default_profile_for_engine(engine, data_family)
        ),
        "latest_product_id": product.id if product is not None else None,
        "product_id": product.product_id if product is not None else None,
        "run_key": product.run_key if product is not None else failed_attempt.latest_run_key if failed_attempt is not None else None,
        "published_at": product.published_at if product is not None else None,
        "health_status": product.health_status if product is not None else None,
        "primary_asset_path": product.primary_asset_path if product is not None else None,
        "preview_path": product.preview_path if product is not None else None,
        "production_run_id": failed_attempt.run_id if failed_attempt is not None else None,
        "production_item_id": failed_attempt.id if failed_attempt is not None else None,
        "production_status": failed_attempt.status if failed_attempt is not None else None,
        "production_error": failed_attempt.last_error if failed_attempt is not None else None,
        "latest_log_path": failed_attempt.latest_log_path if failed_attempt is not None else None,
        "latest_output_dir": failed_attempt.latest_output_dir if failed_attempt is not None else None,
        "attempt_ended_at": failed_attempt.ended_at if failed_attempt is not None else None,
        "can_dispatch": can_dispatch,
        "skip_reason": skip_reason,
    }


def build_engine_results(
    *,
    products: Iterable[ResultProductORM],
    failed_attempts: Iterable[DinsarProductionRunItemORM] = (),
    data_family: str = "unknown",
    include_legacy: bool = False,
) -> Dict[str, Dict[str, Any]]:
    latest_by_engine: Dict[str, ResultProductORM] = {}
    legacy_latest: Dict[str, ResultProductORM] = {}
    for product in products:
        engine = normalize_dinsar_engine_code(product.engine_code)
        target = legacy_latest if engine in LEGACY_DINSAR_ENGINE_CODES else latest_by_engine
        current = target.get(engine)
        if current is None or (_timestamp(product.published_at), product.id or 0) > (
            _timestamp(current.published_at),
            current.id or 0,
        ):
            target[engine] = product

    failed_by_engine: Dict[str, DinsarProductionRunItemORM] = {}
    attempts_by_engine: Dict[str, List[DinsarProductionRunItemORM]] = {}
    for attempt in failed_attempts:
        engine = normalize_dinsar_engine_code(getattr(getattr(attempt, "run", None), "engine_code", ""))
        if engine:
            attempts_by_engine.setdefault(engine, []).append(attempt)
    for engine, attempts in attempts_by_engine.items():
        latest = _latest_failed_attempt(attempts)
        if latest is not None:
            failed_by_engine[engine] = latest

    allowed = allowed_engines_for_data_family(data_family)
    matrix: Dict[str, Dict[str, Any]] = {}
    for engine in CURRENT_DINSAR_ENGINE_ORDER:
        matrix[engine] = serialize_engine_result(
            engine_code=engine,
            data_family=data_family,
            product=latest_by_engine.get(engine),
            failed_attempt=failed_by_engine.get(engine) if latest_by_engine.get(engine) is None else None,
            allowed=engine in allowed,
        )

    if include_legacy:
        for engine, product in sorted(legacy_latest.items()):
            matrix[engine] = serialize_engine_result(
                engine_code=engine,
                data_family=data_family,
                product=product,
                failed_attempt=None,
                allowed=False,
                legacy=True,
            )
    return matrix


async def build_engine_results_for_task_items(
    db: AsyncSession,
    items: List[DinsarTaskItemORM],
) -> Dict[int, Dict[str, Dict[str, Any]]]:
    if not items:
        return {}

    pair_keys = sorted({str(item.pair_key or "").strip() for item in items if str(item.pair_key or "").strip()})
    aliases = sorted(
        {
            str(item.task_alias or item.task_name or "").strip()
            for item in items
            if str(item.task_alias or item.task_name or "").strip()
        }
    )
    conditions = []
    if pair_keys:
        conditions.append(ResultProductORM.pair_key.in_(pair_keys))
    if aliases:
        conditions.append(ResultProductORM.task_alias.in_(aliases))
        conditions.append(ResultProductORM.task_name.in_(aliases))
    if not conditions:
        return {
            int(item.id): build_engine_results(
                products=[],
                data_family=infer_dinsar_data_family(item.master_satellite, item.slave_satellite),
            )
            for item in items
            if item.id is not None
        }

    result = await db.execute(
        select(ResultProductORM)
        .where(ResultProductORM.catalog_name == DINSAR_CATALOG_NAME)
        .where(or_(*conditions))
        .order_by(ResultProductORM.published_at.desc().nullslast(), ResultProductORM.id.desc())
    )
    products = result.scalars().all()

    attempt_conditions = []
    if pair_keys:
        attempt_conditions.append(DinsarProductionRunItemORM.pair_key.in_(pair_keys))
    if aliases:
        attempt_conditions.append(DinsarProductionRunItemORM.task_alias.in_(aliases))
        attempt_conditions.append(DinsarProductionRunItemORM.task_name.in_(aliases))
    failed_attempts: List[DinsarProductionRunItemORM] = []
    if attempt_conditions:
        attempt_result = await db.execute(
            select(DinsarProductionRunItemORM)
            .options(selectinload(DinsarProductionRunItemORM.run))
            .where(DinsarProductionRunItemORM.status == "FAILED")
            .where(or_(*attempt_conditions))
            .order_by(
                DinsarProductionRunItemORM.ended_at.desc().nullslast(),
                DinsarProductionRunItemORM.updated_at.desc().nullslast(),
                DinsarProductionRunItemORM.id.desc(),
            )
        )
        failed_attempts = attempt_result.scalars().all()

    by_pair_key: Dict[str, List[ResultProductORM]] = {}
    by_alias: Dict[str, List[ResultProductORM]] = {}
    for product in products:
        pair_key = str(product.pair_key or "").strip()
        if pair_key:
            by_pair_key.setdefault(pair_key, []).append(product)
        for alias in {str(product.task_alias or "").strip(), str(product.task_name or "").strip()}:
            if alias:
                by_alias.setdefault(alias, []).append(product)

    failed_by_pair_key: Dict[str, List[DinsarProductionRunItemORM]] = {}
    failed_by_alias: Dict[str, List[DinsarProductionRunItemORM]] = {}
    for attempt in failed_attempts:
        pair_key = str(attempt.pair_key or "").strip()
        if pair_key:
            failed_by_pair_key.setdefault(pair_key, []).append(attempt)
        for alias in {str(attempt.task_alias or "").strip(), str(attempt.task_name or "").strip()}:
            if alias:
                failed_by_alias.setdefault(alias, []).append(attempt)

    output: Dict[int, Dict[str, Dict[str, Any]]] = {}
    for item in items:
        item_products: List[ResultProductORM] = []
        item_failed_attempts: List[DinsarProductionRunItemORM] = []
        pair_key = str(item.pair_key or "").strip()
        alias = str(item.task_alias or item.task_name or "").strip()
        if pair_key:
            item_products.extend(by_pair_key.get(pair_key, []))
            item_failed_attempts.extend(failed_by_pair_key.get(pair_key, []))
        if not item_products and alias:
            item_products.extend(by_alias.get(alias, []))
        if not item_failed_attempts and alias:
            item_failed_attempts.extend(failed_by_alias.get(alias, []))
        data_family = infer_dinsar_data_family(item.master_satellite, item.slave_satellite)
        output[int(item.id)] = build_engine_results(
            products=item_products,
            failed_attempts=item_failed_attempts,
            data_family=data_family,
        )
    return output
