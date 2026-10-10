"""Durable Postgres box runtime: start-app, backups, cron, ensure-postgres."""

from __future__ import annotations

import os
import shutil
import socket
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
DAEMON_SH = ROOT / "scripts" / "pg-backup-daemon.sh"
LISTEN_SH = ROOT / "scripts" / "lib" / "pg-listen-port.sh"
PGLOADER_4G = ROOT / "scripts" / "pg_cutover" / "pgloader-4g.sh"
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


def _pg17_bindir() -> Path | None:
    candidates = [
        Path("/usr/lib/postgresql/17/bin"),
        Path("/usr/pgsql-17/bin"),
        Path("/usr/local/pgsql/bin"),
    ]
    from_path = os.environ.get("PG_BINDIR", "")
    if from_path:
        candidates.insert(0, Path(from_path))
    for directory in candidates:
        if (directory / "pg_ctl").is_file() and (directory / "initdb").is_file():
            return directory
    return None


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def _base_env(tmp: Path, *, path_prefix: Path | None = None, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["SHOPIFYSEO_PG_LIVE_MARK"] = str(tmp / "pg_live_cutover.json")
    env["SHOPIFYSEO_PG_ENV"] = str(tmp / "pg.env")
    env["SHOPIFYSEO_UVICORN_LOG"] = str(tmp / "logs" / "uvicorn.log")
    env["SHOPIFYSEO_UVICORN_PORT"] = "18000"
    env["SHOPIFYSEO_SKIP_ENSURE_POSTGRES"] = "1"
    env["SHOPIFYSEO_SKIP_BACKUP_CRON"] = "1"
    env["SHOPIFYSEO_SKIP_BACKUP_DAEMON"] = "1"
    env["SHOPIFYSEO_PG_BACKUP_DIR"] = str(tmp / "backups")
    env["SHOPIFYSEO_LISTEN_PORT_FILE"] = str(tmp / "listen_port")
    env["SHOPIFYSEO_PG_BACKUP_DAEMON_SH"] = str(DAEMON_SH)
    env["SHOPIFYSEO_PG_BACKUP_LOG"] = str(tmp / "logs" / "pg-backup-daemon.log")
    env["SHOPIFYSEO_PG_BACKUP_PIDFILE"] = str(tmp / "logs" / "pg-backup-daemon.pid")
    env["SHOPIFYSEO_ENSURE_POSTGRES_NO_INSTALL"] = "1"
    if path_prefix is not None:
        env["PATH"] = f"{path_prefix}{os.pathsep}{env.get('PATH', '')}"
    if extra:
        env.update(extra)
    return env


def test_runtime_scripts_exist_executable_and_have_valid_syntax():
    for path in (ENSURE_SH, START_APP, MARK_SH, BACKUP_SH, CRON_SH, DAEMON_SH, PGLOADER_4G):
        assert path.is_file(), path
        assert os.access(path, os.X_OK), f"{path} should be executable"
        proc = _run(["bash", "-n", str(path)])
        assert proc.returncode == 0, proc.stderr
    assert LISTEN_SH.is_file()
    proc = _run(["bash", "-n", str(LISTEN_SH)])
    assert proc.returncode == 0, proc.stderr


def test_runtime_scripts_help():
    for path in (ENSURE_SH, START_APP, MARK_SH, BACKUP_SH, CRON_SH, DAEMON_SH):
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


def test_nightly_backup_removes_partials_older_than_one_day(tmp_path):
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
    stale = backup_dir / "shopifyseo-20200101T000000Z.dump.partial"
    stale.write_text("old\n", encoding="utf-8")
    os.utime(stale, (0, 0))
    fresh = backup_dir / "shopifyseo-fresh.dump.partial"
    fresh.write_text("new\n", encoding="utf-8")
    proc = _run(["bash", str(BACKUP_SH)], env=env)
    assert proc.returncode == 0, proc.stderr
    assert not stale.exists()
    assert fresh.exists()


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
    assert "--socket-dir" in proc.stdout
    assert "create_main_cluster" in proc.stdout
    assert "5433" in proc.stdout
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
    assert "setsid nohup /home/box/workspace/shopifyseo/scripts/start-app.sh" in text
    assert "cp -a /var/lib/postgresql/17/main/." in text
    assert "chown -R box:box" in text
    assert "pgrep -x cron" in text
    assert "cron daemon" in text.lower() or "no cron" in text.lower()
    assert "pg-backup-daemon.sh" in text
    assert "/home/box/pgdata/17/run" in text
    assert "pg_ctlcluster 17 main stop" in text
    assert "shopifyseo_cutover_dryrun" in text
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


def test_ensure_postgres_busy_unidentified_port_falls_back_and_inits(tmp_path):
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    port = int(holder.getsockname()[1])
    holder.listen(1)
    try:
        data = tmp_path / "pgdata"
        data.mkdir()
        called = tmp_path / "initdb.called"
        stop_called = tmp_path / "pg_ctlcluster.called"
        listen = tmp_path / "listen_port"
        shims = tmp_path / "bin"
        _write_shim(shims, "initdb", f"echo INITDB >> {called}\nexit 0\n")
        _write_shim(
            shims,
            "pg_ctl",
            """
if [[ " $* " == *" status "* ]]; then exit 1; fi
exit 0
""",
        )
        _write_shim(shims, "pg_ctlcluster", f"echo STOP >> {stop_called}\nexit 0\n")
        env = _base_env(
            tmp_path,
            path_prefix=shims,
            extra={
                "PG_BINDIR": str(shims),
                "SHOPIFYSEO_DEBIAN_PGDATA": str(tmp_path / "debian-missing"),
                "SHOPIFYSEO_LISTEN_PORT_FILE": str(listen),
            },
        )
        proc = _run(
            [
                "bash",
                str(ENSURE_SH),
                "--pgdata",
                str(data),
                "--port",
                str(port),
                "--socket-dir",
                str(tmp_path / "run"),
                "--no-install",
            ],
            env=env,
        )
        assert proc.returncode == 0, proc.stderr + proc.stdout
        assert called.exists()
        assert not stop_called.exists()
        chosen = int(listen.read_text(encoding="utf-8").strip())
        assert chosen != port
        assert "not positively Debian 17/main" in proc.stderr
    finally:
        holder.close()


def test_backup_failed_dump_leaves_no_partial_or_new_dump(tmp_path):
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
if [[ -n "$outfile" ]]; then
  printf 'junk\\n' > "$outfile"
fi
exit 1
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
    existing = backup_dir / "shopifyseo-20260101T000000Z.dump"
    existing.write_text("good-dump\n", encoding="utf-8")
    proc = _run(["bash", str(BACKUP_SH)], env=env)
    assert proc.returncode != 0
    assert existing.read_text(encoding="utf-8") == "good-dump\n"
    assert list(backup_dir.glob("shopifyseo-*.dump")) == [existing]
    assert list(backup_dir.glob("*.partial")) == []
    assert "secret" not in proc.stdout
    assert "secret" not in proc.stderr


def test_start_app_no_mark_survives_ensure_failure(tmp_path):
    launched = tmp_path / "uvicorn.launched"
    ensure_shim = tmp_path / "ensure-fail.sh"
    ensure_shim.write_text("#!/bin/bash\necho ensure-failed >&2\nexit 1\n", encoding="utf-8")
    _chmod_exec(ensure_shim)
    python_shim = tmp_path / "fake-python"
    python_shim.write_text(
        f"#!/bin/bash\nprintf '%s\\n' \"$0 $*\" > {launched}\nexit 0\n",
        encoding="utf-8",
    )
    _chmod_exec(python_shim)
    pgdata = tmp_path / "existing-pgdata"
    pgdata.mkdir()
    (pgdata / "PG_VERSION").write_text("17\n", encoding="utf-8")
    env = _base_env(tmp_path)
    env["SHOPIFYSEO_SKIP_ENSURE_POSTGRES"] = "0"
    env["SHOPIFYSEO_ENSURE_POSTGRES_SH"] = str(ensure_shim)
    env["SHOPIFYSEO_PYTHON"] = str(python_shim)
    env["PGDATA_DIR"] = str(pgdata)
    env["SHOPIFYSEO_UVICORN_PORT"] = str(_free_port())
    proc = _run(["bash", str(START_APP)], env=env)
    assert proc.returncode == 0, proc.stderr
    deadline = time.time() + 2
    while not launched.is_file() and time.time() < deadline:
        time.sleep(0.05)
    assert launched.is_file(), proc.stdout + proc.stderr
    assert "uvicorn" in launched.read_text(encoding="utf-8")
    assert "started uvicorn" in proc.stdout
    assert "continuing on SQLite" in proc.stderr


def test_start_app_mark_plus_ensure_failure_is_fatal(tmp_path):
    launched = tmp_path / "uvicorn.launched"
    ensure_shim = tmp_path / "ensure-fail.sh"
    ensure_shim.write_text("#!/bin/bash\necho ensure-failed >&2\nexit 1\n", encoding="utf-8")
    _chmod_exec(ensure_shim)
    python_shim = tmp_path / "fake-python"
    python_shim.write_text(
        f"#!/bin/bash\nprintf '%s\\n' \"$0 $*\" > {launched}\nexit 0\n",
        encoding="utf-8",
    )
    _chmod_exec(python_shim)
    env = _base_env(tmp_path)
    env["SHOPIFYSEO_SKIP_ENSURE_POSTGRES"] = "0"
    env["SHOPIFYSEO_ENSURE_POSTGRES_SH"] = str(ensure_shim)
    env["SHOPIFYSEO_PYTHON"] = str(python_shim)
    env["SHOPIFYSEO_UVICORN_PORT"] = str(_free_port())
    (tmp_path / "pg_live_cutover.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pg.env").write_text(
        "DATABASE_URL=postgresql://shopifyseo:secret@127.0.0.1:5432/shopifyseo\n",
        encoding="utf-8",
    )
    proc = _run(["bash", str(START_APP)], env=env)
    assert proc.returncode != 0
    assert not launched.exists()


@pytest.mark.skipif(_pg17_bindir() is None, reason="PostgreSQL 17 binaries not installed")
def test_ensure_postgres_starts_twice_then_stops(tmp_path):
    bindir = _pg17_bindir()
    assert bindir is not None
    port = _free_port()
    data = tmp_path / "pgdata"
    sock = tmp_path / "run"
    env = _base_env(tmp_path, extra={"PG_BINDIR": str(bindir), "PGSOCKET_DIR": str(sock)})
    env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
    args = [
        "bash",
        str(ENSURE_SH),
        "--pgdata",
        str(data),
        "--port",
        str(port),
        "--socket-dir",
        str(sock),
        "--no-install",
    ]
    try:
        first = _run(args, env=env)
        assert first.returncode == 0, first.stderr + first.stdout
        combined = first.stdout + first.stderr
        assert "already running" not in combined
        assert str(sock) in combined
        second = _run(args, env=env)
        assert second.returncode == 0, second.stderr + second.stdout
        assert "already running" in second.stdout + second.stderr
    finally:
        _run([str(bindir / "pg_ctl"), "-D", str(data), "-m", "fast", "stop"], env=env)


@pytest.mark.skipif(_pg17_bindir() is None, reason="PostgreSQL 17 binaries not installed")
def test_ensure_postgres_bootstrap_twice_leaves_password(tmp_path):
    bindir = _pg17_bindir()
    assert bindir is not None
    port = _free_port()
    data = tmp_path / "pgdata"
    sock = tmp_path / "run"
    env = _base_env(tmp_path, extra={"PG_BINDIR": str(bindir), "PGSOCKET_DIR": str(sock)})
    env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
    env["PGPASSWORD"] = "first-secret"
    env["SHOPIFYSEO_PG_ENV"] = str(tmp_path / "pg.env")
    (tmp_path / "pg.env").write_text(
        f"DATABASE_URL=postgresql://shopifyseo:first-secret@127.0.0.1:{port}/shopifyseo\n",
        encoding="utf-8",
    )
    args = [
        "bash",
        str(ENSURE_SH),
        "--pgdata",
        str(data),
        "--port",
        str(port),
        "--socket-dir",
        str(sock),
        "--no-install",
        "--bootstrap",
    ]
    psql = str(bindir / "psql")
    try:
        first = _run(args, env=env)
        assert first.returncode == 0, first.stderr + first.stdout
        assert "first-secret" not in first.stdout
        assert "first-secret" not in first.stderr
        check = _run(
            [psql, "-h", "127.0.0.1", "-p", str(port), "-U", "shopifyseo", "-d", "shopifyseo", "-w", "-c", "SELECT 1"],
            env=env,
        )
        assert check.returncode == 0, check.stderr
        env_second = env.copy()
        env_second["PGPASSWORD"] = "second-secret"
        second = _run(args, env=env_second)
        assert second.returncode == 0, second.stderr + second.stdout
        assert "second-secret" not in second.stdout + second.stderr
        still_first = _run(
            [psql, "-h", "127.0.0.1", "-p", str(port), "-U", "shopifyseo", "-d", "shopifyseo", "-w", "-c", "SELECT 1"],
            env=env,
        )
        assert still_first.returncode == 0, still_first.stderr
        changed = _run(
            [psql, "-h", "127.0.0.1", "-p", str(port), "-U", "shopifyseo", "-d", "shopifyseo", "-w", "-c", "SELECT 1"],
            env=env_second,
        )
        assert changed.returncode != 0
    finally:
        _run([str(bindir / "pg_ctl"), "-D", str(data), "-m", "fast", "stop"], env=env)


def _write_debian_pidfile(directory: Path, port: int, pid: int) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "postmaster.pid").write_text(
        f"{pid}\n{directory}\n{int(time.time())}\n{port}\n/tmp\n",
        encoding="utf-8",
    )


