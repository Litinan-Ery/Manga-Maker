from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.bootstrap.container import AppContainer, LegacyCompatibilityBindings
from backend.app.bootstrap.legacy import (
    LEGACY_APP_STATE_ALLOWLIST,
    LEGACY_BINDING_DELETE_WHEN,
    LEGACY_BINDING_OWNER,
)
from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.modules.layout.sqlite import SQLiteLayoutStore
from backend.app.modules.lineage.sqlite import SQLiteLineageStore
from backend.app.platform.durable_work.outbox import SQLiteOutboxStore
from backend.app.platform.durable_work.sqlite import SQLiteDurableWorkUnitOfWork
from backend.app.platform.durable_work.worker import DurableWorker
from backend.app.platform.recovery.coordinator import RecoveryCoordinator
from backend.app.shared_kernel import ArtifactRef, Sha256, SystemClock, Uuid7IdFactory

ROOT = Path(__file__).resolve().parents[2]


def app_state_values(client: TestClient) -> dict[str, Any]:
    value = vars(client.app.state).get("_state")
    assert isinstance(value, dict)
    return value


def test_create_app_installs_typed_container_and_exact_legacy_seam(tmp_path: Path) -> None:
    app = create_app(Settings(app_data_dir=tmp_path / "app-data", environment="test"))
    with TestClient(app) as client:
        state = app_state_values(client)
        container = state["container"]
        assert isinstance(container, AppContainer)
        assert isinstance(container.legacy, LegacyCompatibilityBindings)
        assert isinstance(container.durable_work, SQLiteDurableWorkUnitOfWork)
        assert isinstance(container.durable_worker, DurableWorker)
        assert isinstance(container.outbox, SQLiteOutboxStore)
        assert isinstance(container.recovery_coordinator, RecoveryCoordinator)
        assert isinstance(container.lineage, SQLiteLineageStore)
        assert isinstance(container.layout, SQLiteLayoutStore)
        assert set(state) == {*LEGACY_APP_STATE_ALLOWLIST, "container"}
        for name in LEGACY_APP_STATE_ALLOWLIST:
            expected = getattr(container, name, None)
            if expected is None:
                expected = getattr(container.legacy, name)
            assert state[name] is expected
        assert container.database.schema_version() == 30
        assert client.get("/health").status_code == 200

    assert container.durable_worker.stopped
    assert LEGACY_BINDING_OWNER == "MM-026"
    assert "allowlist is empty" in LEGACY_BINDING_DELETE_WHEN


def test_container_is_immutable_and_main_has_no_service_construction(tmp_path: Path) -> None:
    app = create_app(Settings(app_data_dir=tmp_path / "app-data", environment="test"))
    container = vars(app.state)["_state"]["container"]
    with pytest.raises(FrozenInstanceError):
        container.database = None

    source = inspect.getsource(__import__("backend.app.main", fromlist=["create_app"]))
    assert "Service(" not in source
    assert "build_app_container" not in source
    assert "create_application" in source


def test_shared_kernel_primitives_are_business_neutral_and_strict() -> None:
    artifact = ArtifactRef(
        artifact_type="page_layout_draft",
        artifact_id=Uuid7IdFactory().new(),
        version=1,
        content_sha256=Sha256.digest(b"fixture"),
        schema_version="1.0",
    )

    assert artifact.artifact_id.version == 7
    assert len(str(artifact.content_sha256)) == 64
    assert SystemClock().now().tzinfo is not None
    with pytest.raises(ValueError, match="lowercase hexadecimal"):
        Sha256("A" * 64)


def test_required_module_platform_and_workflow_skeletons_exist() -> None:
    required = (
        "backend/app/bootstrap",
        "backend/app/shared_kernel",
        "backend/app/platform/persistence",
        "backend/app/platform/durable_work",
        "backend/app/platform/file_store",
        "backend/app/platform/security",
        "backend/app/platform/observability",
        "backend/app/modules/project_source",
        "backend/app/modules/text_execution",
        "backend/app/modules/adaptation",
        "backend/app/modules/world_bible",
        "backend/app/modules/layout",
        "backend/app/modules/prompting",
        "backend/app/modules/production",
        "backend/app/modules/review",
        "backend/app/modules/composition",
        "backend/app/modules/asset_catalog",
        "backend/app/modules/exporting",
        "backend/app/modules/lineage",
        "backend/app/workflows/chapter_production",
        "backend/app/workflows/book_production",
    )
    for relative in required:
        directory = ROOT / relative
        assert directory.is_dir(), relative
        assert (directory / "__init__.py").is_file(), relative
