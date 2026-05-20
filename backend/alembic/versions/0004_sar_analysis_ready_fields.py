"""add SAR analysis-ready scene fields

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table_name: str) -> bool:
    return table_name in set(sa.inspect(op.get_bind()).get_table_names())


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column_name in {col["name"] for col in inspector.get_columns(table_name)}


def _has_index(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return index_name in {idx["name"] for idx in inspector.get_indexes(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if _has_table(table_name) and not _has_column(table_name, column.name):
        op.add_column(table_name, column)


def _create_index_if_missing(table_name: str, index_name: str, columns: list[str]) -> None:
    if _has_table(table_name) and not _has_index(table_name, index_name):
        op.create_index(index_name, table_name, columns)


def _drop_index_if_exists(table_name: str, index_name: str) -> None:
    if _has_table(table_name) and _has_index(table_name, index_name):
        op.drop_index(index_name, table_name=table_name)


def _drop_column_if_exists(table_name: str, column_name: str) -> None:
    if _has_table(table_name) and _has_column(table_name, column_name):
        op.drop_column(table_name, column_name)


def upgrade() -> None:
    table = "sar_scene_geo"
    _add_column_if_missing(table, sa.Column("analysis_tif_path", sa.String(), nullable=True))
    _add_column_if_missing(table, sa.Column("analysis_dir", sa.String(), nullable=True))
    _add_column_if_missing(table, sa.Column("analysis_preview_path", sa.String(), nullable=True))
    _add_column_if_missing(table, sa.Column("analysis_engine", sa.String(length=32), nullable=True))
    _add_column_if_missing(table, sa.Column("analysis_profile", sa.String(length=64), nullable=True))
    _add_column_if_missing(table, sa.Column("analysis_backscatter_unit", sa.String(length=32), nullable=True))
    _add_column_if_missing(table, sa.Column("analysis_nodata_value", sa.Float(), nullable=True))
    _add_column_if_missing(table, sa.Column("analysis_metadata_json", sa.JSON(), nullable=True))
    _add_column_if_missing(table, sa.Column("analysis_quality_json", sa.JSON(), nullable=True))

    _create_index_if_missing(table, "ix_sar_scene_geo_analysis_tif_path", ["analysis_tif_path"])
    _create_index_if_missing(table, "ix_sar_scene_geo_analysis_engine", ["analysis_engine"])
    _create_index_if_missing(table, "ix_sar_scene_geo_analysis_profile", ["analysis_profile"])


def downgrade() -> None:
    table = "sar_scene_geo"
    _drop_index_if_exists(table, "ix_sar_scene_geo_analysis_profile")
    _drop_index_if_exists(table, "ix_sar_scene_geo_analysis_engine")
    _drop_index_if_exists(table, "ix_sar_scene_geo_analysis_tif_path")

    for column_name in (
        "analysis_quality_json",
        "analysis_metadata_json",
        "analysis_nodata_value",
        "analysis_backscatter_unit",
        "analysis_profile",
        "analysis_engine",
        "analysis_preview_path",
        "analysis_dir",
        "analysis_tif_path",
    ):
        _drop_column_if_exists(table, column_name)