def _spawn_named(tmp_path: Path, name: str) -> subprocess.Popen:
    # Copy a real binary so /proc/<pid>/comm is `name` (a bash wrapper becomes
    # "bash" or "sleep" after exec).
    exe = tmp_path / name
    shutil.copy("/bin/sleep", exe)
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return subprocess.Popen([str(exe), "300"], start_new_session=True)


def _ensure_busy_port_args(tmp_path: Path, data: Path, port: int) -> list[str]:
    return [
        "bash",
        str(ENSURE_SH),
        "--pgdata",
        str(data),
        "--port",
        str(port),
        "--socket-dir",
        str(tmp_path / "run"),
        "--no-install",
    ]


def test_ensure_postgres_stops_only_identified_debian_cluster(tmp_path):
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    port = int(holder.getsockname()[1])
    holder.listen(1)
    fake_pg = _spawn_named(tmp_path, "postgres")
    try:
        debian = tmp_path / "debian" / "17" / "main"
        _write_debian_pidfile(debian, port, fake_pg.pid)
        data = tmp_path / "pgdata"
        data.mkdir()
        stop_called = tmp_path / "pg_ctlcluster.called"
        listen = tmp_path / "listen_port"
        shims = tmp_path / "bin"
        _write_shim(shims, "initdb", "exit 0\n")
        _write_shim(
            shims,
            "pg_ctl",
            """
if [[ " $* " == *" status "* ]]; then exit 1; fi
exit 0
""",
        )
        _write_shim(shims, "pg_ctlcluster", f"echo STOP-17-main >> {stop_called}\nexit 0\n")
        env = _base_env(
            tmp_path,
            path_prefix=shims,
            extra={
                "PG_BINDIR": str(shims),
                "SHOPIFYSEO_DEBIAN_PGDATA": str(debian),
                "SHOPIFYSEO_LISTEN_PORT_FILE": str(listen),
            },
        )
        proc = _run(_ensure_busy_port_args(tmp_path, data, port), env=env)
        assert proc.returncode == 0, proc.stderr + proc.stdout
        assert stop_called.exists(), proc.stdout + proc.stderr
        assert stop_called.read_text(encoding="utf-8").strip() == "STOP-17-main"
        assert "Debian 17/main" in proc.stderr
        chosen = int(listen.read_text(encoding="utf-8").strip())
        assert chosen != port
    finally:
        fake_pg.terminate()
        fake_pg.wait(timeout=3)
        holder.close()


