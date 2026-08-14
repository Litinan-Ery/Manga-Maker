from __future__ import annotations

import copy
import inspect
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.api import pages as pages_api
from backend.app.modules.composition.contracts import PageDocumentSnapshotV1
from backend.app.modules.composition.public import CompositionFacade
from tests.test_pages_api import prepare_page

ROOT = Path(__file__).resolve().parents[3]


def test_page_revision_route_uses_facade_with_legacy_behavior(
    client: TestClient,
    session_headers: dict[str, str],
) -> None:
    prepared, provider, page = prepare_page(client, session_headers)
    project_id = prepared["project_id"]
    endpoint = f"/api/v1/projects/{project_id}/pages/{page['page_id']}/versions"
    calls_before = provider.generation_calls
    revised_document = copy.deepcopy(page["document"])
    revised_document["text_layers"][0]["text"] = "新版本"

    created_response = client.post(
        endpoint,
        headers=session_headers,
        json={
            "expected_revision": page["page_revision"],
            "document": revised_document,
        },
    )
    assert created_response.status_code == 201, created_response.text
    created = created_response.json()
    assert created["version"] == 2
    assert created["parent_page_version_id"] == page["page_version_id"]
    assert "contract_version" not in created

    duplicate = client.post(
        endpoint,
        headers=session_headers,
        json={
            "expected_revision": created["page_revision"],
            "document": created["document"],
        },
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["page_version_id"] == created["page_version_id"]

    conflict = client.post(
        endpoint,
        headers=session_headers,
        json={"expected_revision": page["page_revision"], "document": created["document"]},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "PAGE_REVISION_CONFLICT"
    assert provider.generation_calls == calls_before

    container = vars(client.app.state)["_state"]["container"]
    facade: CompositionFacade = container.composition
    assert facade is not None


def test_composition_contract_is_frozen_and_route_has_no_service_lookup(
    client: TestClient,
    session_headers: dict[str, str],
) -> None:
    _prepared, _provider, page = prepare_page(client, session_headers)
    document = PageDocumentSnapshotV1.model_validate(page["document"])

    with pytest.raises(ValidationError, match="Instance is frozen"):
        document.page_number = 999
    assert isinstance(document.panels, tuple)
    assert "request.app.state" not in inspect.getsource(pages_api.create_page_revision)


def test_each_business_module_exposes_public_contract_files() -> None:
    modules = (
        "project_source",
        "text_execution",
        "adaptation",
        "world_bible",
        "layout",
        "prompting",
        "production",
        "review",
        "composition",
        "asset_catalog",
        "exporting",
        "lineage",
    )
    for module in modules:
        root = ROOT / "backend" / "app" / "modules" / module
        assert (root / "contracts.py").is_file(), module
        assert (root / "public.py").is_file(), module
