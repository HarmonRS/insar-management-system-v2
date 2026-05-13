from __future__ import annotations

import os
from typing import Any, Dict, List, Set, Tuple
from urllib.parse import urlparse

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateColumn

from .config import read_bool_env, settings


POSTGIS_TABLES = {
    "spatial_ref_sys",
}

POSTGIS_VIEWS = {
    "geometry_columns",
    "geography_columns",
    "raster_columns",
    "raster_overviews",
}

ALLOWED_EXTRA_TABLES = {
    "spatial_query_logs",
}

MIGRATION_FILES = [
    "001_st_intersection_agg.sql",
    "002_spatial_functions.sql",
    "003_pairing_enhancement.sql",
    "004_pairing_refactor.sql",
    "005_pairing_task_trace.sql",
    "006_result_pairing_trace.sql",
    "007_timeseries_stack_plan_trace.sql",
    "008_timeseries_stack_plan_edges.sql",
    "009_raw_source_pairing_fields.sql",
    "010_source_orbit_asset_inventory.sql",
]


def project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _database_url_to_sync_url(database_url: str) -> str:
    if "postgresql+asyncpg" in database_url:
        return database_url.replace("postgresql+asyncpg", "postgresql", 1)
    return database_url


def _split_sql_statements(sql_text: str) -> List[str]:
    statements: List[str] = []
    buf: List[str] = []
    in_single = False
    in_double = False
    dollar_tag = None
    i = 0
    length = len(sql_text)

    while i < length:
        ch = sql_text[i]
        if dollar_tag:
            if sql_text.startswith(dollar_tag, i):
                buf.append(dollar_tag)
                i += len(dollar_tag)
                dollar_tag = None
                continue
            buf.append(ch)
            i += 1
            continue

        if not in_single and not in_double:
            if ch == "'":
                in_single = True
                buf.append(ch)
                i += 1
                continue
            if ch == '"':
                in_double = True
                buf.append(ch)
                i += 1
                continue
            if ch == "$":
                end = sql_text.find("$", i + 1)
                if end != -1:
                    tag = sql_text[i:end + 1]
                    dollar_tag = tag
                    buf.append(tag)
                    i += len(tag)
                    continue
            if ch == ";":
                stmt = "".join(buf).strip()
                if stmt:
                    statements.append(stmt)
                buf = []
                i += 1
                continue
        else:
            if in_single:
                if ch == "'" and i + 1 < length and sql_text[i + 1] == "'":
                    buf.append(ch)
                    buf.append(sql_text[i + 1])
                    i += 2
                    continue
                if ch == "'":
                    in_single = False
                    buf.append(ch)
                    i += 1
                    continue
            if in_double and ch == '"':
                in_double = False
                buf.append(ch)
                i += 1
                continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def _is_effective_sql(stmt: str) -> bool:
    stripped = stmt.strip()
    if not stripped:
        return False
    for line in stripped.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("--"):
            continue
        if line.startswith("/*") and line.endswith("*/"):
            continue
        return True
    return False


def _apply_sql_file(conn, path: str) -> bool:
    if not os.path.exists(path):
        print(f"[WARN] Migration file not found: {path}")
        return False
    with open(path, "r", encoding="utf-8-sig") as stream:
        sql_text = stream.read()
    for stmt in _split_sql_statements(sql_text):
        if not _is_effective_sql(stmt):
            continue
        conn.exec_driver_sql(stmt)
    return True


def _load_base():
    from backend.app.database import Base
    import backend.app.models  # noqa: F401

    return Base


def get_required_table_names() -> List[str]:
    base = _load_base()
    return sorted(base.metadata.tables.keys())