def test_ensure_postgres_does_not_stop_unidentified_postgres(tmp_path):
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    port = int(holder.getsockname()[1])
    holder.listen(1)
    try:
        debian = tmp_path / "debian" / "17" / "main"
        # Port in the pidfile does not match the busy port → not identified.
        _write_debian_pidfile(debian, port + 99, os.getpid())
        stop_called = tmp_path / "pg_ctlcluster.called"
        shims = tmp_path / "bin"
        _write_shim(shims, "initdb", "exit 0\n")
        _write_shim(
            shims,
            "pg_ctl",
            """
if [[ " $* " == *" status "* ]]; then exit 1; fi
exit 0
""",
        )
        _write_shim(shims, "pg_ctlcluster", f"echo STOP >> {stop_called}\nexit 0\n")
        env = _base_env(
            tmp_path,
            path_prefix=shims,
            extra={
                "PG_BINDIR": str(shims),
                "SHOPIFYSEO_DEBIAN_PGDATA": str(debian),
            },
        )
        data = tmp_path / "pgdata"
        data.mkdir()
        proc = _run(
            [
                "bash",
                str(ENSURE_SH),
                "--pgdata",
                str(data),
                "--port",
                str(port),
                "--socket-dir",
                str(tmp_path / "run"),
                "--no-install",
            ],
            env=env,
        )
        assert proc.returncode == 0, proc.stderr + proc.stdout
        assert not stop_called.exists()
        assert "not positively Debian 17/main" in proc.stderr
    finally:
        holder.close()


