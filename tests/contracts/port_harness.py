from __future__ import annotations

from backend.app.errors import ApplicationError
from backend.app.modules.composition.contracts import (
    CreatePageRevisionCommandV1,
    PageDocumentSnapshotV1,
    PageVersionSnapshotV1,
)
from backend.app.modules.composition.public import CompositionFacade


def assert_composition_revision_port_contract(
    facade: CompositionFacade,
    seed: PageVersionSnapshotV1,
    revised_document: PageDocumentSnapshotV1,
) -> PageVersionSnapshotV1:
    """Exercise the same observable contract against fake and real adapters."""

    command = CreatePageRevisionCommandV1(
        project_id=seed.project_id,
        page_id=seed.page_id,
        expected_revision=seed.page_revision,
        document=revised_document,
    )
    created = facade.create_page_revision(command)
    assert created.page_version_id != seed.page_version_id
    assert created.parent_page_version_id == seed.page_version_id
    assert created.version == seed.version + 1
    assert created.page_revision == seed.page_revision + 1
    assert created.document == revised_document
    assert created.external_requests_started == 0

    duplicate = facade.create_page_revision(
        CreatePageRevisionCommandV1(
            project_id=seed.project_id,
            page_id=seed.page_id,
            expected_revision=created.page_revision,
            document=revised_document,
        )
    )
    assert duplicate == created

    try:
        facade.create_page_revision(command)
    except ApplicationError as exc:
        assert exc.code == "PAGE_REVISION_CONFLICT"
        assert exc.status_code == 409
    else:  # pragma: no cover - produces a direct contract failure
        raise AssertionError("stale page revision must fail with PAGE_REVISION_CONFLICT")
    return created
