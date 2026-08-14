from __future__ import annotations

import copy
from typing import cast

from fastapi.testclient import TestClient

from backend.app.bootstrap.container import AppContainer
from backend.app.errors import ApplicationError
from backend.app.modules.composition.contracts import (
    CreatePageRevisionCommandV1,
    PageDocumentSnapshotV1,
    PageVersionSnapshotV1,
)
from backend.app.modules.composition.public import CompositionFacade
from backend.app.shared_kernel.canonical_json import canonical_sha256
from tests.contracts.port_harness import assert_composition_revision_port_contract
from tests.test_pages_api import prepare_page


class InMemoryCompositionFacade:
    def __init__(self, seed: PageVersionSnapshotV1) -> None:
        self._current = seed

    def create_page_revision(
        self, command: CreatePageRevisionCommandV1
    ) -> PageVersionSnapshotV1:
        current = self._current
        if command.expected_revision != current.page_revision:
            raise ApplicationError(
                "PAGE_REVISION_CONFLICT", "页面已被修改，请刷新后重试。", 409
            )
        if command.project_id != current.project_id or command.page_id != current.page_id:
            raise ApplicationError("PAGE_VERSION_NOT_FOUND", "没有找到该页面版本。", 404)
        if command.document == current.document:
            return current

        payload = current.model_dump(mode="json")
        payload.update(
            {
                "page_revision": current.page_revision + 1,
                "page_version_id": f"fake-version-{current.version + 1}",
                "version": current.version + 1,
                "parent_page_version_id": current.page_version_id,
                "document_sha256": canonical_sha256(command.document),
                "render_sha256": canonical_sha256(
                    {"fake_render_document": command.document.model_dump(mode="json")}
                ),
                "source_job_id": None,
                "document": command.document.model_dump(mode="json"),
            }
        )
        self._current = PageVersionSnapshotV1.model_validate(payload)
        return self._current


def test_composition_port_contract_matches_in_memory_and_legacy_sqlite_adapters(
    client: TestClient,
    session_headers: dict[str, str],
) -> None:
    _prepared, provider, page = prepare_page(client, session_headers)
    seed = PageVersionSnapshotV1.model_validate(page)
    revised_payload = copy.deepcopy(page["document"])
    revised_payload["text_layers"][0]["text"] = "新版本"
    revised = PageDocumentSnapshotV1.model_validate(revised_payload)
    container = cast(AppContainer, client.app.state.container)
    calls_before = provider.generation_calls

    adapters: tuple[CompositionFacade, ...] = (
        InMemoryCompositionFacade(seed),
        container.composition,
    )
    results = [
        assert_composition_revision_port_contract(adapter, seed, revised)
        for adapter in adapters
    ]

    assert all(len(result.document_sha256) == 64 for result in results)
    assert all(result.document == revised for result in results)
    assert provider.generation_calls == calls_before