def test_ensure_postgres_ignores_debian_pidfile_when_pid_is_not_postgres(tmp_path):
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    port = int(holder.getsockname()[1])
    holder.listen(1)
    try:
        debian = tmp_path / "debian" / "17" / "main"
        # Live pid, matching data dir and port, but comm is pytest — not postgres.
        _write_debian_pidfile(debian, port, os.getpid())
        stop_called = tmp_path / "pg_ctlcluster.called"
        shims = tmp_path / "bin"
        _write_shim(shims, "initdb", "exit 0\n")
        _write_shim(
            shims,
            "pg_ctl",
            """
if [[ " $* " == *" status "* ]]; then exit 1; fi
exit 0
""",
        )
        _write_shim(shims, "pg_ctlcluster", f"echo STOP >> {stop_called}\nexit 0\n")
        env = _base_env(
            tmp_path,
            path_prefix=shims,
            extra={
                "PG_BINDIR": str(shims),
                "SHOPIFYSEO_DEBIAN_PGDATA": str(debian),
            },
        )
        data = tmp_path / "pgdata"
        data.mkdir()
        proc = _run(_ensure_busy_port_args(tmp_path, data, port), env=env)
        assert proc.returncode == 0, proc.stderr + proc.stdout
        assert not stop_called.exists()
        assert "not positively Debian 17/main" in proc.stderr
    finally:
        holder.close()


