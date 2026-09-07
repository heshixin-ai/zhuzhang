from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PRIOR_REVISION = "f6c2a9b81d30"
HEAD_REVISION = "8f4c2d7a9b10"
TARGET_EMAIL_ENV = "ZHUZHANG_DEMO_OWNER_ADOPTION_EMAIL"


def _run_alembic(
    database_path: Path,
    *arguments: str,
    target_email: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop(TARGET_EMAIL_ENV, None)
    env.update({
        "APP_ENV": "test",
        "ZHUZHANG_APP_ENV": "test",
        "DATABASE_URL": f"sqlite:///{database_path}",
    })
    if target_email is not None:
        env[TARGET_EMAIL_ENV] = target_email
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=BACKEND_ROOT,
        env=env,
        check=check,
        capture_output=True,
        text=True,
    )


def _insert_user(
    connection: sqlite3.Connection,
    user_id: str,
    email: str,
    *,
    status: str = "active",
) -> None:
    connection.execute(
        """
        INSERT INTO users (id, name, email, status, email_verified_at, created_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        (user_id, user_id, email, status),
    )


def _insert_project(connection: sqlite3.Connection, project_id: str, name: str) -> None:
    connection.execute(
        """
        INSERT INTO projects (
            id, name, city, area_sqm, area_basis, renovation_type, notes,
            fund_limit_cents, reserve_cents, status, source_file_count,
            source_bytes, created_at
        ) VALUES (?, ?, 'Suzhou', 100, 'usable area', 'full service', '',
                  10000000, 1000000, 'active', 0, 0, CURRENT_TIMESTAMP)
        """,
        (project_id, name),
    )
    connection.execute(
        """
        INSERT INTO baseline_versions (
            id, project_id, version, amount_cents, reason, is_active,
            confirmed_by, confirmed_at
        ) VALUES (?, ?, 1, 8800000, 'migration sentinel', 1,
                  'project owner', CURRENT_TIMESTAMP)
        """,
        (f"baseline-{project_id}", project_id),
    )


def _insert_membership(
    connection: sqlite3.Connection,
    membership_id: str,
    user_id: str,
    project_id: str,
    *,
    role: str = "owner",
    status: str = "active",
) -> None:
    connection.execute(
        """
        INSERT INTO project_memberships (
            id, user_id, project_id, role, status, created_at
        ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (membership_id, user_id, project_id, role, status),
    )


def _insert_auth_records(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        INSERT INTO login_sessions (
            id, user_id, token_hash, csrf_hash, user_agent, ip_hash,
            authenticated_at, last_seen_at, expires_at, revoked_at, created_at
        ) VALUES
            (
                'demo-active-session', 'demo-owner',
                'demo-active-token', 'demo-active-csrf', 'migration test',
                'demo-active-ip', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
                '2030-01-01 00:00:00', NULL, CURRENT_TIMESTAMP
            ),
            (
                'demo-revoked-session', 'demo-owner',
                'demo-revoked-token', 'demo-revoked-csrf', 'migration test',
                'demo-revoked-ip', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
                '2030-01-01 00:00:00', '2026-01-01 00:00:00', CURRENT_TIMESTAMP
            );
        INSERT INTO login_challenges (
            id, email, code_hash, status, attempts, max_attempts,
            request_ip_hash, expires_at, created_at, consumed_at
        ) VALUES
            (
                'demo-pending-challenge', 'Owner@Example.Local', 'demo-code',
                'pending', 0, 5, 'demo-challenge-ip',
                '2030-01-01 00:00:00', CURRENT_TIMESTAMP, NULL
            ),
            (
                'demo-consumed-challenge', 'owner@example.local', 'used-code',
                'consumed', 0, 5, 'used-challenge-ip',
                '2030-01-01 00:00:00', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            );
    """)


def _snapshot(connection: sqlite3.Connection) -> dict[str, list[tuple]]:
    tables = (
        "users",
        "projects",
        "project_memberships",
        "baseline_versions",
        "login_sessions",
        "login_challenges",
    )
    return {
        table: connection.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
        for table in tables
    }


def test_explicit_target_adopts_exclusive_demo_projects_without_data_loss(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "explicit-target.sqlite3"
    _run_alembic(database_path, "upgrade", PRIOR_REVISION)

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("UPDATE users SET status = 'active' WHERE id = 'demo-owner'")
        _insert_user(connection, "real-owner", "real-owner@example.com")
        _insert_user(connection, "unrelated-user", "unrelated@example.com")

        _insert_project(connection, "exclusive-new", "Exclusive new membership")
        _insert_project(connection, "exclusive-existing", "Exclusive existing membership")
        _insert_project(connection, "shared-project", "Already shared")
        for project_id in ("exclusive-new", "exclusive-existing", "shared-project"):
            _insert_membership(
                connection,
                f"demo-{project_id}",
                "demo-owner",
                project_id,
            )
        _insert_membership(
            connection,
            "existing-real-membership",
            "real-owner",
            "exclusive-existing",
            role="viewer",
            status="revoked",
        )
        _insert_membership(
            connection,
            "shared-real-owner",
            "real-owner",
            "shared-project",
        )
        _insert_auth_records(connection)
        connection.execute(
            """
            INSERT INTO login_sessions (
                id, user_id, token_hash, csrf_hash, user_agent, ip_hash,
                authenticated_at, last_seen_at, expires_at, revoked_at, created_at
            ) VALUES (
                'real-active-session', 'real-owner', 'real-token', 'real-csrf',
                'migration test', 'real-ip', CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP, '2030-01-01 00:00:00', NULL,
                CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            INSERT INTO login_challenges (
                id, email, code_hash, status, attempts, max_attempts,
                request_ip_hash, expires_at, created_at, consumed_at
            ) VALUES (
                'real-pending-challenge', 'real-owner@example.com',
                'real-code', 'pending', 0, 5, 'real-challenge-ip',
                '2030-01-01 00:00:00', CURRENT_TIMESTAMP, NULL
            )
            """
        )

    _run_alembic(
        database_path,
        "upgrade",
        "head",
        target_email="REAL-OWNER@EXAMPLE.COM",
    )

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == (HEAD_REVISION,)
        assert connection.execute(
            "SELECT id, name FROM projects ORDER BY id"
        ).fetchall() == [
            ("exclusive-existing", "Exclusive existing membership"),
            ("exclusive-new", "Exclusive new membership"),
            ("shared-project", "Already shared"),
        ]
        assert connection.execute(
            "SELECT project_id, amount_cents, reason FROM baseline_versions ORDER BY project_id"
        ).fetchall() == [
            ("exclusive-existing", 8800000, "migration sentinel"),
            ("exclusive-new", 8800000, "migration sentinel"),
            ("shared-project", 8800000, "migration sentinel"),
        ]
        real_memberships = connection.execute(
            """
            SELECT id, project_id, role, status
            FROM project_memberships
            WHERE user_id = 'real-owner'
            ORDER BY project_id
            """
        ).fetchall()
        assert real_memberships[0] == (
            "existing-real-membership",
            "exclusive-existing",
            "owner",
            "active",
        )
        assert real_memberships[1][1:] == ("exclusive-new", "owner", "active")
        assert len(real_memberships[1][0]) == 36
        assert real_memberships[2] == (
            "shared-real-owner",
            "shared-project",
            "owner",
            "active",
        )
        assert connection.execute(
            """
            SELECT project_id, role, status
            FROM project_memberships
            WHERE user_id = 'demo-owner'
            ORDER BY project_id
            """
        ).fetchall() == [
            ("exclusive-existing", "owner", "revoked"),
            ("exclusive-new", "owner", "revoked"),
            ("shared-project", "owner", "revoked"),
        ]
        assert connection.execute(
            "SELECT status FROM users WHERE id = 'demo-owner'"
        ).fetchone() == ("disabled",)
        assert connection.execute(
            "SELECT revoked_at IS NOT NULL FROM login_sessions WHERE id = 'demo-active-session'"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT revoked_at FROM login_sessions WHERE id = 'demo-revoked-session'"
        ).fetchone() == ("2026-01-01 00:00:00",)
        assert connection.execute(
            "SELECT revoked_at FROM login_sessions WHERE id = 'real-active-session'"
        ).fetchone() == (None,)
        assert connection.execute(
            "SELECT status FROM login_challenges WHERE id = 'demo-pending-challenge'"
        ).fetchone() == ("expired",)
        assert connection.execute(
            "SELECT status FROM login_challenges WHERE id = 'demo-consumed-challenge'"
        ).fetchone() == ("consumed",)
        assert connection.execute(
            "SELECT status FROM login_challenges WHERE id = 'real-pending-challenge'"
        ).fetchone() == ("pending",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    # Downgrade only moves the schema revision marker. It must never recreate
    # demo grants or undo an explicitly approved ownership transfer.
    _run_alembic(database_path, "downgrade", PRIOR_REVISION)
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            """
            SELECT role, status
            FROM project_memberships
            WHERE user_id = 'real-owner' AND project_id = 'exclusive-new'
            """
        ).fetchone() == ("owner", "active")
        assert connection.execute(
            """
            SELECT status
            FROM project_memberships
            WHERE user_id = 'demo-owner' AND project_id = 'exclusive-new'
            """
        ).fetchone() == ("revoked",)
        assert connection.execute(
            "SELECT status FROM users WHERE id = 'demo-owner'"
        ).fetchone() == ("disabled",)


def test_missing_target_does_not_guess_the_only_active_user_or_stamp_revision(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "missing-target.sqlite3"
    _run_alembic(database_path, "upgrade", PRIOR_REVISION)

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("UPDATE users SET status = 'active' WHERE id = 'demo-owner'")
        _insert_user(connection, "only-real-user", "only-real-user@example.com")
        _insert_project(connection, "unresolved-project", "Must remain with demo")
        _insert_membership(
            connection,
            "unresolved-demo-membership",
            "demo-owner",
            "unresolved-project",
        )
        _insert_auth_records(connection)
        before = _snapshot(connection)

    result = _run_alembic(database_path, "upgrade", "head", check=False)

    assert result.returncode != 0
    assert TARGET_EMAIL_ENV in result.stderr
    with sqlite3.connect(database_path) as connection:
        assert _snapshot(connection) == before
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == (PRIOR_REVISION,)


@pytest.mark.parametrize(
    "target_email",
    [
        "missing@example.com",
        "disabled@example.com",
        "owner@example.local",
    ],
    ids=("missing-user", "disabled-user", "demo-user"),
)
def test_invalid_target_aborts_before_writes(
    tmp_path: Path,
    target_email: str,
) -> None:
    database_path = tmp_path / "invalid-target.sqlite3"
    _run_alembic(database_path, "upgrade", PRIOR_REVISION)

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("UPDATE users SET status = 'active' WHERE id = 'demo-owner'")
        _insert_user(connection, "valid-user", "valid@example.com")
        _insert_user(
            connection,
            "disabled-user",
            "disabled@example.com",
            status="disabled",
        )
        _insert_project(connection, "invalid-target-project", "Must remain with demo")
        _insert_membership(
            connection,
            "invalid-target-demo-membership",
            "demo-owner",
            "invalid-target-project",
        )
        _insert_auth_records(connection)
        before = _snapshot(connection)

    result = _run_alembic(
        database_path,
        "upgrade",
        "head",
        target_email=target_email,
        check=False,
    )

    assert result.returncode != 0
    assert "无效或不唯一" in result.stderr
    with sqlite3.connect(database_path) as connection:
        assert _snapshot(connection) == before
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == (PRIOR_REVISION,)


def test_case_insensitive_duplicate_target_email_is_rejected_as_ambiguous(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "ambiguous-target.sqlite3"
    _run_alembic(database_path, "upgrade", PRIOR_REVISION)

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("UPDATE users SET status = 'active' WHERE id = 'demo-owner'")
        _insert_user(connection, "duplicate-a", "duplicate@example.com")
        _insert_user(connection, "duplicate-b", "DUPLICATE@example.com")
        _insert_project(connection, "ambiguous-target-project", "Must remain with demo")
        _insert_membership(
            connection,
            "ambiguous-target-demo-membership",
            "demo-owner",
            "ambiguous-target-project",
        )
        _insert_auth_records(connection)
        before = _snapshot(connection)

    result = _run_alembic(
        database_path,
        "upgrade",
        "head",
        target_email="duplicate@example.com",
        check=False,
    )

    assert result.returncode != 0
    assert "无效或不唯一" in result.stderr
    with sqlite3.connect(database_path) as connection:
        assert _snapshot(connection) == before
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == (PRIOR_REVISION,)


def test_shared_project_is_safely_isolated_without_an_adoption_target(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "shared-project.sqlite3"
    _run_alembic(database_path, "upgrade", PRIOR_REVISION)

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("UPDATE users SET status = 'active' WHERE id = 'demo-owner'")
        _insert_user(connection, "real-owner", "real-owner@example.com")
        _insert_project(connection, "shared-project", "Already has a real owner")
        _insert_membership(
            connection,
            "shared-demo-owner",
            "demo-owner",
            "shared-project",
        )
        _insert_membership(
            connection,
            "shared-real-owner",
            "real-owner",
            "shared-project",
        )
        _insert_auth_records(connection)

    _run_alembic(database_path, "upgrade", "head")

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == (HEAD_REVISION,)
        assert connection.execute(
            "SELECT id, name FROM projects"
        ).fetchone() == ("shared-project", "Already has a real owner")
        assert connection.execute(
            "SELECT project_id, amount_cents FROM baseline_versions"
        ).fetchone() == ("shared-project", 8800000)
        assert connection.execute(
            """
            SELECT id, role, status
            FROM project_memberships
            WHERE user_id = 'real-owner'
            """
        ).fetchone() == ("shared-real-owner", "owner", "active")
        assert connection.execute(
            """
            SELECT role, status
            FROM project_memberships
            WHERE user_id = 'demo-owner'
            """
        ).fetchone() == ("owner", "revoked")
        assert connection.execute(
            "SELECT status FROM users WHERE id = 'demo-owner'"
        ).fetchone() == ("disabled",)
        assert connection.execute(
            "SELECT revoked_at IS NOT NULL FROM login_sessions WHERE id = 'demo-active-session'"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT status FROM login_challenges WHERE id = 'demo-pending-challenge'"
        ).fetchone() == ("expired",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_clean_install_reaches_head_without_an_adoption_target(tmp_path: Path) -> None:
    database_path = tmp_path / "clean-install.sqlite3"

    _run_alembic(database_path, "upgrade", "head")

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == (HEAD_REVISION,)
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone() == (0,)
        assert connection.execute(
            "SELECT status FROM users WHERE id = 'demo-owner'"
        ).fetchone() == ("disabled",)
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM project_memberships
            WHERE user_id = 'demo-owner' AND status = 'active'
            """
        ).fetchone() == (0,)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    _run_alembic(database_path, "check")