def _build_schema_diagnostics(
    expected: Dict[str, Dict[str, Dict[str, Any]]],
    current: Dict[str, Dict[str, Dict[str, Any]]],
) -> Dict[str, Any]:
    filtered_current = {
        name: cols for name, cols in current.items() if name not in ALLOWED_EXTRA_TABLES
    }
    expected_tables = set(expected.keys())
    current_tables = set(filtered_current.keys())

    missing_tables = sorted(expected_tables - current_tables)
    extra_tables = sorted(current_tables - expected_tables)
    missing_columns: Dict[str, List[str]] = {}
    extra_columns: Dict[str, List[str]] = {}
    type_mismatches: List[Dict[str, str]] = []
    nullable_mismatches: List[Dict[str, Any]] = []
    reasons: List[str] = []

    if missing_tables:
        reasons.append(f"Missing tables: {missing_tables}")
    if extra_tables:
        reasons.append(f"Extra tables: {extra_tables}")

    bootstrap_required = not current_tables and bool(expected_tables) and set(missing_tables) == expected_tables

    for table in sorted(expected_tables & current_tables):
        expected_cols = expected[table]
        current_cols = filtered_current[table]
        expected_col_names = set(expected_cols.keys())
        current_col_names = set(current_cols.keys())

        table_missing_cols = sorted(expected_col_names - current_col_names)
        table_extra_cols = sorted(current_col_names - expected_col_names)
        if table_missing_cols:
            missing_columns[table] = table_missing_cols
            reasons.append(f"Table {table} missing columns: {table_missing_cols}")
        if table_extra_cols:
            extra_columns[table] = table_extra_cols
            reasons.append(f"Table {table} extra columns: {table_extra_cols}")

        for col_name in sorted(expected_col_names & current_col_names):
            exp = expected_cols[col_name]
            cur = current_cols[col_name]
            if exp["type"] != cur["type"]:
                mismatch = {
                    "table": table,
                    "column": col_name,
                    "expected": exp["type"],
                    "actual": cur["type"],
                }
                type_mismatches.append(mismatch)
                reasons.append(
                    f"Table {table} column {col_name} type mismatch: expected {exp['type']} got {cur['type']}"
                )
            if exp["nullable"] != cur["nullable"]:
                mismatch = {
                    "table": table,
                    "column": col_name,
                    "expected": exp["nullable"],
                    "actual": cur["nullable"],
                }
                nullable_mismatches.append(mismatch)
                reasons.append(
                    f"Table {table} column {col_name} nullable mismatch: expected {exp['nullable']} got {cur['nullable']}"
                )

    return {
        "mismatch": len(reasons) > 0,
        "bootstrap_required": bootstrap_required,
        "reasons": reasons,
        "reason_count": len(reasons),
        "required_tables": sorted(expected.keys()),
        "required_table_count": len(expected),
        "current_table_count": len(filtered_current),
        "missing_tables": missing_tables,
        "extra_tables": extra_tables,
        "missing_columns": missing_columns,
        "extra_columns": extra_columns,
        "type_mismatches": type_mismatches,
        "nullable_mismatches": nullable_mismatches,
    }


def _normalize_type(type_obj, dialect) -> str:
    if type_obj is None:
        return ""
    try:
        compiled = type_obj.compile(dialect=dialect)
    except Exception:
        compiled = str(type_obj)
    if compiled is None:
        return ""
    norm = str(compiled).lower().replace(" ", "")
    float_aliases = {"doubleprecision", "float8", "float4", "real", "float"}
    if norm in float_aliases:
        return "float"
    return norm


def _get_expected_schema(base, dialect) -> Dict[str, Dict[str, Dict[str, Any]]]:
    expected: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for table_name, table in base.metadata.tables.items():
        expected[table_name] = {}
        for col in table.columns:
            expected[table_name][col.name] = {
                "type": _normalize_type(col.type, dialect),
                "nullable": bool(col.nullable),
            }
    return expected


def _get_current_schema(inspector, dialect) -> Dict[str, Dict[str, Dict[str, Any]]]:
    current: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for table_name in inspector.get_table_names():
        if table_name in POSTGIS_TABLES:
            continue
        cols = inspector.get_columns(table_name)
        current[table_name] = {}
        for col in cols:
            current[table_name][col["name"]] = {
                "type": _normalize_type(col.get("type"), dialect),
                "nullable": bool(col.get("nullable", True)),
            }
    return current


def _schema_mismatch(
    expected: Dict[str, Dict[str, Dict[str, Any]]],
    current: Dict[str, Dict[str, Dict[str, Any]]],
) -> Tuple[bool, List[str]]:
    diagnostics = _build_schema_diagnostics(expected, current)
    return diagnostics["mismatch"], diagnostics["reasons"]


def inspect_database_structure(bind) -> Dict[str, Any]:
    base = _load_base()
    expected = _get_expected_schema(base, bind.dialect)
    current = _get_current_schema(inspect(bind), bind.dialect)
    return _build_schema_diagnostics(expected, current)