def test_ensure_postgres_ignores_pg_lsclusters_when_status_down(tmp_path):
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    port = int(holder.getsockname()[1])
    holder.listen(1)
    try:
        debian = tmp_path / "debian" / "17" / "main"
        debian.mkdir(parents=True)
        stop_called = tmp_path / "pg_ctlcluster.called"
        shims = tmp_path / "bin"
        _write_shim(shims, "initdb", "exit 0\n")
        _write_shim(
            shims,
            "pg_ctl",
            """
if [[ " $* " == *" status "* ]]; then exit 1; fi
exit 0
""",
        )
        _write_shim(shims, "pg_ctlcluster", f"echo STOP >> {stop_called}\nexit 0\n")
        _write_shim(
            shims,
            "pg_lsclusters",
            f"""
if [[ "$*" == *"--no-header"* ]]; then
  echo "17 main {port} down postgres {debian} /tmp/log"
fi
exit 0
""",
        )
        env = _base_env(
            tmp_path,
            path_prefix=shims,
            extra={
                "PG_BINDIR": str(shims),
                "SHOPIFYSEO_DEBIAN_PGDATA": str(debian),
            },
        )
        data = tmp_path / "pgdata"
        data.mkdir()
        proc = _run(_ensure_busy_port_args(tmp_path, data, port), env=env)
        assert proc.returncode == 0, proc.stderr + proc.stdout
        assert not stop_called.exists(), proc.stderr
        assert "not positively Debian 17/main" in proc.stderr
    finally:
        holder.close()


def test_ensure_postgres_stops_debian_when_pg_lsclusters_online(tmp_path):
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    port = int(holder.getsockname()[1])
    holder.listen(1)
    try:
        debian = tmp_path / "debian" / "17" / "main"
        debian.mkdir(parents=True)
        stop_called = tmp_path / "pg_ctlcluster.called"
        listen = tmp_path / "listen_port"
        shims = tmp_path / "bin"
        _write_shim(shims, "initdb", "exit 0\n")
        _write_shim(
            shims,
            "pg_ctl",
            """
if [[ " $* " == *" status "* ]]; then exit 1; fi
exit 0
""",
        )
        _write_shim(shims, "pg_ctlcluster", f"echo STOP-17-main >> {stop_called}\nexit 0\n")
        _write_shim(
            shims,
            "pg_lsclusters",
            f"""
if [[ "$*" == *"--no-header"* ]]; then
  echo "17 main {port} online postgres {debian} /tmp/log"
fi
exit 0
""",
        )
        env = _base_env(
            tmp_path,
            path_prefix=shims,
            extra={
                "PG_BINDIR": str(shims),
                "SHOPIFYSEO_DEBIAN_PGDATA": str(debian),
                "SHOPIFYSEO_LISTEN_PORT_FILE": str(listen),
            },
        )
        data = tmp_path / "pgdata"
        data.mkdir()
        proc = _run(_ensure_busy_port_args(tmp_path, data, port), env=env)
        assert proc.returncode == 0, proc.stderr + proc.stdout
        assert stop_called.exists(), proc.stderr + proc.stdout
        assert stop_called.read_text(encoding="utf-8").strip() == "STOP-17-main"
    finally:
        holder.close()


