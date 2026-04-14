"""baseline: 标记现有 schema 为 Alembic 管理起点

此迁移为空操作（no-op）。数据库表结构已由应用启动时的 create_all 创建。
首次引入 Alembic 时，请执行以下命令将当前数据库标记为此基线：

    cd backend
    alembic stamp head

之后所有 schema 变更均通过 alembic revision --autogenerate 管理。

Revision ID: 0001
Revises:
Create Date: 2026-02-19
"""
from typing import Sequence, Union

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 基线迁移：数据库已由 create_all 初始化，此处无需操作。
    pass


def downgrade() -> None:
    # 基线无法回滚。
    pass