def _drop_all_objects(conn, inspector) -> None:
    for view in inspector.get_view_names():
        if view in POSTGIS_VIEWS:
            continue
        conn.exec_driver_sql(f'DROP VIEW IF EXISTS "{view}" CASCADE')
    for table in inspector.get_table_names():
        if table in POSTGIS_TABLES:
            continue
        conn.exec_driver_sql(f'DROP TABLE IF EXISTS "{table}" CASCADE')


def _add_missing_columns(conn, inspector, base, dialect) -> List[str]:
    added_columns: List[str] = []
    existing_tables = set(inspector.get_table_names())
    for table_name, table in base.metadata.tables.items():
        if table_name not in existing_tables:
            continue

        current_columns = {column["name"] for column in inspector.get_columns(table_name)}
        for column in table.columns:
            if column.name in current_columns:
                continue
            try:
                column_sql = str(CreateColumn(column).compile(dialect=dialect))
                conn.exec_driver_sql(f'ALTER TABLE "{table_name}" ADD COLUMN {column_sql}')
                added_columns.append(f"{table_name}.{column.name}")
            except Exception as exc:
                print(f"[WARN] Failed to add column {table_name}.{column.name}: {exc}")
    return added_columns


def _resolve_hazard_shapefile() -> str:
    hazard_dir = settings.HAZARD_POINTS_DIR
    hazard_filename = settings.HAZARD_POINTS_FILENAME or "Point.shp"
    if hazard_dir:
        return os.path.join(hazard_dir, hazard_filename)
    return os.path.join(project_root(), "backend", "Point", hazard_filename)


def bootstrap_admin_user(session) -> Dict[str, Any]:
    from backend.app.auth_utils import hash_password, normalize_username
    from backend.app.models import AuthUserORM

    status = {"created": False, "updated": False, "username": None, "message": ""}

    admin_online = session.query(AuthUserORM).filter(
        AuthUserORM.role == "admin",
        AuthUserORM.is_active == True,
    ).first()
    if admin_online:
        status["username"] = admin_online.username
        status["message"] = f"Admin account exists: {admin_online.username}"
        return status

    admin_username = normalize_username(settings.INIT_ADMIN_USERNAME or "admin") or "admin"
    admin_password = settings.INIT_ADMIN_PASSWORD or ""
    reset_existing_password = bool(settings.INIT_ADMIN_RESET_PASSWORD)

    existing_user = session.query(AuthUserORM).filter(
        AuthUserORM.username == admin_username
    ).one_or_none()

    password_required = existing_user is None or reset_existing_password or not existing_user.password_hash
    if password_required and not admin_password:
        raise RuntimeError(
            "INIT_ADMIN_PASSWORD is required to create/reset admin account. "
            "Please set INIT_ADMIN_PASSWORD before startup."
        )

    if existing_user:
        existing_user.role = "admin"
        existing_user.is_active = True
        if reset_existing_password or not existing_user.password_hash:
            existing_user.password_hash = hash_password(admin_password)
        existing_user.created_by = existing_user.created_by or "system:db_maintenance"
        session.commit()
        status["updated"] = True
        status["username"] = admin_username
        status["message"] = f"Promoted existing user to admin: {admin_username}"
        return status

    admin_user = AuthUserORM(
        username=admin_username,
        password_hash=hash_password(admin_password),
        role="admin",
        is_active=True,
        created_by="system:db_maintenance",
    )
    session.add(admin_user)
    session.commit()
    status["created"] = True
    status["username"] = admin_username
    status["message"] = f"Created initial admin user: {admin_username}"
    return status


