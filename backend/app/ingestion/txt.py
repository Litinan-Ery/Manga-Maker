from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from charset_normalizer import from_bytes

from ..database import Database
from ..errors import ApplicationError
from ..ids import uuid7
from ..projects import ProjectService

MAX_TXT_BYTES = 10 * 1024 * 1024
PREVIEW_CHARACTERS = 600
MAX_STORY_BEATS_PER_CHAPTER = 2000
CHAPTER_PATTERN = re.compile(
    r"(?m)^[ \t]*(?:"
    r"第[零〇一二两三四五六七八九十百千万\d]+[章节回卷部篇集](?:[ \t]+[^\n]{0,60})?"
    r"|序章|楔子|前言|后记|尾声"
    r")[ \t]*$"
)


@dataclass(frozen=True, slots=True)
class EncodingCandidate:
    encoding: str
    confidence: float
    preview: str
    cjk_ratio: float


@dataclass(frozen=True, slots=True)
class ChapterBoundary:
    title: str
    start_offset: int
    end_offset: int


class TxtIngestionService:
    def __init__(self, database: Database, projects: ProjectService) -> None:
        self.database = database
        self.projects = projects

    def preflight(self, project_id: str, filename: str, content: bytes) -> dict[str, Any]:
        workspace = self.projects.workspace_path(project_id)
        if not content:
            raise ApplicationError(
                code="EMPTY_TXT_FILE",
                message="TXT 文件为空。",
                status_code=422,
            )
        if len(content) > MAX_TXT_BYTES:
            raise ApplicationError(
                code="TXT_FILE_TOO_LARGE",
                message="TXT 文件超过 P0 的 10 MB 安全上限。",
                status_code=413,
            )
        if b"\x00" in content:
            raise ApplicationError(
                code="BINARY_FILE_REJECTED",
                message="文件包含二进制空字节，不能作为 TXT 导入。",
                status_code=422,
            )

        candidates = detect_encoding_candidates(content)
        if not candidates:
            raise ApplicationError(
                code="TXT_ENCODING_UNDETECTED",
                message="无法可靠解码该 TXT，请先转换为 UTF-8 或 GB18030。",
                status_code=422,
            )

        preflight_id = str(uuid7())
        staging_path = workspace / "source" / "preflight" / f"{preflight_id}.bin"
        self._write_bytes(staging_path, content)
        byte_sha256 = hashlib.sha256(content).hexdigest()
        candidate_payload = [asdict(candidate) for candidate in candidates]
        with self.database.writer() as connection:
            connection.execute(
                """
                INSERT INTO source_preflights(
                    preflight_id, project_id, original_filename, staging_path,
                    byte_size, sha256, candidates_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    preflight_id,
                    project_id,
                    Path(filename).name or "source.txt",
                    str(staging_path),
                    len(content),
                    byte_sha256,
                    json.dumps(candidate_payload, ensure_ascii=False),
                ),
            )
        recommended = candidates[0]
        return {
            "preflight_id": preflight_id,
            "filename": Path(filename).name or "source.txt",
            "byte_size": len(content),
            "sha256": byte_sha256,
            "candidates": candidate_payload,
            "recommended_encoding": recommended.encoding,
            "requires_confirmation": recommended.confidence < 0.95,
        }

    def confirm(self, project_id: str, preflight_id: str, encoding: str) -> dict[str, Any]:
        workspace = self.projects.workspace_path(project_id)
        with self.database.reader() as connection:
            row = connection.execute(
                """
                SELECT * FROM source_preflights
                WHERE preflight_id = ? AND project_id = ?
                """,
                (preflight_id, project_id),
            ).fetchone()
        if row is None:
            raise ApplicationError(
                code="SOURCE_PREFLIGHT_NOT_FOUND",
                message="没有找到该 TXT 预检记录。",
                status_code=404,
            )
        if row["status"] != "pending":
            raise ApplicationError(
                code="SOURCE_PREFLIGHT_ALREADY_USED",
                message="该 TXT 预检已经确认。",
                status_code=409,
            )
        staging_path = Path(str(row["staging_path"])).resolve()
        allowed_root = (workspace / "source" / "preflight").resolve()
        if not staging_path.is_relative_to(allowed_root) or not staging_path.is_file():
            raise ApplicationError(
                code="SOURCE_PREFLIGHT_DAMAGED",
                message="TXT 预检文件缺失或路径无效。",
                status_code=409,
            )
        content = staging_path.read_bytes()
        if hashlib.sha256(content).hexdigest() != row["sha256"]:
            raise ApplicationError(
                code="SOURCE_PREFLIGHT_DAMAGED",
                message="TXT 预检文件哈希不匹配。",
                status_code=409,
            )
        try:
            decoded = content.decode(encoding, errors="strict")
        except (LookupError, UnicodeDecodeError) as exc:
            raise ApplicationError(
                code="TXT_ENCODING_INVALID",
                message="所选编码无法完整解码该文件。",
                status_code=422,
            ) from exc
        normalized_text = normalize_text(decoded)
        if not normalized_text.strip():
            raise ApplicationError(
                code="EMPTY_TXT_CONTENT",
                message="解码后没有可用正文。",
                status_code=422,
            )

        source_file_id = str(uuid7())
        source_root = workspace / "source"
        original_path = source_root / f"original-{source_file_id}.txt"
        normalized_path = source_root / f"normalized-{source_file_id}.txt"
        self._write_bytes(original_path, content)
        self._write_bytes(normalized_path, normalized_text.encode("utf-8"))
        boundaries = detect_chapters(normalized_text)
        chapter_set_id = str(uuid7())
        try:
            with self.database.writer() as connection:
                connection.execute(
                    """
                    INSERT INTO source_files(
                        source_file_id, project_id, preflight_id, original_filename,
                        original_path, normalized_path, encoding, byte_sha256,
                        text_sha256, character_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source_file_id,
                        project_id,
                        preflight_id,
                        str(row["original_filename"]),
                        str(original_path),
                        str(normalized_path),
                        encoding,
                        str(row["sha256"]),
                        hashlib.sha256(normalized_text.encode("utf-8")).hexdigest(),
                        len(normalized_text),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO source_chapter_sets(
                        chapter_set_id, source_file_id, version, is_current
                    ) VALUES (?, ?, 1, 1)
                    """,
                    (chapter_set_id, source_file_id),
                )
                chapters = self._insert_chapters(
                    connection, chapter_set_id, boundaries, normalized_text
                )
                connection.execute(
                    "UPDATE source_preflights SET status = 'confirmed' WHERE preflight_id = ?",
                    (preflight_id,),
                )
                connection.execute(
                    """
                    UPDATE projects
                    SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP
                    WHERE project_id = ?
                    """,
                    (project_id,),
                )
        except Exception:
            self._move_to_orphan(original_path)
            self._move_to_orphan(normalized_path)
            raise
        return {
            "source_file_id": source_file_id,
            "encoding": encoding,
            "character_count": len(normalized_text),
            "chapter_set_id": chapter_set_id,
            "chapter_set_version": 1,
            "chapters": chapters,
        }

    def current_chapters(self, project_id: str) -> dict[str, Any]:
        with self.database.reader() as connection:
            source_row = connection.execute(
                """
                SELECT source_file_id FROM source_files
                WHERE project_id = ? ORDER BY created_at DESC, source_file_id DESC LIMIT 1
                """,
                (project_id,),
            ).fetchone()
            if source_row is None:
                raise ApplicationError(
                    code="SOURCE_FILE_NOT_FOUND",
                    message="该项目尚未确认 TXT 源文件。",
                    status_code=404,
                )
            set_row = connection.execute(
                """
                SELECT chapter_set_id, version FROM source_chapter_sets
                WHERE source_file_id = ? AND is_current = 1
                """,
                (source_row["source_file_id"],),
            ).fetchone()
            if set_row is None:
                raise ApplicationError(
                    code="CHAPTER_SET_NOT_FOUND",
                    message="没有找到当前章节版本。",
                    status_code=404,
                )
            rows = connection.execute(
                """
                SELECT chapter_id, version, ordinal, title, start_offset, end_offset,
                       text_sha256
                FROM source_chapters WHERE chapter_set_id = ? ORDER BY ordinal
                """,
                (set_row["chapter_set_id"],),
            ).fetchall()
        return {
            "source_file_id": str(source_row["source_file_id"]),
            "chapter_set_id": str(set_row["chapter_set_id"]),
            "chapter_set_version": int(set_row["version"]),
            "chapters": [dict(row) for row in rows],
        }

    def replace_chapters(
        self,
        project_id: str,
        source_file_id: str,
        boundaries: list[ChapterBoundary],
    ) -> dict[str, Any]:
        normalized_text = self._source_text(project_id, source_file_id)
        validate_boundaries(boundaries, len(normalized_text))
        with self.database.writer() as connection:
            current = connection.execute(
                """
                SELECT chapter_set_id, version FROM source_chapter_sets
                WHERE source_file_id = ? AND is_current = 1
                """,
                (source_file_id,),
            ).fetchone()
            if current is None:
                raise ApplicationError(
                    code="CHAPTER_SET_NOT_FOUND",
                    message="没有找到当前章节版本。",
                    status_code=404,
                )
            next_version = int(current["version"]) + 1
            next_set_id = str(uuid7())
            connection.execute(
                "UPDATE source_chapter_sets SET is_current = 0 WHERE chapter_set_id = ?",
                (current["chapter_set_id"],),
            )
            connection.execute(
                """
                INSERT INTO source_chapter_sets(
                    chapter_set_id, source_file_id, version, is_current
                ) VALUES (?, ?, ?, 1)
                """,
                (next_set_id, source_file_id, next_version),
            )
            chapters = self._insert_chapters(connection, next_set_id, boundaries, normalized_text)
            connection.execute(
                """
                UPDATE projects
                SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP
                WHERE project_id = ?
                """,
                (project_id,),
            )
        return {
            "source_file_id": source_file_id,
            "chapter_set_id": next_set_id,
            "chapter_set_version": next_version,
            "chapters": chapters,
        }

    def chapter_text(self, project_id: str, chapter_id: str) -> dict[str, Any]:
        with self.database.reader() as connection:
            row = connection.execute(
                """
                SELECT c.chapter_id, c.title, c.start_offset, c.end_offset,
                       sf.normalized_path
                FROM source_chapters c
                JOIN source_chapter_sets cs ON cs.chapter_set_id = c.chapter_set_id
                JOIN source_files sf ON sf.source_file_id = cs.source_file_id
                WHERE c.chapter_id = ? AND sf.project_id = ? AND cs.is_current = 1
                """,
                (chapter_id, project_id),
            ).fetchone()
        if row is None:
            raise ApplicationError(
                code="SOURCE_CHAPTER_NOT_FOUND",
                message="没有找到当前版本中的该章节。",
                status_code=404,
            )
        source_text = Path(str(row["normalized_path"])).read_text(encoding="utf-8")
        start = int(row["start_offset"])
        end = int(row["end_offset"])
        return {
            "chapter_id": str(row["chapter_id"]),
            "title": str(row["title"]),
            "start_offset": start,
            "end_offset": end,
            "text": source_text[start:end],
        }

    def draft_story_beats(self, project_id: str, chapter_id: str) -> dict[str, Any]:
        chapter = self._current_chapter(project_id, chapter_id)
        source_text = Path(str(chapter["normalized_path"])).read_text(encoding="utf-8")
        chapter_start = int(chapter["start_offset"])
        chapter_end = int(chapter["end_offset"])
        chapter_text = source_text[chapter_start:chapter_end]
        segments = segment_story_beats(chapter_text)
        if not segments:
            raise ApplicationError(
                code="STORY_BEAT_SOURCE_EMPTY",
                message="该章节没有可提取的剧情文本。",
                status_code=422,
            )
        if len(segments) > MAX_STORY_BEATS_PER_CHAPTER:
            raise ApplicationError(
                code="TOO_MANY_STORY_BEATS",
                message="该章节产生的剧情节拍过多，请先拆分章节。",
                status_code=422,
                details={"limit": MAX_STORY_BEATS_PER_CHAPTER, "actual": len(segments)},
            )

        beat_set_id = str(uuid7())
        with self.database.writer() as connection:
            current = connection.execute(
                """
                SELECT beat_set_id, version FROM story_beat_sets
                WHERE chapter_id = ? AND is_current = 1
                """,
                (chapter_id,),
            ).fetchone()
            next_version = int(current["version"]) + 1 if current is not None else 1
            if current is not None:
                connection.execute(
                    "UPDATE story_beat_sets SET is_current = 0 WHERE beat_set_id = ?",
                    (current["beat_set_id"],),
                )
            connection.execute(
                """
                INSERT INTO story_beat_sets(beat_set_id, chapter_id, version, is_current)
                VALUES (?, ?, ?, 1)
                """,
                (beat_set_id, chapter_id, next_version),
            )
            beats: list[dict[str, Any]] = []
            for ordinal, (start, end) in enumerate(segments, start=1):
                excerpt = chapter_text[start:end]
                anchor_id = str(uuid7())
                excerpt_sha256 = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
                connection.execute(
                    """
                    INSERT INTO source_anchors(
                        anchor_id, chapter_id, chapter_version, start_offset,
                        end_offset, excerpt_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        anchor_id,
                        chapter_id,
                        int(chapter["version"]),
                        start,
                        end,
                        excerpt_sha256,
                    ),
                )
                beat_id = str(uuid7())
                summary = " ".join(excerpt.split())[:160]
                connection.execute(
                    """
                    INSERT INTO story_beats(
                        beat_id, beat_set_id, ordinal, anchor_id, source_summary
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (beat_id, beat_set_id, ordinal, anchor_id, summary),
                )
                beats.append(
                    {
                        "beat_id": beat_id,
                        "ordinal": ordinal,
                        "anchor_id": anchor_id,
                        "source_summary": summary,
                        "source_excerpt": excerpt,
                        "start_offset": start,
                        "end_offset": end,
                        "excerpt_sha256": excerpt_sha256,
                        "resolution_status": "unresolved",
                        "omission_reason": None,
                    }
                )
            connection.execute(
                """
                INSERT INTO audit_events(event_id, project_id, event_type, payload_json)
                VALUES (?, ?, 'story_beats.drafted', ?)
                """,
                (
                    str(uuid7()),
                    project_id,
                    json.dumps(
                        {
                            "chapter_id": chapter_id,
                            "beat_set_id": beat_set_id,
                            "version": next_version,
                            "beat_count": len(beats),
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
        return {
            "beat_set_id": beat_set_id,
            "beat_set_version": next_version,
            "chapter_id": chapter_id,
            "beats": beats,
        }

    def current_story_beats(self, project_id: str, chapter_id: str) -> dict[str, Any]:
        chapter = self._current_chapter(project_id, chapter_id)
        with self.database.reader() as connection:
            beat_set = connection.execute(
                """
                SELECT beat_set_id, version FROM story_beat_sets
                WHERE chapter_id = ? AND is_current = 1
                """,
                (chapter_id,),
            ).fetchone()
            if beat_set is None:
                raise ApplicationError(
                    code="STORY_BEAT_SET_NOT_FOUND",
                    message="该章节尚未建立剧情节拍。",
                    status_code=404,
                )
            rows = connection.execute(
                """
                SELECT b.beat_id, b.ordinal, b.anchor_id, b.source_summary,
                       b.resolution_status, b.omission_reason,
                       a.start_offset, a.end_offset, a.excerpt_sha256
                FROM story_beats b
                JOIN source_anchors a ON a.anchor_id = b.anchor_id
                WHERE b.beat_set_id = ?
                ORDER BY b.ordinal
                """,
                (beat_set["beat_set_id"],),
            ).fetchall()
        source_text = Path(str(chapter["normalized_path"])).read_text(encoding="utf-8")
        chapter_text = source_text[int(chapter["start_offset"]) : int(chapter["end_offset"])]
        beats = []
        for row in rows:
            beat = dict(row)
            beat["source_excerpt"] = chapter_text[int(row["start_offset"]) : int(row["end_offset"])]
            beats.append(beat)
        return {
            "beat_set_id": str(beat_set["beat_set_id"]),
            "beat_set_version": int(beat_set["version"]),
            "chapter_id": chapter_id,
            "beats": beats,
        }

    def _current_chapter(self, project_id: str, chapter_id: str) -> sqlite3.Row:
        with self.database.reader() as connection:
            row = connection.execute(
                """
                SELECT c.chapter_id, c.version, c.start_offset, c.end_offset,
                       sf.normalized_path
                FROM source_chapters c
                JOIN source_chapter_sets cs ON cs.chapter_set_id = c.chapter_set_id
                JOIN source_files sf ON sf.source_file_id = cs.source_file_id
                WHERE c.chapter_id = ? AND sf.project_id = ? AND cs.is_current = 1
                """,
                (chapter_id, project_id),
            ).fetchone()
        if row is None:
            raise ApplicationError(
                code="SOURCE_CHAPTER_NOT_FOUND",
                message="没有找到当前版本中的该章节。",
                status_code=404,
            )
        return cast(sqlite3.Row, row)

    def create_anchor(
        self,
        project_id: str,
        chapter_id: str,
        start_offset: int,
        end_offset: int,
    ) -> dict[str, Any]:
        with self.database.reader() as connection:
            row = connection.execute(
                """
                SELECT c.version, c.start_offset, c.end_offset, sf.normalized_path
                FROM source_chapters c
                JOIN source_chapter_sets cs ON cs.chapter_set_id = c.chapter_set_id
                JOIN source_files sf ON sf.source_file_id = cs.source_file_id
                WHERE c.chapter_id = ? AND sf.project_id = ?
                """,
                (chapter_id, project_id),
            ).fetchone()
        if row is None:
            raise ApplicationError(
                code="SOURCE_CHAPTER_NOT_FOUND",
                message="没有找到该章节。",
                status_code=404,
            )
        text = Path(str(row["normalized_path"])).read_text(encoding="utf-8")
        chapter_text = text[int(row["start_offset"]) : int(row["end_offset"])]
        if start_offset < 0 or end_offset <= start_offset or end_offset > len(chapter_text):
            raise ApplicationError(
                code="INVALID_SOURCE_ANCHOR",
                message="来源锚点超出章节范围。",
                status_code=422,
            )
        excerpt = chapter_text[start_offset:end_offset]
        anchor_id = str(uuid7())
        excerpt_sha256 = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
        with self.database.writer() as connection:
            connection.execute(
                """
                INSERT INTO source_anchors(
                    anchor_id, chapter_id, chapter_version, start_offset,
                    end_offset, excerpt_sha256
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    anchor_id,
                    chapter_id,
                    int(row["version"]),
                    start_offset,
                    end_offset,
                    excerpt_sha256,
                ),
            )
        return {
            "anchor_id": anchor_id,
            "chapter_id": chapter_id,
            "chapter_version": int(row["version"]),
            "start_offset": start_offset,
            "end_offset": end_offset,
            "excerpt": excerpt,
            "excerpt_sha256": excerpt_sha256,
        }

    def _source_text(self, project_id: str, source_file_id: str) -> str:
        with self.database.reader() as connection:
            row = connection.execute(
                """
                SELECT normalized_path FROM source_files
                WHERE source_file_id = ? AND project_id = ?
                """,
                (source_file_id, project_id),
            ).fetchone()
        if row is None:
            raise ApplicationError(
                code="SOURCE_FILE_NOT_FOUND",
                message="没有找到该 TXT 源文件。",
                status_code=404,
            )
        return Path(str(row["normalized_path"])).read_text(encoding="utf-8")

    @staticmethod
    def _insert_chapters(
        connection: sqlite3.Connection,
        chapter_set_id: str,
        boundaries: list[ChapterBoundary],
        text: str,
    ) -> list[dict[str, Any]]:
        chapters: list[dict[str, Any]] = []
        for ordinal, boundary in enumerate(boundaries, start=1):
            chapter_id = str(uuid7())
            chapter_text = text[boundary.start_offset : boundary.end_offset]
            text_sha256 = hashlib.sha256(chapter_text.encode("utf-8")).hexdigest()
            connection.execute(
                """
                INSERT INTO source_chapters(
                    chapter_id, chapter_set_id, ordinal, title,
                    start_offset, end_offset, text_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chapter_id,
                    chapter_set_id,
                    ordinal,
                    boundary.title,
                    boundary.start_offset,
                    boundary.end_offset,
                    text_sha256,
                ),
            )
            chapters.append(
                {
                    "chapter_id": chapter_id,
                    "version": 1,
                    "ordinal": ordinal,
                    "title": boundary.title,
                    "start_offset": boundary.start_offset,
                    "end_offset": boundary.end_offset,
                    "character_count": len(chapter_text),
                    "text_sha256": text_sha256,
                }
            )
        return chapters

    @staticmethod
    def _write_bytes(path: Path, content: bytes) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with path.open("xb") as handle:
            os.chmod(path, 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _move_to_orphan(path: Path) -> None:
        if path.exists():
            os.replace(path, path.with_name(f".orphan-{path.name}"))


def detect_encoding_candidates(content: bytes) -> list[EncodingCandidate]:
    ordered_encodings: list[tuple[str, float]] = []
    if content.startswith(b"\xef\xbb\xbf"):
        ordered_encodings.append(("utf-8-sig", 1.0))
    else:
        try:
            content.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            pass
        else:
            ordered_encodings.append(("utf-8", 1.0))

    best = from_bytes(content).best()
    if best is not None and best.encoding:
        coherence = max(0.0, min(1.0, float(best.coherence)))
        ordered_encodings.append((best.encoding.lower(), max(0.55, coherence)))
    ordered_encodings.extend((("gb18030", 0.72), ("gbk", 0.65)))

    candidates: list[EncodingCandidate] = []
    seen: set[str] = set()
    for encoding, base_confidence in ordered_encodings:
        canonical = encoding.lower().replace("_", "-")
        if canonical in seen:
            continue
        seen.add(canonical)
        try:
            decoded = content.decode(encoding, errors="strict")
        except (LookupError, UnicodeDecodeError):
            continue
        if has_excessive_controls(decoded):
            continue
        cjk_ratio = calculate_cjk_ratio(decoded)
        confidence = base_confidence
        if canonical in {"gb18030", "gbk"} and cjk_ratio >= 0.2:
            confidence = max(confidence, 0.85)
        candidates.append(
            EncodingCandidate(
                encoding=encoding,
                confidence=round(confidence, 3),
                preview=normalize_text(decoded)[:PREVIEW_CHARACTERS],
                cjk_ratio=round(cjk_ratio, 3),
            )
        )
    candidates.sort(key=lambda item: item.confidence, reverse=True)
    return candidates


def normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")


def detect_chapters(text: str) -> list[ChapterBoundary]:
    matches = list(CHAPTER_PATTERN.finditer(text))
    if not matches:
        return [ChapterBoundary(title="全文", start_offset=0, end_offset=len(text))]

    boundaries: list[ChapterBoundary] = []
    if matches[0].start() > 0:
        boundaries.append(
            ChapterBoundary(
                title="正文前内容",
                start_offset=0,
                end_offset=matches[0].start(),
            )
        )
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        title = " ".join(match.group(0).strip().split())
        boundaries.append(ChapterBoundary(title=title, start_offset=match.start(), end_offset=end))
    validate_boundaries(boundaries, len(text))
    return boundaries


def validate_boundaries(boundaries: list[ChapterBoundary], text_length: int) -> None:
    if not boundaries:
        raise ApplicationError(
            code="INVALID_CHAPTER_BOUNDARIES",
            message="章节列表不能为空。",
            status_code=422,
        )
    expected_start = 0
    for boundary in boundaries:
        if not boundary.title.strip():
            raise ApplicationError(
                code="INVALID_CHAPTER_BOUNDARIES",
                message="章节标题不能为空。",
                status_code=422,
            )
        if boundary.start_offset != expected_start:
            raise ApplicationError(
                code="INVALID_CHAPTER_BOUNDARIES",
                message="章节边界必须连续覆盖全文。",
                status_code=422,
            )
        if boundary.end_offset <= boundary.start_offset or boundary.end_offset > text_length:
            raise ApplicationError(
                code="INVALID_CHAPTER_BOUNDARIES",
                message="章节边界超出正文范围。",
                status_code=422,
            )
        expected_start = boundary.end_offset
    if expected_start != text_length:
        raise ApplicationError(
            code="INVALID_CHAPTER_BOUNDARIES",
            message="章节边界必须完整覆盖全文。",
            status_code=422,
        )


def calculate_cjk_ratio(text: str) -> float:
    meaningful = [character for character in text if not character.isspace()]
    if not meaningful:
        return 0.0
    cjk = sum(
        1
        for character in meaningful
        if "\u3400" <= character <= "\u9fff" or "\uf900" <= character <= "\ufaff"
    )
    return cjk / len(meaningful)


def has_excessive_controls(text: str) -> bool:
    controls = sum(1 for character in text if ord(character) < 32 and character not in "\n\r\t\f")
    return controls > max(2, len(text) // 100)


def segment_story_beats(text: str, target_characters: int = 280) -> list[tuple[int, int]]:
    """Create deterministic, lossless source segments without calling an LLM."""
    segments: list[tuple[int, int]] = []
    line_start = 0
    for line in text.splitlines(keepends=True):
        content_length = len(line.rstrip("\r\n"))
        raw_content = line[:content_length]
        leading = len(raw_content) - len(raw_content.lstrip())
        trailing_end = len(raw_content.rstrip())
        start = line_start + leading
        end = line_start + trailing_end
        if start < end:
            segments.extend(_segment_story_beat_line(text, start, end, target_characters))
        line_start += len(line)
    if line_start < len(text):
        start = line_start + len(text[line_start:]) - len(text[line_start:].lstrip())
        end = len(text.rstrip())
        if start < end:
            segments.extend(_segment_story_beat_line(text, start, end, target_characters))
    return segments


def _segment_story_beat_line(
    text: str, start: int, end: int, target_characters: int
) -> list[tuple[int, int]]:
    if end - start <= target_characters * 2:
        return [(start, end)]
    pieces: list[tuple[int, int]] = []
    cursor = start
    while cursor < end:
        desired_end = min(cursor + target_characters, end)
        if desired_end < end:
            punctuation = max(
                text.rfind(mark, cursor + 1, min(desired_end + 80, end))
                for mark in ("。", "\uff01", "\uff1f", "!", "?", "…", "\uff1b", ";")
            )
            if punctuation >= cursor + target_characters // 2:
                desired_end = punctuation + 1
        pieces.append((cursor, desired_end))
        cursor = desired_end
    return pieces
