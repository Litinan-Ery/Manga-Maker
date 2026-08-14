from __future__ import annotations

from ....pages.models import PageDocument
from ....pages.service import PageService
from ..contracts import CreatePageRevisionCommandV1, PageVersionSnapshotV1


class LegacyCompositionFacade:
    """Anti-corruption adapter preserving the v0.2 page revision behavior."""

    def __init__(self, pages: PageService) -> None:
        self._pages = pages

    def create_page_revision(
        self,
        command: CreatePageRevisionCommandV1,
    ) -> PageVersionSnapshotV1:
        document = PageDocument.model_validate(command.document.model_dump(mode="json"))
        payload = self._pages.create_revision(
            command.project_id,
            command.page_id,
            document,
            expected_revision=command.expected_revision,
        )
        return PageVersionSnapshotV1.model_validate(payload)