def test_ensure_postgres_pg_lsclusters_requires_exact_data_dir(tmp_path):
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    port = int(holder.getsockname()[1])
    holder.listen(1)
    try:
        debian = tmp_path / "debian" / "17" / "main"
        debian.mkdir(parents=True)
        stop_called = tmp_path / "pg_ctlcluster.called"
        shims = tmp_path / "bin"
        _write_shim(shims, "initdb", "exit 0\n")
        _write_shim(
            shims,
            "pg_ctl",
            """
if [[ " $* " == *" status "* ]]; then exit 1; fi
exit 0
""",
        )
        _write_shim(shims, "pg_ctlcluster", f"echo STOP >> {stop_called}\nexit 0\n")
        _write_shim(
            shims,
            "pg_lsclusters",
            f"""
if [[ "$*" == *"--no-header"* ]]; then
  echo "17 main {port} online postgres {debian}-extra /tmp/log"
fi
exit 0
""",
        )
        env = _base_env(
            tmp_path,
            path_prefix=shims,
            extra={
                "PG_BINDIR": str(shims),
                "SHOPIFYSEO_DEBIAN_PGDATA": str(debian),
            },
        )
        data = tmp_path / "pgdata"
        data.mkdir()
        proc = _run(_ensure_busy_port_args(tmp_path, data, port), env=env)
        assert proc.returncode == 0, proc.stderr + proc.stdout
        assert not stop_called.exists()
    finally:
        holder.close()


def test_start_app_honors_listen_port_file(tmp_path):
    shims = tmp_path / "bin"
    ready_args = tmp_path / "pg_isready.args"
    _write_shim(shims, "pg_isready", f'printf "%s\\n" "$*" > {ready_args}\nexit 0\n')
    env = _base_env(tmp_path, path_prefix=shims)
    (tmp_path / "pg_live_cutover.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pg.env").write_text(
        "DATABASE_URL=postgresql://shopifyseo:secret@127.0.0.1:5432/shopifyseo\n",
        encoding="utf-8",
    )
    (tmp_path / "listen_port").write_text("55433\n", encoding="utf-8")
    proc = _run(["bash", str(START_APP), "--decide-only"], env=env)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "postgres"
    assert "secret" not in proc.stdout
    assert "secret" not in proc.stderr
    logged = ready_args.read_text(encoding="utf-8")
    assert "-p 55433" in logged
    assert "-p 5432" not in logged


def _apply_listen_port(tmp_path: Path, url: str, port: str) -> str:
    (tmp_path / "listen_port").write_text(f"{port}\n", encoding="utf-8")
    script = tmp_path / "probe.sh"
    script.write_text(
        f"""#!/bin/bash
set -euo pipefail
source {LISTEN_SH}
export DATABASE_URL={url!r}
export SHOPIFYSEO_LISTEN_PORT_FILE={tmp_path / "listen_port"}
apply_listen_port_to_env
printf '%s\\n' "${{PGPORT:-unset}}"
""",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    proc = _run(["bash", str(script)])
    assert proc.returncode == 0, proc.stderr
    assert "secret" not in proc.stdout
    return proc.stdout.strip()


def test_listen_port_rewrites_loopback_only(tmp_path):
    assert (
        _apply_listen_port(
            tmp_path,
            "postgresql://shopifyseo:secret@127.0.0.1:5499/shopifyseo",
            "5461",
        )
        == "5461"
    )
    assert (
        _apply_listen_port(
            tmp_path,
            "postgresql://shopifyseo:secret@/shopifyseo?host=/tmp/pg",
            "5461",
        )
        == "unset"
    )
    assert (
        _apply_listen_port(
            tmp_path,
            "postgresql://shopifyseo:secret@10.1.2.3:5432/shopifyseo",
            "5461",
        )
        == "unset"
    )


def test_nightly_backup_honors_listen_port(tmp_path):
    shims = tmp_path / "bin"
    port_log = tmp_path / "pg_dump.port"
    _write_shim(
        shims,
        "pg_dump",
        f"""
printf 'PGPORT=%s\\n' "${{PGPORT:-}}" > {port_log}
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
        "DATABASE_URL=postgresql://shopifyseo:secret@127.0.0.1:5499/shopifyseo\n",
        encoding="utf-8",
    )
    (tmp_path / "listen_port").write_text("5461\n", encoding="utf-8")
    proc = _run(["bash", str(BACKUP_SH)], env=env)
    assert proc.returncode == 0, proc.stderr
    assert port_log.read_text(encoding="utf-8").strip() == "PGPORT=5461"
    assert "secret" not in proc.stdout + proc.stderr


def test_backup_daemon_noop_without_live_mark(tmp_path):
    env = _base_env(tmp_path)
    proc = _run(["bash", str(DAEMON_SH), "--ensure"], env=env)
    assert proc.returncode == 0, proc.stderr
    assert not (tmp_path / "backups").exists() or not any((tmp_path / "backups").iterdir())
    assert not (tmp_path / "logs" / "pg-backup-daemon.pid").exists()


def test_backup_daemon_runs_stale_dump_and_skips_fresh(tmp_path):
    shims = tmp_path / "bin"
    dump_log = tmp_path / "pg_dump.calls"
    _write_shim(
        shims,
        "pg_dump",
        f"""
outfile=""
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "-f" ]]; then
    outfile="$2"
    shift 2
  else
    shift
  fi
