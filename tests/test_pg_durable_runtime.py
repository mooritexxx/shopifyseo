"""Durable Postgres box runtime: start-app, backups, cron, ensure-postgres."""

from __future__ import annotations

import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

from shopifyseo.db.compat import _translate_placeholders

ROOT = Path(__file__).resolve().parents[1]
ENSURE_SH = ROOT / "scripts" / "ensure-postgres.sh"
START_APP = ROOT / "scripts" / "start-app.sh"
MARK_SH = ROOT / "scripts" / "mark-pg-live.sh"
BACKUP_SH = ROOT / "scripts" / "pg-nightly-backup.sh"
CRON_SH = ROOT / "scripts" / "install-pg-backup-cron.sh"
DOCS = ROOT / "docs" / "pg-cutover.md"

_PG_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
_IS_PG = _PG_URL.lower().startswith(("postgresql://", "postgres://"))


def _run(args: list[str], *, env: dict[str, str] | None = None, **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=False,
        text=True,
        capture_output=True,
        **kwargs,
        env=env,
    )


def _chmod_exec(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _write_shim(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
    _chmod_exec(path)
    return path


def _base_env(tmp: Path, *, path_prefix: Path | None = None, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["SHOPIFYSEO_PG_LIVE_MARK"] = str(tmp / "pg_live_cutover.json")
    env["SHOPIFYSEO_PG_ENV"] = str(tmp / "pg.env")
    env["SHOPIFYSEO_UVICORN_LOG"] = str(tmp / "logs" / "uvicorn.log")
    env["SHOPIFYSEO_UVICORN_PORT"] = "18000"
    env["SHOPIFYSEO_SKIP_ENSURE_POSTGRES"] = "1"
    env["SHOPIFYSEO_SKIP_BACKUP_CRON"] = "1"
    env["SHOPIFYSEO_PG_BACKUP_DIR"] = str(tmp / "backups")
    env["SHOPIFYSEO_ENSURE_POSTGRES_NO_INSTALL"] = "1"
    if path_prefix is not None:
        env["PATH"] = f"{path_prefix}{os.pathsep}{env.get('PATH', '')}"
    if extra:
        env.update(extra)
    return env


def test_runtime_scripts_exist_executable_and_have_valid_syntax():
    for path in (ENSURE_SH, START_APP, MARK_SH, BACKUP_SH, CRON_SH):
        assert path.is_file(), path
        assert os.access(path, os.X_OK), f"{path} should be executable"
        proc = _run(["bash", "-n", str(path)])
        assert proc.returncode == 0, proc.stderr


def test_runtime_scripts_help():
    for path in (ENSURE_SH, START_APP, MARK_SH, BACKUP_SH, CRON_SH):
        proc = _run(["bash", str(path), "--help"])
        assert proc.returncode == 0, proc.stderr
        assert "Usage" in proc.stdout or "usage" in proc.stdout.lower()


def test_start_app_no_mark_is_sqlite_and_unsets_database_url(tmp_path):
    env = _base_env(tmp_path)
    env["DATABASE_URL"] = "postgresql://shopifyseo:secret@127.0.0.1:5432/shopifyseo"
    proc = _run(["bash", str(START_APP), "--decide-only"], env=env)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "sqlite"
    assert "DATABASE_URL=unset" in proc.stderr
    assert "secret" not in proc.stdout
    assert "secret" not in proc.stderr


def test_start_app_live_mark_reachable_pg_is_postgres(tmp_path):
    shims = tmp_path / "bin"
    _write_shim(shims, "pg_isready", "exit 0\n")
    env = _base_env(tmp_path, path_prefix=shims)
    (tmp_path / "pg_live_cutover.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pg.env").write_text(
        "DATABASE_URL=postgresql://shopifyseo:secret@127.0.0.1:5432/shopifyseo\n",
        encoding="utf-8",
    )
    proc = _run(["bash", str(START_APP), "--decide-only"], env=env)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "postgres"
    assert "DATABASE_URL=set" in proc.stderr
    assert "secret" not in proc.stdout
    assert "secret" not in proc.stderr


def test_start_app_live_mark_pg_down_is_error_no_sqlite_fallback(tmp_path):
    shims = tmp_path / "bin"
    _write_shim(shims, "pg_isready", "exit 1\n")
    env = _base_env(tmp_path, path_prefix=shims)
    (tmp_path / "pg_live_cutover.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pg.env").write_text(
        "DATABASE_URL=postgresql://shopifyseo:secret@127.0.0.1:5432/shopifyseo\n",
        encoding="utf-8",
    )
    proc = _run(["bash", str(START_APP), "--decide-only"], env=env)
    assert proc.returncode != 0
    assert proc.stdout.strip() == "error"
    assert "not falling back to SQLite" in proc.stderr
    assert proc.stdout.strip() != "sqlite"
    assert "secret" not in proc.stdout
    assert "secret" not in proc.stderr


def test_start_app_stale_cutover_mark_is_sqlite(tmp_path):
    stale = tmp_path / "tmp" / "pg-cutover-rolled-back" / "cutover_mark.json"
    stale.parent.mkdir(parents=True)
    stale.write_text('{"mode": "apply-load"}', encoding="utf-8")
    env = _base_env(tmp_path)
    proc = _run(["bash", str(START_APP), "--decide-only"], env=env)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "sqlite"


def test_start_app_live_mark_missing_database_url_is_error(tmp_path):
    env = _base_env(tmp_path)
    (tmp_path / "pg_live_cutover.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pg.env").write_text("PGHOST=127.0.0.1\n", encoding="utf-8")
    proc = _run(["bash", str(START_APP), "--decide-only"], env=env)
    assert proc.returncode != 0
    assert proc.stdout.strip() == "error"
    assert "DATABASE_URL is missing" in proc.stderr


@pytest.mark.skipif(not _IS_PG, reason="TEST_DATABASE_URL is not Postgres")
def test_start_app_live_mark_real_pg_is_postgres(tmp_path):
    env = _base_env(tmp_path)
    (tmp_path / "pg_live_cutover.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pg.env").write_text(f"DATABASE_URL={_PG_URL}\n", encoding="utf-8")
    proc = _run(["bash", str(START_APP), "--decide-only"], env=env)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "postgres"
    assert _PG_URL not in proc.stdout
    assert _PG_URL not in proc.stderr


def test_backup_noop_without_live_mark(tmp_path):
    env = _base_env(tmp_path)
    backup_dir = tmp_path / "backups"
    proc = _run(["bash", str(BACKUP_SH)], env=env)
    assert proc.returncode == 0, proc.stderr
    assert not backup_dir.exists() or not any(backup_dir.iterdir())


def test_backup_retains_newest_seven(tmp_path):
    shims = tmp_path / "bin"
    _write_shim(
        shims,
        "pg_dump",
        """
outfile=""
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "-f" ]]; then
    outfile="$2"
    shift 2
  else
    shift
  fi
done
printf 'fake-dump\\n' > "$outfile"
""",
    )
    env = _base_env(tmp_path, path_prefix=shims)
    (tmp_path / "pg_live_cutover.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pg.env").write_text(
        "DATABASE_URL=postgresql://shopifyseo:secret@127.0.0.1:5432/shopifyseo\n",
        encoding="utf-8",
    )
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    for i in range(10):
        dump = backup_dir / f"shopifyseo-2026010{i % 10}T00000{i}.dump"
        dump.write_text(f"old-{i}\n", encoding="utf-8")
        os.utime(dump, (i, i))
        time.sleep(0.01)
    proc = _run(["bash", str(BACKUP_SH)], env=env)
    assert proc.returncode == 0, proc.stderr
    remaining = sorted(backup_dir.glob("shopifyseo-*.dump"))
    assert len(remaining) == 7
    assert "secret" not in proc.stdout
    assert "secret" not in proc.stderr
    assert "DATABASE_URL=" not in proc.stdout
    assert "DATABASE_URL=" not in proc.stderr


def test_cron_installer_is_idempotent(tmp_path):
    crontab_file = tmp_path / "crontab.txt"
    shims = tmp_path / "bin"
    _write_shim(
        shims,
        "crontab",
        f"""
file={crontab_file}
if [[ "${{1:-}}" == "-l" ]]; then
  if [[ -f "$file" ]]; then
    cat "$file"
    exit 0
  fi
  echo "no crontab" >&2
  exit 1
fi
cat > "$file"
""",
    )
    env = _base_env(tmp_path, path_prefix=shims)
    env["SHOPIFYSEO_PG_BACKUP_SH"] = str(BACKUP_SH)
    first = _run(["bash", str(CRON_SH)], env=env)
    assert first.returncode == 0, first.stderr
    text1 = crontab_file.read_text(encoding="utf-8")
    assert str(BACKUP_SH) in text1
    assert text1.count(str(BACKUP_SH)) == 1
    second = _run(["bash", str(CRON_SH)], env=env)
    assert second.returncode == 0, second.stderr
    text2 = crontab_file.read_text(encoding="utf-8")
    assert text2.count(str(BACKUP_SH)) == 1
    assert text2.count("shopifyseo-pg-nightly-backup") == 1


def test_ensure_postgres_refuses_to_init_nonempty_datadir(tmp_path):
    data = tmp_path / "pgdata"
    data.mkdir()
    (data / "stale.txt").write_text("not a cluster\n", encoding="utf-8")
    called = tmp_path / "initdb.called"
    shims = tmp_path / "bin"
    _write_shim(shims, "initdb", f"echo INITDB >> {called}\nexit 0\n")
    _write_shim(shims, "pg_ctl", "exit 0\n")
    env = _base_env(tmp_path, path_prefix=shims, extra={"PG_BINDIR": str(shims)})
    proc = _run(
        [
            "bash",
            str(ENSURE_SH),
            "--pgdata",
            str(data),
            "--port",
            "55432",
            "--no-install",
        ],
        env=env,
    )
    assert proc.returncode != 0
    assert "Refusing to initdb" in proc.stderr
    assert not called.exists()


def test_ensure_postgres_help_mentions_var_lib_migration():
    proc = _run(["bash", str(ENSURE_SH), "--help"])
    assert proc.returncode == 0
    assert "/var/lib/postgresql" in proc.stdout
    assert "--bootstrap" in proc.stdout
    assert "never" in proc.stdout.lower() or "Never" in proc.stdout


def test_mark_pg_live_remove(tmp_path):
    env = _base_env(tmp_path)
    mark = tmp_path / "pg_live_cutover.json"
    mark.write_text("{}", encoding="utf-8")
    proc = _run(["bash", str(MARK_SH), "--remove"], env=env)
    assert proc.returncode == 0, proc.stderr
    assert not mark.exists()


def test_docs_cover_snapshot_live_mark_and_rollback():
    text = DOCS.read_text(encoding="utf-8")
    assert "--sqlite" in text
    assert "sqlite3" in text and ".backup" in text
    assert "wal_checkpoint" in text
    assert "start-app.sh" in text
    assert "pg_live_cutover.json" in text
    assert "mark-pg-live.sh" in text
    assert "--remove" in text
    assert "pg-nightly-backup.sh" in text
    assert "install-pg-backup-cron.sh" in text
    assert "ensure-postgres.sh" in text
    assert "restore-tailscale.sh" in text
    assert "cutover_mark.json" in text
    assert "Never commit" in text or "never commit" in text.lower()


def test_like_ilike_translation_cases():
    assert (
        _translate_placeholders("SELECT * FROM t WHERE name LIKE 'a%'", to_postgres=True)
        == "SELECT * FROM t WHERE name ILIKE 'a%'"
    )
    assert (
        _translate_placeholders("SELECT * FROM t WHERE name NOT LIKE 'a%'", to_postgres=True)
        == "SELECT * FROM t WHERE name NOT ILIKE 'a%'"
    )
    sqlite_sql = "SELECT likes, unlike_count FROM t WHERE name LIKE 'x' AND note = 'looks LIKE this'"
    assert _translate_placeholders(sqlite_sql, to_postgres=False) == sqlite_sql
    assert (
        _translate_placeholders(sqlite_sql, to_postgres=True)
        == "SELECT likes, unlike_count FROM t WHERE name ILIKE 'x' AND note = 'looks LIKE this'"
    )
    already = "SELECT * FROM t WHERE name ILIKE 'a%'"
    assert _translate_placeholders(already, to_postgres=True) == already
