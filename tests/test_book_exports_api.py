from fastapi.testclient import TestClient

from tests.test_pages_api import prepare_page


def test_book_export_preflight_uses_explicit_book_scope(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    prepared, provider, page = prepare_page(client, session_headers)
    project = prepared["project_id"]
    chapter = prepared["chapter"]["chapter_id"]
    before = provider.generation_calls
    response = client.post(
        f"/api/v1/projects/{project}/exports/book/preflight",
        headers=session_headers,
        json={"chapter_ids": [chapter]},
    )
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["scope"] == "book"
    assert plan["pages"][0]["chapter_id"] == chapter
    assert plan["pages"][0]["page_version_id"] == page["page_version_id"]
    assert provider.generation_calls == before


def test_hundred_page_book_preserves_order_and_rejects_incomplete_selection(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    import copy
    import hashlib
    import io
    import json
    import zipfile
    from pathlib import Path
    from uuid import uuid4

    from PIL import Image, ImageDraw, PdfParser

    from tests.test_exports_api import download_file

    prepared, provider, page = prepare_page(client, session_headers)
    project = prepared["project_id"]
    database = client.app.state.database
    chapters, versions = [], []
    # Expand an already valid isolated page into a volume-selection fixture. These tiny
    # numbered images test ordering and serialization, never real-provider visual quality.
    with database.writer() as db:
        root = dict(
            db.execute("SELECT * FROM comic_pages WHERE page_id = ?", (page["page_id"],)).fetchone()
        )
        version = dict(
            db.execute(
                "SELECT * FROM page_versions WHERE page_version_id = ?", (page["page_version_id"],)
            ).fetchone()
        )
        chapter = dict(
            db.execute(
                "SELECT * FROM source_chapters WHERE chapter_id = ?", (root["chapter_id"],)
            ).fetchone()
        )
        board = dict(
            db.execute(
                "SELECT * FROM storyboards WHERE chapter_id = ?", (root["chapter_id"],)
            ).fetchone()
        )
        board_version = dict(
            db.execute(
                "SELECT * FROM storyboard_versions WHERE storyboard_version_id = ?",
                (version["storyboard_version_id"],),
            ).fetchone()
        )
        approval = dict(
            db.execute(
                "SELECT * FROM storyboard_approvals WHERE storyboard_version_id = ?",
                (version["storyboard_version_id"],),
            ).fetchone()
        )
        workspace = Path(
            db.execute(
                "SELECT workspace_path FROM projects WHERE project_id = ?", (project,)
            ).fetchone()[0]
        )

        def insert(table, record):
            db.execute(
                f"INSERT INTO {table} ({','.join(record)}) "
                f"VALUES ({','.join('?' for _ in record)})",
                tuple(record.values()),
            )

        for ordinal, count in enumerate([35, 35, 30], 1):
            chapter_id = chapter["chapter_id"] if ordinal == 1 else str(uuid4())
            board_id = board["storyboard_id"] if ordinal == 1 else str(uuid4())
            board_version_id = (
                board_version["storyboard_version_id"] if ordinal == 1 else str(uuid4())
            )
            chapters.append(chapter_id)
            if ordinal > 1:
                insert("source_chapters", {**chapter, "chapter_id": chapter_id, "ordinal": ordinal})
                insert(
                    "storyboards", {**board, "storyboard_id": board_id, "chapter_id": chapter_id}
                )
                insert(
                    "storyboard_versions",
                    {
                        **board_version,
                        "storyboard_version_id": board_version_id,
                        "storyboard_id": board_id,
                        "page_budget": count,
                    },
                )
                insert(
                    "storyboard_approvals",
                    {
                        **approval,
                        "approval_id": str(uuid4()),
                        "storyboard_version_id": board_version_id,
                    },
                )
            board_document = json.loads(board_version["document_json"])
            board_pages = []
            for number in range(1, count + 1):
                global_number = len(versions) + 1
                first = global_number == 1
                page_id = page["page_id"] if first else str(uuid4())
                version_id = page["page_version_id"] if first else str(uuid4())
                versions.append(version_id)
                document = copy.deepcopy(page["document"])
                document.update(
                    schema_version="2.0",
                    page_id=page_id,
                    page_number=number,
                    width=512,
                    height=768,
                    text_layers=[],
                    storyboard_version_id=board_version_id,
                )
                document["panels"] = [document["panels"][0]]
                document["panels"][0]["frame"] = dict(x=0, y=0, width=512, height=768)
                target = workspace / "pages" / f"volume-fixture-{global_number:03}.png"
                image = Image.new("RGB", (512, 768), (global_number, 150, 200))
                ImageDraw.Draw(image).text((32, 32), str(global_number), fill="black")
                image.save(target)
                digest = hashlib.sha256(target.read_bytes()).hexdigest()
                new_root = {
                    **root,
                    "page_id": page_id,
                    "chapter_id": chapter_id,
                    "page_number": number,
                }
                new_version = {
                    **version,
                    "page_id": page_id,
                    "page_version_id": version_id,
                    "storyboard_version_id": board_version_id,
                    "document_json": json.dumps(document),
                    "render_sha256": digest,
                    "rendered_relative_path": str(target.relative_to(workspace)),
                }
                if first:
                    db.execute(
                        "UPDATE page_versions SET document_json = ?, rendered_relative_path = ?, "
                        "render_sha256 = ? WHERE page_version_id = ?",
                        (
                            new_version["document_json"],
                            new_version["rendered_relative_path"],
                            digest,
                            version_id,
                        ),
                    )
                else:
                    insert("comic_pages", new_root)
                    insert("page_versions", new_version)
                board_pages.append(
                    {**board_document["pages"][0], "page_id": page_id, "page_number": number}
                )
            board_document["pages"] = board_pages
            db.execute(
                "UPDATE storyboard_versions SET document_json = ?, page_budget = ? "
                "WHERE storyboard_version_id = ?",
                (json.dumps(board_document), count, board_version_id),
            )

    path = f"/api/v1/projects/{project}/exports/book"
    before = provider.generation_calls
    response = client.post(
        path + "/preflight", headers=session_headers, json={"chapter_ids": chapters}
    )
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["page_count"] == 100
    assert [p["page_version_id"] for p in plan["pages"]] == versions
    assert [p["ordinal"] for p in plan["pages"]] == list(range(1, 101))
    assert plan["pages"][35]["chapter_ordinal"] == 2 and plan["pages"][35]["page_number"] == 1
    for invalid_chapters, invalid_pages in [
        (chapters[:2], versions),
        (list(reversed(chapters)), versions),
        (chapters, versions[:-1]),
        (chapters, list(reversed(versions))),
        (chapters, [versions[0], *versions[:-1]]),
        (chapters, [str(uuid4()), *versions[1:]]),
    ]:
        denied = client.post(
            path + "/preflight",
            headers=session_headers,
            json={"chapter_ids": invalid_chapters, "page_version_ids": invalid_pages},
        )
        assert denied.status_code in (409, 422), denied.text
    exported = client.post(
        path,
        headers=session_headers,
        json={
            "chapter_ids": chapters,
            "page_version_ids": versions,
            "plan_fingerprint": plan["plan_fingerprint"],
            "confirmed": True,
        },
    )
    assert exported.status_code == 201, exported.text
    result = exported.json()
    assert result["scope"] == "book" and result["chapter_title"] == "全书"
    assert len([f for f in result["files"] if f["kind"] == "png"]) == 100
    for item in result["files"]:
        if item["kind"] == "png":
            continue
        data = download_file(client, session_headers, project, result, item)
        if item["kind"] == "pdf":
            parser = PdfParser.PdfParser(buf=data)
            assert len(parser.pages) == 100
            parser.close()
        else:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                assert archive.testzip() is None
                if item["kind"] == "cbz":
                    assert [n for n in archive.namelist() if n.endswith(".png")] == [
                        f"{n:03}.png" for n in range(1, 101)
                    ]
                    for n, selected in enumerate(plan["pages"], 1):
                        assert (
                            hashlib.sha256(archive.read(f"{n:03}.png")).hexdigest()
                            == selected["render_sha256"]
                        )
                else:
                    assert len(json.loads(archive.read("manifest.json"))["selected_pages"]) == 100
    assert provider.generation_calls == before