def seed_hazard_points(session) -> Dict[str, Any]:
    from backend.app.models import HazardPointORM

    status = {"seeded": False, "count": 0, "message": ""}

    count = session.query(HazardPointORM).count()
    if count > 0:
        status["count"] = count
        status["message"] = f"Hazard points already exist ({count} items)."
        return status

    shp_path = _resolve_hazard_shapefile()
    if not os.path.exists(shp_path):
        status["message"] = f"Hazard points file not found: {shp_path}"
        return status

    import geopandas as gpd
    from geoalchemy2.shape import from_shape
    from shapely.geometry import Point

    gdf = gpd.read_file(shp_path, engine="pyogrio")
    if gdf.crs and gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)

    field_tybh = ["TYBH", "tybh", "统一编", "统一编号", "UNIFIED_ID"]
    field_hazard_type = ["灾害类型", "灾害类", "ZHLX", "hazard_type", "TYPE"]
    field_hazard_name = ["灾害名", "ZHMC", "hazard_name", "NAME"]
    field_city = ["市", "CITY", "city"]
    field_county = ["县", "COUNTY", "county"]
    field_township = ["乡", "TOWNSHIP", "township", "乡镇"]
    field_lon = ["经度", "LON", "longitude"]
    field_lat = ["纬度", "维度", "LAT", "latitude"]

    def pick_value(row_obj, candidates):
        for key in candidates:
            if key in row_obj and row_obj[key] not in (None, ""):
                return row_obj[key]
        return None

    def to_float(value):
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    added_count = 0
    for _, row in gdf.iterrows():
        tybh_value = pick_value(row, field_tybh)
        if tybh_value is None:
            continue

        geom_x = getattr(row.geometry, "x", None)
        geom_y = getattr(row.geometry, "y", None)
        lon_value = to_float(geom_x)
        lat_value = to_float(geom_y)
        if lon_value is None:
            lon_value = to_float(pick_value(row, field_lon))
        if lat_value is None:
            lat_value = to_float(pick_value(row, field_lat))
        if lon_value is None or lat_value is None:
            continue

        point = HazardPointORM(
            tybh=str(tybh_value).strip(),
            hazard_type=pick_value(row, field_hazard_type),
            hazard_name=pick_value(row, field_hazard_name),
            city=pick_value(row, field_city),
            county=pick_value(row, field_county),
            township=pick_value(row, field_township),
            longitude=lon_value,
            latitude=lat_value,
            geom=from_shape(Point(lon_value, lat_value), srid=4326),
        )
        session.add(point)
        added_count += 1

    session.commit()
    status["seeded"] = True
    status["count"] = added_count
    status["message"] = f"Imported {added_count} hazard points."
    return status


def ensure_database_ready(
    database_url: str | None = None,
    *,
    bootstrap_admin: bool = True,
    seed_hazard: bool = True,
) -> Dict[str, Any]:
    database_url = database_url or settings.DATABASE_URL
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured.")

    reset_on_mismatch = read_bool_env("DB_SCHEMA_RESET_ON_MISMATCH", False)
    reset_confirm = read_bool_env("DB_SCHEMA_RESET_CONFIRM", False)
    allow_schema_reset = reset_on_mismatch and reset_confirm

    sync_url = _database_url_to_sync_url(database_url)
    parsed = urlparse(sync_url)
    engine = create_engine(sync_url)
    Session = sessionmaker(bind=engine)

    result: Dict[str, Any] = {
        "database": parsed.path.strip("/"),
        "host": parsed.hostname,
        "schema_reset": False,
        "mismatch_detected": False,
        "mismatch_reasons": [],
        "added_columns": [],
        "applied_sql_files": [],
        "admin": None,
        "hazard_seed": None,
    }

    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS postgis")

            base = _load_base()
            inspector = inspect(conn)
            schema_diagnostics = inspect_database_structure(conn)
            bootstrap_required = bool(schema_diagnostics.get("bootstrap_required"))
            result["mismatch_detected"] = schema_diagnostics["mismatch"] and not bootstrap_required
            result["mismatch_reasons"] = [] if bootstrap_required else schema_diagnostics["reasons"]
            result["bootstrap_initialized"] = False

            if schema_diagnostics["mismatch"]:
                if bootstrap_required:
                    print("[INFO] Empty application schema detected. Bootstrapping database tables...")
                else:
                    print("[WARN] Database schema mismatch detected.")
                    for reason in schema_diagnostics["reasons"]:
                        print(f"  - {reason}")
                if allow_schema_reset:
                    _drop_all_objects(conn, inspector)
                    base.metadata.create_all(bind=conn)
                    result["schema_reset"] = True
                else:
                    base.metadata.create_all(bind=conn)
                    inspector = inspect(conn)
                    result["added_columns"] = _add_missing_columns(conn, inspector, base, engine.dialect)
                if bootstrap_required:
                    result["bootstrap_initialized"] = True
            else:
                base.metadata.create_all(bind=conn)

            migrations_dir = os.path.join(project_root(), "backend", "migrations")
            for migration_file in MIGRATION_FILES:
                migration_path = os.path.join(migrations_dir, migration_file)
                if _apply_sql_file(conn, migration_path):
                    result["applied_sql_files"].append(migration_file)

        session = Session()
        try:
            if bootstrap_admin:
                result["admin"] = bootstrap_admin_user(session)
            if seed_hazard:
                result["hazard_seed"] = seed_hazard_points(session)
        finally:
            session.close()

        return result
    finally:
        engine.dispose()
