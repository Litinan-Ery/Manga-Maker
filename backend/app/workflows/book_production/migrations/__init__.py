from pathlib import Path

from ....platform.persistence.migrations import RegisteredMigration

BOOK_WORKFLOW_MIGRATIONS = (
    RegisteredMigration(
        version=34,
        owner="book_workflow",
        name="workflow_context_checkpoints",
        statements=Path(__file__).with_name("0034_context_checkpoints.sql").read_text("utf-8"),
        source_path="backend/app/workflows/book_production/migrations/0034_context_checkpoints.sql",
    ),
)
