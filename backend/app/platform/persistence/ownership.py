from __future__ import annotations

from dataclasses import dataclass


class OwnershipRegistryError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TableOwner:
    table: str
    owner: str
    introduced_in: int = 1

    def __post_init__(self) -> None:
        if not self.table or not self.owner:
            raise OwnershipRegistryError("table and owner must be non-empty")
        if self.introduced_in < 1:
            raise OwnershipRegistryError("table schema version must be positive")


TABLE_OWNER_ENTRIES: tuple[TableOwner, ...] = (
    TableOwner("schema_migrations", "persistence"),
    TableOwner("projects", "project_source"),
    TableOwner("source_preflights", "project_source"),
    TableOwner("source_files", "project_source"),
    TableOwner("source_chapter_sets", "project_source"),
    TableOwner("source_chapters", "project_source"),
    TableOwner("source_anchors", "project_source"),
    TableOwner("audit_events", "observability"),
    TableOwner("recovery_runs", "recovery"),
    TableOwner("text_model_configs", "text_execution"),
    TableOwner("story_beat_sets", "adaptation"),
    TableOwner("story_beats", "adaptation"),
    TableOwner("storyboards", "adaptation"),
    TableOwner("storyboard_versions", "adaptation"),
    TableOwner("storyboard_approvals", "adaptation"),
    TableOwner("character_bibles", "world_bible"),
    TableOwner("character_bible_versions", "world_bible"),
    TableOwner("character_bible_approvals", "world_bible"),
    TableOwner("style_bibles", "world_bible"),
    TableOwner("style_bible_versions", "world_bible"),
    TableOwner("style_bible_approvals", "world_bible"),
    TableOwner("reference_assets", "world_bible"),
    TableOwner("continuity_ledgers", "world_bible"),
    TableOwner("continuity_ledger_versions", "world_bible"),
    TableOwner("continuity_approvals", "world_bible"),
    TableOwner("character_tag_bundles", "world_bible"),
    TableOwner("character_tag_bundle_versions", "world_bible"),
    TableOwner("character_tag_bundle_approvals", "world_bible"),
    TableOwner("prompt_bundles", "prompting"),
    TableOwner("prompt_bundle_versions", "prompting"),
    TableOwner("prompt_bundle_approvals", "prompting"),
    TableOwner("novelai_configs", "production"),
    TableOwner("generation_jobs", "production"),
    TableOwner("generation_job_items", "production"),
    TableOwner("generation_attempts", "production"),
    TableOwner("generation_specs", "production"),
    TableOwner("provider_execution_specs", "production", introduced_in=27),
    TableOwner("generation_approvals", "production", introduced_in=29),
    TableOwner("asset_versions", "production"),
    TableOwner("mask_assets", "production"),
    TableOwner("comic_pages", "composition"),
    TableOwner("page_versions", "composition"),
    TableOwner("asset_library_items", "asset_catalog"),
    TableOwner("export_revisions", "exporting"),
    TableOwner("export_files", "exporting"),
    TableOwner("package_import_preflights", "exporting"),
    TableOwner("book_production_plans", "book_workflow"),
    TableOwner("book_production_chapters", "book_workflow"),
    TableOwner("work_items", "durable_work", introduced_in=17),
    TableOwner("work_attempts", "durable_work", introduced_in=17),
    TableOwner("work_handler_receipts", "durable_work", introduced_in=17),
    TableOwner("worker_leases", "durable_work", introduced_in=18),
    TableOwner("outbox_project_sequences", "durable_work", introduced_in=19),
    TableOwner("outbox_events", "durable_work", introduced_in=19),
    TableOwner("handled_events", "durable_work", introduced_in=19),
    TableOwner("outbox_publish_attempts", "durable_work", introduced_in=20),
    TableOwner("recovery_reports", "recovery", introduced_in=21),
    TableOwner("recovery_findings", "recovery", introduced_in=21),
    TableOwner("recovery_repair_receipts", "recovery", introduced_in=21),
    TableOwner("artifact_versions", "lineage", introduced_in=22),
    TableOwner("artifact_dependencies", "lineage", introduced_in=22),
    TableOwner("invalidation_events", "lineage", introduced_in=22),
    TableOwner("invalidation_impacts", "lineage", introduced_in=22),
    TableOwner("page_layout_drafts", "layout", introduced_in=23),
    TableOwner("layout_approvals", "layout", introduced_in=23),
    TableOwner("dimension_selections", "layout", introduced_in=23),
    TableOwner("layout_command_receipts", "layout", introduced_in=24),
    TableOwner("layout_approval_dimension_selections", "layout", introduced_in=24),
)


def build_table_owner_registry(entries: tuple[TableOwner, ...]) -> dict[str, str]:
    result: dict[str, str] = {}
    for entry in entries:
        if entry.table in result:
            raise OwnershipRegistryError(
                f"table {entry.table!r} has duplicate owners {result[entry.table]!r} "
                f"and {entry.owner!r}"
            )
        result[entry.table] = entry.owner
    return result


TABLE_OWNERS: dict[str, str] = build_table_owner_registry(TABLE_OWNER_ENTRIES)
TABLE_SCHEMA_VERSIONS: dict[str, int] = {
    entry.table: entry.introduced_in for entry in TABLE_OWNER_ENTRIES
}


def owner_for_table(table: str) -> str:
    try:
        return TABLE_OWNERS[table]
    except KeyError as exc:
        raise OwnershipRegistryError(f"table {table!r} is not registered") from exc
