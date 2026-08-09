from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from typing import Any, cast
from uuid import UUID

from ..adaptation.models import StoryboardDocument
from ..bibles.models import CharacterBibleDocument
from ..database import Database
from ..errors import ApplicationError
from ..generation.assets import canonical_json
from ..ids import uuid7
from .models import ContinuityEntry, ContinuityKind, ContinuityLedgerDocument


class ContinuityService:
    """Versioned cross-chapter state derived locally from approved project inputs."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def current(self, project_id: str) -> dict[str, Any]:
        row = self._current_row(project_id)
        if row is None:
            raise ApplicationError(
                "CONTINUITY_LEDGER_NOT_FOUND", "尚未建立跨章节状态账本。", 404
            )
        return self._payload(row)

    def versions(self, project_id: str) -> list[dict[str, Any]]:
        self._require_project(project_id)
        with self.database.reader() as connection:
            rows = connection.execute(
                """SELECT v.*, l.project_id, a.approval_hash, a.approved_at,
                          sc.ordinal AS through_chapter_ordinal, sc.title AS through_chapter_title
                   FROM continuity_ledger_versions v
                   JOIN continuity_ledgers l
                     ON l.continuity_ledger_id = v.continuity_ledger_id
                   JOIN source_chapters sc ON sc.chapter_id = v.through_chapter_id
                   LEFT JOIN continuity_approvals a
                     ON a.continuity_version_id = v.continuity_version_id
                   WHERE l.project_id = ? ORDER BY v.version DESC""",
                (project_id,),
            ).fetchall()
        return [self._payload(row) for row in rows]

    def draft(self, project_id: str, chapter_id: str) -> dict[str, Any]:
        chapter = self._chapter(project_id, chapter_id)
        source = self._approved_source(project_id, chapter_id)
        storyboard = StoryboardDocument.model_validate_json(str(source["storyboard_json"]))
        characters = CharacterBibleDocument.model_validate_json(str(source["character_json"]))
        current = self._current_row(project_id)
        if current is not None:
            if current["approval_hash"] is None:
                raise ApplicationError(
                    "CONTINUITY_APPROVAL_REQUIRED",
                    "请先批准当前账本版本，再推进到下一章。",
                    409,
                )
            current_document = ContinuityLedgerDocument.model_validate_json(
                str(current["document_json"])
            )
            expected = current_document.through_chapter_ordinal + 1
            if int(chapter["ordinal"]) != expected:
                raise ApplicationError(
                    "CONTINUITY_CHAPTER_ORDER_INVALID",
                    f"账本必须按章推进，下一章序号应为 {expected}。",
                    409,
                )
            ledger_id = str(current_document.continuity_ledger_id)
            parent_version_id = str(current["continuity_version_id"])
            entries = [entry.model_copy(deep=True) for entry in current_document.entries]
            notes = current_document.notes
        else:
            if int(chapter["ordinal"]) != 1:
                raise ApplicationError(
                    "CONTINUITY_CHAPTER_ORDER_INVALID",
                    "跨章节账本必须从当前章节集的第一章开始。",
                    409,
                )
            ledger_id = str(uuid7())
            parent_version_id = None
            entries = []
            notes = "本账本由已审批分镜和角色设定在本机草拟，请确认状态后再批准。"

        document = self._merge_chapter(
            project_id,
            ledger_id,
            chapter,
            storyboard,
            characters,
            entries,
            notes,
        )
        impact = self._impact(
            project_id,
            None if current is None else self._document(current),
            document,
        )
        with self.database.writer() as connection:
            if current is None:
                connection.execute(
                    """INSERT INTO continuity_ledgers(continuity_ledger_id, project_id)
                       VALUES (?, ?)""",
                    (ledger_id, project_id),
                )
            version_id = self._insert_version(
                connection,
                ledger_id,
                parent_version_id,
                chapter_id,
                str(source["storyboard_version_id"]),
                str(source["character_bible_version_id"]),
                document,
                impact,
                {"change_type": "local_approved_chapter_draft", "external_model_called": False},
            )
            self._audit(
                connection,
                project_id,
                "continuity.version_drafted",
                {
                    "continuity_version_id": version_id,
                    "through_chapter_id": chapter_id,
                    "entry_count": len(document.entries),
                    "external_requests_started": 0,
                },
            )
        return self.current(project_id)

    def impact(
        self, project_id: str, version_id: str, document: ContinuityLedgerDocument
    ) -> dict[str, Any]:
        parent = self._version_row(project_id, version_id)
        normalized = self._normalize_document(parent, document)
        self._validate_document_sources(project_id, normalized)
        return self._impact(project_id, self._document(parent), normalized)

    def revise(
        self, project_id: str, version_id: str, document: ContinuityLedgerDocument
    ) -> dict[str, Any]:
        parent = self._version_row(project_id, version_id)
        self._require_current(parent)
        normalized = self._normalize_document(parent, document)
        self._validate_document_sources(project_id, normalized)
        self._require_stable_entry_ids(self._document(parent), normalized)
        impact = self._impact(project_id, self._document(parent), normalized)
        with self.database.writer() as connection:
            self._assert_current(connection, version_id)
            next_id = self._insert_version(
                connection,
                str(parent["continuity_ledger_id"]),
                version_id,
                str(parent["through_chapter_id"]),
                str(parent["source_storyboard_version_id"]),
                str(parent["source_character_bible_version_id"]),
                normalized,
                impact,
                {
                    "change_type": "manual_edit",
                    "parent_version_id": version_id,
                    "changed_entry_count": len(impact["changed_entries"]),
                },
            )
            self._audit(
                connection,
                project_id,
                "continuity.version_created",
                {
                    "continuity_version_id": next_id,
                    "parent_version_id": version_id,
                    "affected_chapter_count": len(impact["affected_chapters"]),
                    "affected_panel_count": len(impact["affected_panel_ids"]),
                    "external_requests_started": 0,
                },
            )
        return self.current(project_id)

    def approve(self, project_id: str, version_id: str) -> dict[str, Any]:
        row = self._version_row(project_id, version_id)
        self._require_current(row)
        if not self._source_is_fresh(row):
            raise ApplicationError(
                "CONTINUITY_SOURCE_STALE",
                "当前章节的分镜或角色设定已变化，请重新草拟账本。",
                409,
            )
        document = self._document(row)
        self._validate_document_sources(project_id, document)
        approval_hash = hashlib.sha256(
            (canonical_json(document.model_dump(mode="json")) + version_id).encode()
        ).hexdigest()
        with self.database.writer() as connection:
            self._assert_current(connection, version_id)
            connection.execute(
                """INSERT OR IGNORE INTO continuity_approvals(
                       approval_id, continuity_version_id, approval_hash
                   ) VALUES (?, ?, ?)""",
                (str(uuid7()), version_id, approval_hash),
            )
            self._audit(
                connection,
                project_id,
                "continuity.version_approved",
                {
                    "continuity_version_id": version_id,
                    "approval_hash": approval_hash,
                    "external_requests_started": 0,
                },
            )
        return self.current(project_id)

    def _merge_chapter(
        self,
        project_id: str,
        ledger_id: str,
        chapter: sqlite3.Row,
        storyboard: StoryboardDocument,
        characters: CharacterBibleDocument,
        entries: list[ContinuityEntry],
        notes: str,
    ) -> ContinuityLedgerDocument:
        chapter_id = str(chapter["chapter_id"])
        by_key = {entry.stable_key: entry for entry in entries}
        character_panels: dict[str, list[str]] = {}
        all_panels: list[str] = []
        for page in storyboard.pages:
            for panel in page.panels:
                panel_id = str(panel.panel_id)
                all_panels.append(panel_id)
                for name in panel.characters:
                    character_panels.setdefault(name.casefold(), []).append(panel_id)

        for character in characters.characters:
            character_key = self._stable_key("character", character.name)
            panels = character_panels.get(character.name.casefold(), [])
            self._upsert(
                by_key,
                kind="character",
                stable_key=character_key,
                name=character.name,
                status="active",
                attributes={
                    "narrative_role": character.narrative_role,
                    "age_range": character.age_range,
                    "face_shape": character.face_shape,
                    "hair": character.hair,
                    "body_type": character.body_type,
                    "signature_features": "、".join(character.signature_features),
                    "relationships": "、".join(character.relationships),
                },
                chapter_id=chapter_id,
                panel_ids=panels,
            )
            self._upsert(
                by_key,
                kind="outfit",
                stable_key=self._stable_key("outfit", character.name),
                name=f"{character.name}的服装",
                status="current",
                attributes={
                    "character_name": character.name,
                    "items": "、".join(character.outfit),
                },
                chapter_id=chapter_id,
                panel_ids=panels,
            )
            for prop in character.props:
                self._upsert(
                    by_key,
                    kind="prop",
                    stable_key=self._stable_key("prop", prop),
                    name=prop,
                    status="present",
                    attributes={"owner": character.name},
                    chapter_id=chapter_id,
                    panel_ids=panels,
                )

        for scene in storyboard.scenes:
            scene_panels = [
                str(panel.panel_id)
                for page in storyboard.pages
                if scene.scene_id in page.scene_ids
                for panel in page.panels
            ]
            self._upsert(
                by_key,
                kind="location",
                stable_key=self._stable_key("location", scene.location),
                name=scene.location,
                status="established",
                attributes={"time_of_day": scene.time_of_day, "summary": scene.summary},
                chapter_id=chapter_id,
                panel_ids=scene_panels,
            )

        for page in storyboard.pages:
            self._upsert(
                by_key,
                kind="plot",
                stable_key=self._stable_key("plot", f"{chapter_id}:{page.page_number}"),
                name=f"第 {chapter['ordinal']} 章第 {page.page_number} 页转折",
                status="open",
                attributes={"turning_point": page.turning_point},
                chapter_id=chapter_id,
                panel_ids=[str(panel.panel_id) for panel in page.panels],
            )

        return ContinuityLedgerDocument(
            schema_version="1.0",
            continuity_ledger_id=UUID(ledger_id),
            project_id=UUID(project_id),
            through_chapter_id=UUID(chapter_id),
            through_chapter_ordinal=int(chapter["ordinal"]),
            entries=sorted(by_key.values(), key=lambda entry: (entry.kind, entry.stable_key)),
            notes=notes,
        )

    @staticmethod
    def _upsert(
        entries: dict[str, ContinuityEntry],
        *,
        kind: ContinuityKind,
        stable_key: str,
        name: str,
        status: str,
        attributes: dict[str, str],
        chapter_id: str,
        panel_ids: Iterable[str],
    ) -> None:
        existing = entries.get(stable_key)
        chapters = [] if existing is None else [str(item) for item in existing.source_chapter_ids]
        panels = [] if existing is None else [str(item) for item in existing.source_panel_ids]
        chapters = list(dict.fromkeys([*chapters, chapter_id]))
        panels = list(dict.fromkeys([*panels, *panel_ids]))
        entries[stable_key] = ContinuityEntry(
            entry_id=uuid7() if existing is None else existing.entry_id,
            kind=kind,
            stable_key=stable_key,
            name=name,
            status=status if existing is None else existing.status,
            attributes={**({} if existing is None else existing.attributes), **attributes},
            notes="" if existing is None else existing.notes,
            source_chapter_ids=[UUID(item) for item in chapters],
            source_panel_ids=[UUID(item) for item in panels],
        )

    def _impact(
        self,
        project_id: str,
        before: ContinuityLedgerDocument | None,
        after: ContinuityLedgerDocument,
    ) -> dict[str, Any]:
        before_entries = (
            {} if before is None else {entry.stable_key: entry for entry in before.entries}
        )
        after_entries = {entry.stable_key: entry for entry in after.entries}
        changed: list[dict[str, str]] = []
        for key in sorted(set(before_entries) | set(after_entries)):
            old = before_entries.get(key)
            new = after_entries.get(key)
            if old is None and new is not None:
                changed.append(
                    {
                        "stable_key": key,
                        "kind": new.kind,
                        "name": new.name,
                        "change": "added",
                    }
                )
            elif new is None and old is not None:
                changed.append(
                    {
                        "stable_key": key,
                        "kind": old.kind,
                        "name": old.name,
                        "change": "removed",
                    }
                )
            elif old is not None and new is not None and old.model_dump() != new.model_dump():
                changed.append(
                    {
                        "stable_key": key,
                        "kind": new.kind,
                        "name": new.name,
                        "change": "changed",
                    }
                )

        affected_chapters: dict[str, dict[str, Any]] = {}
        affected_panels: set[str] = set()
        with self.database.reader() as connection:
            rows = connection.execute(
                """SELECT s.chapter_id, sc.ordinal, sc.title, sv.document_json
                   FROM storyboards s
                   JOIN source_chapters sc ON sc.chapter_id = s.chapter_id
                   JOIN storyboard_versions sv ON sv.storyboard_id = s.storyboard_id
                   JOIN storyboard_approvals sa
                     ON sa.storyboard_version_id = sv.storyboard_version_id
                   WHERE s.project_id = ? AND sv.is_current = 1 AND sc.ordinal > ?
                   ORDER BY sc.ordinal""",
                (project_id, after.through_chapter_ordinal),
            ).fetchall()
        for row in rows:
            storyboard = StoryboardDocument.model_validate_json(str(row["document_json"]))
            matched: set[str] = set()
            for change in changed:
                for page in storyboard.pages:
                    for panel in page.panels:
                        haystack = " ".join(
                            [
                                *panel.characters,
                                panel.purpose,
                                panel.visual_prompt,
                                *[line.text for line in panel.dialogue],
                            ]
                        ).casefold()
                        character_name = after_entries.get(change["stable_key"])
                        lookup = change["name"].removesuffix("的服装").casefold()
                        if change["kind"] == "plot" or lookup in haystack:
                            matched.add(str(panel.panel_id))
                        elif character_name is not None:
                            owner = character_name.attributes.get("character_name", "").casefold()
                            if owner and owner in haystack:
                                matched.add(str(panel.panel_id))
            if matched:
                chapter_id = str(row["chapter_id"])
                affected_panels.update(matched)
                affected_chapters[chapter_id] = {
                    "chapter_id": chapter_id,
                    "ordinal": int(row["ordinal"]),
                    "title": str(row["title"]),
                    "panel_count": len(matched),
                }
        return {
            "changed_entries": changed,
            "affected_chapters": list(affected_chapters.values()),
            "affected_panel_ids": sorted(affected_panels),
            "requires_future_review": bool(affected_chapters),
            "external_requests_started": 0,
        }

    def _insert_version(
        self,
        connection: sqlite3.Connection,
        ledger_id: str,
        parent_version_id: str | None,
        chapter_id: str,
        storyboard_version_id: str,
        character_version_id: str,
        document: ContinuityLedgerDocument,
        impact: dict[str, Any],
        provenance: dict[str, Any],
    ) -> str:
        row = connection.execute(
            """SELECT COALESCE(MAX(version), 0) + 1 AS next_version
               FROM continuity_ledger_versions WHERE continuity_ledger_id = ?""",
            (ledger_id,),
        ).fetchone()
        version = int(row["next_version"])
        version_id = str(uuid7())
        serialized = canonical_json(document.model_dump(mode="json"))
        connection.execute(
            """UPDATE continuity_ledger_versions SET is_current = 0
               WHERE continuity_ledger_id = ? AND is_current = 1""",
            (ledger_id,),
        )
        connection.execute(
            """INSERT INTO continuity_ledger_versions(
                   continuity_version_id, continuity_ledger_id, version,
                   parent_version_id, through_chapter_id,
                   source_storyboard_version_id, source_character_bible_version_id,
                   document_json, document_sha256, provenance_json, impact_json, is_current
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (
                version_id,
                ledger_id,
                version,
                parent_version_id,
                chapter_id,
                storyboard_version_id,
                character_version_id,
                serialized,
                hashlib.sha256(serialized.encode()).hexdigest(),
                canonical_json(provenance),
                canonical_json(impact),
            ),
        )
        return version_id

    def _payload(self, row: sqlite3.Row) -> dict[str, Any]:
        approval_status = "approved" if row["approval_hash"] is not None else "draft"
        if approval_status == "approved" and not self._source_is_fresh(row):
            approval_status = "stale"
        return {
            "continuity_ledger_id": str(row["continuity_ledger_id"]),
            "continuity_version_id": str(row["continuity_version_id"]),
            "project_id": str(row["project_id"]),
            "version": int(row["version"]),
            "parent_version_id": row["parent_version_id"],
            "through_chapter_id": str(row["through_chapter_id"]),
            "through_chapter_ordinal": int(row["through_chapter_ordinal"]),
            "through_chapter_title": str(row["through_chapter_title"]),
            "source_storyboard_version_id": str(row["source_storyboard_version_id"]),
            "source_character_bible_version_id": str(row["source_character_bible_version_id"]),
            "document_sha256": str(row["document_sha256"]),
            "document": json.loads(str(row["document_json"])),
            "provenance": json.loads(str(row["provenance_json"])),
            "impact": json.loads(str(row["impact_json"])),
            "approval_status": approval_status,
            "approval_hash": row["approval_hash"],
            "approved_at": row["approved_at"],
            "is_current": bool(row["is_current"]),
            "created_at": str(row["created_at"]),
            "external_requests_started": 0,
        }

    def _current_row(self, project_id: str) -> sqlite3.Row | None:
        with self.database.reader() as connection:
            row = connection.execute(
                """SELECT v.*, l.project_id, a.approval_hash, a.approved_at,
                          sc.ordinal AS through_chapter_ordinal, sc.title AS through_chapter_title
                   FROM continuity_ledgers l
                   JOIN continuity_ledger_versions v
                     ON v.continuity_ledger_id = l.continuity_ledger_id
                   JOIN source_chapters sc ON sc.chapter_id = v.through_chapter_id
                   LEFT JOIN continuity_approvals a
                     ON a.continuity_version_id = v.continuity_version_id
                   WHERE l.project_id = ? AND v.is_current = 1""",
                (project_id,),
            ).fetchone()
        return cast(sqlite3.Row | None, row)

    def _version_row(self, project_id: str, version_id: str) -> sqlite3.Row:
        with self.database.reader() as connection:
            row = connection.execute(
                """SELECT v.*, l.project_id, a.approval_hash, a.approved_at,
                          sc.ordinal AS through_chapter_ordinal, sc.title AS through_chapter_title
                   FROM continuity_ledger_versions v
                   JOIN continuity_ledgers l
                     ON l.continuity_ledger_id = v.continuity_ledger_id
                   JOIN source_chapters sc ON sc.chapter_id = v.through_chapter_id
                   LEFT JOIN continuity_approvals a
                     ON a.continuity_version_id = v.continuity_version_id
                   WHERE l.project_id = ? AND v.continuity_version_id = ?""",
                (project_id, version_id),
            ).fetchone()
        if row is None:
            raise ApplicationError("CONTINUITY_VERSION_NOT_FOUND", "没有找到该账本版本。", 404)
        return cast(sqlite3.Row, row)

    def _approved_source(self, project_id: str, chapter_id: str) -> sqlite3.Row:
        with self.database.reader() as connection:
            row = connection.execute(
                """SELECT sv.storyboard_version_id, sv.document_json AS storyboard_json,
                          cbv.character_bible_version_id,
                          cbv.document_json AS character_json
                   FROM storyboards s
                   JOIN storyboard_versions sv ON sv.storyboard_id = s.storyboard_id
                   JOIN storyboard_approvals sa
                     ON sa.storyboard_version_id = sv.storyboard_version_id
                   JOIN character_bibles cb
                     ON cb.project_id = s.project_id AND cb.chapter_id = s.chapter_id
                   JOIN character_bible_versions cbv
                     ON cbv.character_bible_id = cb.character_bible_id
                   JOIN character_bible_approvals cba
                     ON cba.character_bible_version_id = cbv.character_bible_version_id
                   WHERE s.project_id = ? AND s.chapter_id = ?
                     AND sv.is_current = 1 AND cbv.is_current = 1""",
                (project_id, chapter_id),
            ).fetchone()
        if row is None:
            raise ApplicationError(
                "CONTINUITY_SOURCE_APPROVAL_REQUIRED",
                "当前章节必须先批准分镜和角色设定，才能更新状态账本。",
                409,
            )
        return cast(sqlite3.Row, row)

    def _chapter(self, project_id: str, chapter_id: str) -> sqlite3.Row:
        with self.database.reader() as connection:
            row = connection.execute(
                """SELECT sc.* FROM source_chapters sc
                   JOIN source_chapter_sets cs ON cs.chapter_set_id = sc.chapter_set_id
                   JOIN source_files sf ON sf.source_file_id = cs.source_file_id
                   WHERE sf.project_id = ? AND sc.chapter_id = ? AND cs.is_current = 1""",
                (project_id, chapter_id),
            ).fetchone()
        if row is None:
            raise ApplicationError("SOURCE_CHAPTER_NOT_FOUND", "没有找到当前章节。", 404)
        return cast(sqlite3.Row, row)

    def _source_is_fresh(self, row: sqlite3.Row) -> bool:
        with self.database.reader() as connection:
            current = connection.execute(
                """SELECT sv.storyboard_version_id, cbv.character_bible_version_id
                   FROM storyboards s
                   JOIN storyboard_versions sv ON sv.storyboard_id = s.storyboard_id
                   JOIN character_bibles cb
                     ON cb.project_id = s.project_id AND cb.chapter_id = s.chapter_id
                   JOIN character_bible_versions cbv
                     ON cbv.character_bible_id = cb.character_bible_id
                   WHERE s.project_id = ? AND s.chapter_id = ?
                     AND sv.is_current = 1 AND cbv.is_current = 1""",
                (str(row["project_id"]), str(row["through_chapter_id"])),
            ).fetchone()
        return current is not None and (
            str(current["storyboard_version_id"]) == str(row["source_storyboard_version_id"])
            and str(current["character_bible_version_id"])
            == str(row["source_character_bible_version_id"])
        )

    def _validate_document_sources(
        self, project_id: str, document: ContinuityLedgerDocument
    ) -> None:
        if str(document.project_id) != project_id:
            raise ApplicationError("CONTINUITY_PROJECT_MISMATCH", "账本项目标识不匹配。", 422)
        chapter_ids = {str(item) for entry in document.entries for item in entry.source_chapter_ids}
        panel_ids = {str(item) for entry in document.entries for item in entry.source_panel_ids}
        with self.database.reader() as connection:
            known_chapters = {
                str(row["chapter_id"])
                for row in connection.execute(
                    """SELECT sc.chapter_id FROM source_chapters sc
                       JOIN source_chapter_sets cs ON cs.chapter_set_id = sc.chapter_set_id
                       JOIN source_files sf ON sf.source_file_id = cs.source_file_id
                       WHERE sf.project_id = ?""",
                    (project_id,),
                )
            }
            known_panels = {
                str(panel.panel_id)
                for row in connection.execute(
                    """SELECT sv.document_json FROM storyboard_versions sv
                       JOIN storyboards s ON s.storyboard_id = sv.storyboard_id
                       WHERE s.project_id = ?""",
                    (project_id,),
                )
                for page in StoryboardDocument.model_validate_json(str(row["document_json"])).pages
                for panel in page.panels
            }
        if not chapter_ids.issubset(known_chapters) or not panel_ids.issubset(known_panels):
            raise ApplicationError(
                "CONTINUITY_SOURCE_INVALID",
                "账本包含不属于当前项目的章节或分格来源。",
                422,
            )

    @staticmethod
    def _normalize_document(
        parent: sqlite3.Row, document: ContinuityLedgerDocument
    ) -> ContinuityLedgerDocument:
        return document.model_copy(
            update={
                "continuity_ledger_id": UUID(str(parent["continuity_ledger_id"])),
                "project_id": UUID(str(parent["project_id"])),
                "through_chapter_id": UUID(str(parent["through_chapter_id"])),
                "through_chapter_ordinal": int(parent["through_chapter_ordinal"]),
            }
        )

    @staticmethod
    def _require_stable_entry_ids(
        before: ContinuityLedgerDocument, after: ContinuityLedgerDocument
    ) -> None:
        before_ids = {entry.stable_key: entry.entry_id for entry in before.entries}
        if any(
            key in before_ids and before_ids[key] != entry.entry_id
            for key, entry in ((entry.stable_key, entry) for entry in after.entries)
        ):
            raise ApplicationError(
                "CONTINUITY_ENTRY_ID_CHANGED",
                "已有状态项的稳定标识不能修改。",
                422,
            )

    @staticmethod
    def _stable_key(kind: ContinuityKind, value: str) -> str:
        digest = hashlib.sha256(" ".join(value.casefold().split()).encode()).hexdigest()[:24]
        return f"{kind}:{digest}"

    @staticmethod
    def _document(row: sqlite3.Row) -> ContinuityLedgerDocument:
        return ContinuityLedgerDocument.model_validate_json(str(row["document_json"]))

    @staticmethod
    def _require_current(row: sqlite3.Row) -> None:
        if not bool(row["is_current"]):
            raise ApplicationError(
                "CONTINUITY_VERSION_CONFLICT", "账本版本已经变化，请刷新后重试。", 409
            )

    @staticmethod
    def _assert_current(connection: sqlite3.Connection, version_id: str) -> None:
        row = connection.execute(
            """SELECT 1 FROM continuity_ledger_versions
               WHERE continuity_version_id = ? AND is_current = 1""",
            (version_id,),
        ).fetchone()
        if row is None:
            raise ApplicationError(
                "CONTINUITY_VERSION_CONFLICT", "账本版本已经变化，请刷新后重试。", 409
            )

    def _require_project(self, project_id: str) -> None:
        with self.database.reader() as connection:
            row = connection.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
        if row is None:
            raise ApplicationError("PROJECT_NOT_FOUND", "没有找到该项目。", 404)

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        project_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        connection.execute(
            """INSERT INTO audit_events(event_id, project_id, event_type, payload_json)
               VALUES (?, ?, ?, ?)""",
            (str(uuid7()), project_id, event_type, canonical_json(payload)),
        )
