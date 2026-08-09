from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from backend.app.database import Database


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "db.sqlite3")
    database.migrate()
    database.migrate()

    assert database.schema_version() == 9
    assert database.check() is True
    with database.reader() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_single_writer_serializes_writes(tmp_path: Path) -> None:
    database = Database(tmp_path / "db.sqlite3")
    database.migrate()

    def write(index: int) -> None:
        with database.writer() as connection:
            connection.execute(
                """
                INSERT INTO projects(project_id, title, workspace_path)
                VALUES (?, ?, ?)
                """,
                (f"project-{index}", f"Project {index}", f"/tmp/project-{index}"),
            )

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(write, range(20)))

    with database.reader() as connection:
        count = connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    assert count == 20
