"""water_v2: 新增 sar_scene_geo 和 flood_detections 表

Revision ID: 0002
Revises: 0001
Create Date: 2026-02-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 清理旧 water 表（如果存在）
    op.execute("DROP TABLE IF EXISTS flood_events CASCADE")
    op.execute("DROP TABLE IF EXISTS water_pairs CASCADE")
    op.execute("DROP TABLE IF EXISTS water_masks CASCADE")

    op.create_table(
        "sar_scene_geo",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("radar_data_id", sa.Integer(), nullable=False),
        sa.Column("geo_path", sa.String(), nullable=True),
        sa.Column("pixel_size_m", sa.Float(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="PENDING"),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["radar_data_id"], ["radar_data.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("radar_data_id", name="uq_sar_scene_geo_radar"),
    )
    op.create_index("ix_sar_scene_geo_radar_data_id", "sar_scene_geo", ["radar_data_id"])
    op.create_index("ix_sar_scene_geo_status", "sar_scene_geo", ["status"])

    op.create_table(
        "flood_detections",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("pre_scene_id", sa.Integer(), nullable=False),
        sa.Column("post_scene_id", sa.Integer(), nullable=False),
        sa.Column("output_dir", sa.String(), nullable=True),
        sa.Column("classified_path", sa.String(), nullable=True),
        sa.Column("flood_area_km2", sa.Float(), nullable=True),
        sa.Column("stable_water_area_km2", sa.Float(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="PENDING"),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["pre_scene_id"], ["sar_scene_geo.id"]),
        sa.ForeignKeyConstraint(["post_scene_id"], ["sar_scene_geo.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pre_scene_id", "post_scene_id", name="uq_flood_detection_pair"),
    )
    op.create_index("ix_flood_detections_pre_scene_id", "flood_detections", ["pre_scene_id"])
    op.create_index("ix_flood_detections_post_scene_id", "flood_detections", ["post_scene_id"])
    op.create_index("ix_flood_detections_status", "flood_detections", ["status"])


def downgrade() -> None:
    op.drop_table("flood_detections")
    op.drop_table("sar_scene_geo")