done
echo called >> {dump_log}
printf 'fake-dump\\n' > "$outfile"
""",
    )
    env = _base_env(tmp_path, path_prefix=shims)
    env["SHOPIFYSEO_PG_BACKUP_MAX_AGE_SECONDS"] = "86400"
    env["SHOPIFYSEO_SKIP_BACKUP_DAEMON"] = "0"
    (tmp_path / "pg_live_cutover.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pg.env").write_text(
        "DATABASE_URL=postgresql://shopifyseo:secret@127.0.0.1:5432/shopifyseo\n",
        encoding="utf-8",
    )
    pidfile = tmp_path / "logs" / "pg-backup-daemon.pid"
    try:
        first = _run(["bash", str(DAEMON_SH), "--ensure"], env=env)
        assert first.returncode == 0, first.stderr
        assert dump_log.is_file()
        assert dump_log.read_text(encoding="utf-8").count("called") == 1
        dumps = list((tmp_path / "backups").glob("shopifyseo-*.dump"))
        assert len(dumps) == 1
        second = _run(["bash", str(DAEMON_SH), "--ensure"], env=env)
        assert second.returncode == 0, second.stderr
        assert dump_log.read_text(encoding="utf-8").count("called") == 1
        assert "secret" not in first.stdout + first.stderr + second.stdout + second.stderr
    finally:
        if pidfile.is_file():
            raw = pidfile.read_text(encoding="utf-8").strip()
            if raw.isdigit():
                try:
                    os.kill(int(raw), 15)
                except OSError:
                    pass


def test_backup_daemon_pidfile_prevents_second_loop(tmp_path):
    env = _base_env(tmp_path)
    env["SHOPIFYSEO_PG_BACKUP_LOOP_SECONDS"] = "60"
    (tmp_path / "pg_live_cutover.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pg.env").write_text(
        "DATABASE_URL=postgresql://shopifyseo:secret@127.0.0.1:5432/shopifyseo\n",
        encoding="utf-8",
    )
    first = subprocess.Popen(
        ["bash", str(DAEMON_SH), "--loop"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    pidfile = tmp_path / "logs" / "pg-backup-daemon.pid"
    deadline = time.time() + 3
    while time.time() < deadline and not pidfile.is_file():
        time.sleep(0.05)
    try:
        assert pidfile.is_file(), first.stderr.read() if first.stderr else "no pidfile"
        owner = pidfile.read_text(encoding="utf-8").strip()
        assert owner == str(first.pid)
        second = _run(["bash", str(DAEMON_SH), "--loop"], env=env)
        assert second.returncode == 0, second.stderr
        assert pidfile.read_text(encoding="utf-8").strip() == owner
        assert first.poll() is None
    finally:
        first.terminate()
        try:
            first.wait(timeout=3)
        except subprocess.TimeoutExpired:
            first.kill()
            first.wait(timeout=3)
    deadline = time.time() + 2
    while pidfile.is_file() and time.time() < deadline:
        time.sleep(0.05)
    assert not pidfile.exists()


def test_backup_daemon_treats_unrelated_live_pid_as_stale(tmp_path):
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
    env["SHOPIFYSEO_PG_BACKUP_LOOP_SECONDS"] = "60"
    (tmp_path / "pg_live_cutover.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pg.env").write_text(
        "DATABASE_URL=postgresql://shopifyseo:secret@127.0.0.1:5432/shopifyseo\n",
        encoding="utf-8",
    )
    sleeper = subprocess.Popen(["sleep", "300"])
    pidfile = tmp_path / "logs" / "pg-backup-daemon.pid"
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    pidfile.write_text(f"{sleeper.pid}\n", encoding="utf-8")
    try:
        proc = _run(["bash", str(DAEMON_SH), "--ensure"], env=env)
        assert proc.returncode == 0, proc.stderr
        deadline = time.time() + 3
        owner = ""
        while time.time() < deadline:
            if pidfile.is_file():
                owner = pidfile.read_text(encoding="utf-8").strip()
                if owner.isdigit() and owner != str(sleeper.pid):
                    break
            time.sleep(0.05)
        assert owner != str(sleeper.pid), proc.stderr
        assert owner.isdigit()
        assert "already running" not in proc.stderr
    finally:
        sleeper.terminate()
        try:
            sleeper.wait(timeout=2)
        except subprocess.TimeoutExpired:
            sleeper.kill()
        if pidfile.is_file():
            raw = pidfile.read_text(encoding="utf-8").strip()
            if raw.isdigit() and int(raw) != sleeper.pid:
                try:
                    os.kill(int(raw), 15)
                except OSError:
                    pass


def test_start_app_launches_backup_hook_only_for_postgres(tmp_path):
    launched = tmp_path / "daemon.launched"
    daemon = tmp_path / "fake-daemon.sh"
    daemon.write_text(f"#!/bin/bash\necho ensure >> {launched}\nexit 0\n", encoding="utf-8")
    _chmod_exec(daemon)
    python_shim = tmp_path / "fake-python"
    python_shim.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    _chmod_exec(python_shim)
    shims = tmp_path / "bin"
    _write_shim(shims, "pg_isready", "exit 0\n")
    env = _base_env(tmp_path, path_prefix=shims)
    env["SHOPIFYSEO_SKIP_BACKUP_DAEMON"] = "0"
    env["SHOPIFYSEO_PG_BACKUP_DAEMON_SH"] = str(daemon)
    env["SHOPIFYSEO_PYTHON"] = str(python_shim)
    env["SHOPIFYSEO_UVICORN_PORT"] = str(_free_port())
    sqlite_proc = _run(["bash", str(START_APP)], env=env)
    assert sqlite_proc.returncode == 0, sqlite_proc.stderr
    time.sleep(0.2)
    assert not launched.exists()

    (tmp_path / "pg_live_cutover.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pg.env").write_text(
        "DATABASE_URL=postgresql://shopifyseo:secret@127.0.0.1:5432/shopifyseo\n",
        encoding="utf-8",
    )
    pg_proc = _run(["bash", str(START_APP)], env=env)
    assert pg_proc.returncode == 0, pg_proc.stderr
    deadline = time.time() + 2
    while not launched.is_file() and time.time() < deadline:
        time.sleep(0.05)
    assert launched.is_file()
    assert "pg backup hook launched" in pg_proc.stderr


def test_start_app_survives_backup_daemon_failure(tmp_path):
    daemon = tmp_path / "fake-daemon.sh"
    daemon.write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
    _chmod_exec(daemon)
    python_shim = tmp_path / "fake-python"
    python_shim.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    _chmod_exec(python_shim)
    shims = tmp_path / "bin"
    _write_shim(shims, "pg_isready", "exit 0\n")
    env = _base_env(tmp_path, path_prefix=shims)
    env["SHOPIFYSEO_SKIP_BACKUP_DAEMON"] = "0"
    env["SHOPIFYSEO_PG_BACKUP_DAEMON_SH"] = str(daemon)
    env["SHOPIFYSEO_PYTHON"] = str(python_shim)
    env["SHOPIFYSEO_UVICORN_PORT"] = str(_free_port())
    (tmp_path / "pg_live_cutover.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pg.env").write_text(
        "DATABASE_URL=postgresql://shopifyseo:secret@127.0.0.1:5432/shopifyseo\n",
        encoding="utf-8",
    )
    proc = _run(["bash", str(START_APP)], env=env)
    assert proc.returncode == 0, proc.stderr
    assert "secret" not in proc.stdout
    assert "secret" not in proc.stderr
