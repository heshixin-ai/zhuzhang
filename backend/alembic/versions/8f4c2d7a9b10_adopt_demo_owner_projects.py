"""adopt demo-owned projects for an explicitly selected real user

Revision ID: 8f4c2d7a9b10
Revises: f6c2a9b81d30
Create Date: 2026-09-07 04:30:00
"""

from __future__ import annotations

import os
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op


revision: str = "8f4c2d7a9b10"
down_revision: Union[str, None] = "f6c2a9b81d30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DEMO_USER_ID = "demo-owner"
DEMO_EMAIL = "owner@example.local"
TARGET_EMAIL_ENV = "ZHUZHANG_DEMO_OWNER_ADOPTION_EMAIL"


def _resolve_target_user_id(bind) -> str:
    target_email = os.getenv(TARGET_EMAIL_ENV, "").strip().lower()
    if not target_email:
        raise RuntimeError(
            "数据库仍存在由 demo-owner 独占所有权的项目，但未配置显式接管人。"
            f"请设置 {TARGET_EMAIL_ENV} 后重试；本次迁移未执行。"
        )
    if target_email == DEMO_EMAIL:
        raise RuntimeError(
            "demo-owner 迁移目标无效或不唯一：目标必须精确匹配一个 "
            "status='active' 的非 demo 用户；本次迁移未执行。"
        )

    matches = list(bind.execute(
        sa.text("""
            SELECT id
            FROM users
            WHERE id <> :demo_user_id
              AND status = 'active'
              AND lower(email) = :target_email
            ORDER BY id
            LIMIT 2
        """),
        {"demo_user_id": DEMO_USER_ID, "target_email": target_email},
    ).scalars())
    if len(matches) != 1:
        raise RuntimeError(
            "demo-owner 迁移目标无效或不唯一：目标必须精确匹配一个 "
            "status='active' 的非 demo 用户；本次迁移未执行。"
        )
    return matches[0]


def upgrade() -> None:
    bind = op.get_bind()
    exclusive_project_ids = list(bind.execute(
        sa.text("""
            SELECT demo_membership.project_id
            FROM project_memberships AS demo_membership
            WHERE demo_membership.user_id = :demo_user_id
              AND demo_membership.role = 'owner'
              AND demo_membership.status = 'active'
              AND NOT EXISTS (
                  SELECT 1
                  FROM project_memberships AS other_owner
                  JOIN users AS other_user ON other_user.id = other_owner.user_id
                  WHERE other_owner.project_id = demo_membership.project_id
                    AND other_owner.user_id <> :demo_user_id
                    AND other_owner.role = 'owner'
                    AND other_owner.status = 'active'
                    AND other_user.status = 'active'
              )
            ORDER BY demo_membership.project_id
        """),
        {"demo_user_id": DEMO_USER_ID},
    ).scalars())

    # Never infer ownership from the number of users in the database. Only
    # projects that would otherwise lose their sole active owner need an
    # explicit recipient. Missing, invalid, or ambiguous configuration aborts
    # before any write, so Alembic cannot stamp unresolved access as migrated.
    candidate_id = _resolve_target_user_id(bind) if exclusive_project_ids else None
    for project_id in exclusive_project_ids:
        existing_membership_id = bind.execute(
            sa.text("""
                SELECT id
                FROM project_memberships
                WHERE user_id = :candidate_id AND project_id = :project_id
            """),
            {"candidate_id": candidate_id, "project_id": project_id},
        ).scalar_one_or_none()
        if existing_membership_id is None:
            bind.execute(
                sa.text("""
                    INSERT INTO project_memberships (
                        id, user_id, project_id, role, status, created_at
                    ) VALUES (
                        :membership_id, :candidate_id, :project_id,
                        'owner', 'active', CURRENT_TIMESTAMP
                    )
                """),
                {
                    "membership_id": str(uuid4()),
                    "candidate_id": candidate_id,
                    "project_id": project_id,
                },
            )
        else:
            bind.execute(
                sa.text("""
                    UPDATE project_memberships
                    SET role = 'owner', status = 'active'
                    WHERE id = :membership_id
                """),
                {"membership_id": existing_membership_id},
            )

    # This also covers projects that already have another active owner: their
    # existing ownership remains unchanged and only demo access is removed.
    bind.execute(
        sa.text("""
            UPDATE project_memberships
            SET status = 'revoked'
            WHERE user_id = :demo_user_id AND status = 'active'
        """),
        {"demo_user_id": DEMO_USER_ID},
    )
    bind.execute(
        sa.text("""
            UPDATE login_sessions
            SET revoked_at = CURRENT_TIMESTAMP
            WHERE user_id = :demo_user_id AND revoked_at IS NULL
        """),
        {"demo_user_id": DEMO_USER_ID},
    )
    bind.execute(
        sa.text("""
            UPDATE login_challenges
            SET status = 'expired'
            WHERE lower(email) = :demo_email AND status = 'pending'
        """),
        {"demo_email": DEMO_EMAIL},
    )
    bind.execute(
        sa.text("""
            UPDATE users
            SET status = 'disabled'
            WHERE id = :demo_user_id
        """),
        {"demo_user_id": DEMO_USER_ID},
    )


def downgrade() -> None:
    # This data migration is intentionally irreversible: a downgrade cannot
    # safely determine which grants, sessions, or challenge states existed
    # before the explicit ownership transfer. Restore a verified backup if the
    # pre-migration identity state must be recovered; never re-enable demo
    # access automatically.
    pass
