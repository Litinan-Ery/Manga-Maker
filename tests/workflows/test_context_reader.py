from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock
from uuid import uuid4

from backend.app.bootstrap.context_reader import LegacyContextDomainReader
from backend.app.errors import ApplicationError
from backend.app.workflows.book_production.contracts import (
    ArtifactPointer,
    ReviewRecord,
    UnitReference,
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_reader_hashes_source_and_rejects_changed_text_with_same_database_metadata() -> None:
    chapter = str(uuid4())
    ingestion = Mock()
    ingestion.current_chapters.return_value = {
        "chapters": [{"chapter_id": chapter, "text_sha256": digest(b"original")}]
    }
    ingestion.chapter_text.return_value = {"text": "original"}
    reader = LegacyContextDomainReader(ingestion, Mock(), Mock())
    pointer = ArtifactPointer(kind="source", artifact_id=chapter, sha256=digest(b"original"))
    assert reader.source_matches("project", pointer)
    ingestion.chapter_text.return_value = {"text": "changed"}
    assert not reader.source_matches("project", pointer)


def test_reader_discovers_new_adoption_and_invalidates_review_after_rerender(
    tmp_path: Path,
) -> None:
    image, render = tmp_path / "image.png", tmp_path / "render.png"
    image.write_bytes(b"original image")
    render.write_bytes(b"lettered image")
    generation, page = str(uuid4()), str(uuid4())
    full = Mock()
    full.get.return_value = {
        "status": "ready",
        "image_sha256": digest(image.read_bytes()),
        "plan_sha256": "a" * 64,
        "page_id": page,
    }
    full.content_path.return_value = image
    pages = Mock()
    pages.get_current.side_effect = ApplicationError("PAGE_NOT_FOUND", "not adopted", 404)
    reader = LegacyContextDomainReader(Mock(), full, pages)
    unit = UnitReference(
        unit_id="page-1", page_number=1, generation_id=generation, plan_sha256="a" * 64
    )
    assert reader.inspect_unit("project", unit)["generated"]
    assert not reader.inspect_unit("project", unit)["lettered"]
    pages.get_current.side_effect = None
    pages.get_current.return_value = {
        "page_version_id": str(uuid4()),
        "render_sha256": digest(render.read_bytes()),
        "renderer_version": "1",
        "font_sha256": "b" * 64,
        "document": {"page_image": {"generation_id": generation}},
    }
    pages.content_path.return_value = render
    reviewed = unit.model_copy(
        update={
            "review": ReviewRecord(
                result="accepted",
                scope="panel/text",
                rule_version="page-review-1",
                plan_sha256="a" * 64,
                renderer_version="1",
                font_sha256="b" * 64,
                image_sha256=digest(image.read_bytes()),
                render_sha256=digest(render.read_bytes()),
                evidence_id=uuid4(),
            )
        }
    )
    assert reader.inspect_unit("project", reviewed)["reviewed"]
    pages.get_current.return_value["document"]["page_image"]["generation_id"] = str(uuid4())
    mismatched = reader.inspect_unit("project", reviewed)
    assert mismatched["generated"] and not mismatched["lettered"]
    assert mismatched["code"] == "PAGE_GENERATION_MISMATCH"
    pages.get_current.return_value["document"]["page_image"]["generation_id"] = generation
    stale_rule = reviewed.model_copy(
        update={"review": reviewed.review.model_copy(update={"rule_version": "old-rule"})}
    )
    assert not reader.inspect_unit("project", stale_rule)["reviewed"]
    pages.get_current.return_value["font_sha256"] = "c" * 64
    assert not reader.inspect_unit("project", reviewed)["reviewed"]
    pages.get_current.return_value["font_sha256"] = "b" * 64
    render.write_bytes(b"different lettering")
    # Disk changed without metadata: block all resume, not merely flag the review stale.
    assert reader.inspect_unit("project", reviewed)["code"] == "PAGE_RENDER_HASH_MISMATCH"
    pages.get_current.return_value["render_sha256"] = digest(render.read_bytes())
    result = reader.inspect_unit("project", reviewed)
    assert result["lettered"] and not result["reviewed"]
    assert result["review_record"] == "stale"


def test_capture_uses_unbounded_latest_per_page_port() -> None:
    chapter = str(uuid4())
    ingestion, full, pages = Mock(), Mock(), Mock()
    ingestion.current_chapters.return_value = {
        "chapters": [{"chapter_id": chapter, "text_sha256": "a" * 64}]
    }
    full.list_current_generations.return_value = [
        {
            "page_id": str(uuid4()),
            "generation_id": str(uuid4()),
            "plan_sha256": "b" * 64,
            "page_number": n,
        }
        for n in range(1, 102)
    ]
    pages.get_current.side_effect = ApplicationError("PAGE_NOT_FOUND", "not adopted", 404)
    snapshot = LegacyContextDomainReader(ingestion, full, pages).capture("project", "完整范围")
    assert len(snapshot.units) == 101
    full.list_generations.assert_not_called()


def test_latest_generation_query_keeps_older_pages_beyond_history_limit(tmp_path: Path) -> None:
    # Exercise the real read query with 101 pages and a second attempt for page 1.
    import sqlite3
    from contextlib import contextmanager

    from backend.app.fullpages.service import FullPageService

    connection = sqlite3.connect(tmp_path / "query.db")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE full_page_generations (project_id TEXT, chapter_id TEXT, "
        "page_id TEXT, generation_id TEXT, created_at TEXT)"
    )
    for page in range(101):
        connection.execute(
            "INSERT INTO full_page_generations VALUES ('p', 'c', ?, ?, '2026-01-01')",
            (str(page), f"first-{page}"),
        )
    connection.execute(
        "INSERT INTO full_page_generations VALUES ('p', 'c', '0', 'second', '2026-01-01')"
    )

    @contextmanager
    def reader() -> Any:
        yield connection

    service = object.__new__(FullPageService)
    service.database = SimpleNamespace(reader=reader)
    service._payload = lambda row: dict(row)
    try:
        rows = service.list_current_generations("p", "c")
        assert len(rows) == 101
        assert next(row for row in rows if row["page_id"] == "0")["generation_id"] == "second"
        assert service.list_current_generations("another", "c") == []
    finally:
        connection.close()
