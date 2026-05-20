"""add flood pipeline models

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table_name: str) -> bool:
    return table_name in set(sa.inspect(op.get_bind()).get_table_names())


def _has_index(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return index_name in {idx["name"] for idx in inspector.get_indexes(table_name)}


def _create_index_if_missing(
    table_name: str,
    index_name: str,
    columns: list[str],
    *,
    unique: bool = False,
    **kwargs,
) -> None:
    if _has_table(table_name) and not _has_index(table_name, index_name):
        op.create_index(index_name, table_name, columns, unique=unique, **kwargs)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    if not _has_table("water_extractions"):
        op.create_table(
            "water_extractions",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("scene_id", sa.Integer(), nullable=True),
            sa.Column("processor", sa.String(length=32), nullable=False, server_default="otsu"),
            sa.Column("task_id", sa.String(length=64), nullable=True),
            sa.Column("input_path", sa.String(), nullable=True),
            sa.Column("output_path", sa.String(), nullable=True),
            sa.Column("preview_path", sa.String(), nullable=True),
            sa.Column("vector_path", sa.String(), nullable=True),
            sa.Column("water_area_km2", sa.Float(), nullable=True),
            sa.Column("water_pixel_count", sa.Integer(), nullable=True),
            sa.Column("threshold_value", sa.Float(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
            sa.Column("error_msg", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
            sa.ForeignKeyConstraint(["scene_id"], ["sar_scene_geo.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("water_extractions", "ix_water_extractions_scene_id", ["scene_id"])
    _create_index_if_missing("water_extractions", "ix_water_extractions_processor", ["processor"])
    _create_index_if_missing("water_extractions", "ix_water_extractions_task_id", ["task_id"])
    _create_index_if_missing("water_extractions", "ix_water_extractions_status", ["status"])

    if not _has_table("flood_overlays"):
        op.create_table(
            "flood_overlays",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("detection_id", sa.Integer(), nullable=False),
            sa.Column("flood_vector_path", sa.String(), nullable=True),
            sa.Column("hazard_points_hit", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("hazard_points_near", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("hazard_points_total", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("dinsar_products_intersecting", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("affected_area_km2", sa.Float(), nullable=True),
            sa.Column("summary_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
            sa.ForeignKeyConstraint(["detection_id"], ["flood_detections.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("flood_overlays", "ix_flood_overlays_detection_id", ["detection_id"])

    if not _has_table("flood_products"):
        op.create_table(
            "flood_products",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("product_id", sa.String(length=64), nullable=False),
            sa.Column("detection_id", sa.Integer(), nullable=True),
            sa.Column("overlay_id", sa.Integer(), nullable=True),
            sa.Column("display_name", sa.String(length=255), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="READY"),
            sa.Column("publish_dir", sa.String(), nullable=True),
            sa.Column("manifest_path", sa.String(), nullable=True),
            sa.Column("geom", Geometry("POLYGON", srid=4326), nullable=True),
            sa.Column("summary_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
            sa.ForeignKeyConstraint(["detection_id"], ["flood_detections.id"]),
            sa.ForeignKeyConstraint(["overlay_id"], ["flood_overlays.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("publish_dir", name="uq_flood_products_publish_dir"),
            sa.UniqueConstraint("manifest_path", name="uq_flood_products_manifest_path"),
        )
    _create_index_if_missing("flood_products", "ix_flood_products_product_id", ["product_id"], unique=True)
    _create_index_if_missing("flood_products", "ix_flood_products_detection_id", ["detection_id"])
    _create_index_if_missing("flood_products", "ix_flood_products_overlay_id", ["overlay_id"])
    _create_index_if_missing("flood_products", "ix_flood_products_status", ["status"])

    if _has_table("water_detections") and _has_table("water_extractions"):
        op.execute(
            sa.text(
                """
                INSERT INTO water_extractions (
                    id, scene_id, processor, input_path, output_path,
                    water_area_km2, water_pixel_count, threshold_value,
                    status, error_msg, created_at, updated_at
                )
                SELECT
                    wd.id, wd.scene_id, 'otsu', wd.input_path, wd.output_path,
                    wd.water_area_km2, wd.water_pixel_count, wd.otsu_threshold_db,
                    wd.status, wd.error_msg, wd.created_at, wd.updated_at
                FROM water_detections wd
                WHERE NOT EXISTS (
                    SELECT 1 FROM water_extractions we WHERE we.id = wd.id
                )
                """
            )
        )
        op.execute(
            sa.text(
                """
                SELECT setval(
                    pg_get_serial_sequence('water_extractions', 'id'),
                    COALESCE((SELECT MAX(id) FROM water_extractions), 1),
                    (SELECT COUNT(*) FROM water_extractions) > 0
                )
                """
            )
        )


def downgrade() -> None:
    if _has_table("flood_products"):
        op.drop_table("flood_products")
    if _has_table("flood_overlays"):
        op.drop_table("flood_overlays")
    if _has_table("water_extractions"):
        op.drop_table("water_extractions")
