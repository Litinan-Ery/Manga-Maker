from __future__ import annotations

from dataclasses import dataclass

from ..adaptation.service import AdaptationService
from ..bibles.service import BibleService
from ..book.service import BookProductionService
from ..config import Settings
from ..continuity.service import ContinuityService
from ..database import Database
from ..exports.service import ExportService
from ..generation.assets import AssetStore
from ..generation.executor import GenerationExecutor
from ..generation.queue import GenerationQueueService
from ..generation.revisions import RevisionService
from ..ingestion.txt import TxtIngestionService
from ..library.service import AssetLibraryService
from ..modules.adaptation.public import AdaptationFacade
from ..modules.composition.public import CompositionFacade
from ..modules.layout.public import LayoutFacade
from ..modules.lineage.public import LineageFacade
from ..novelai.service import NovelAIService
from ..pages.service import PageService
from ..platform.durable_work.outbox import SQLiteOutboxStore
from ..platform.durable_work.sqlite import SQLiteDurableWorkUnitOfWork
from ..platform.durable_work.worker import DurableWorker
from ..platform.recovery.coordinator import RecoveryCoordinator
from ..projects import ProjectService
from ..prompting.service import PromptingService
from ..recovery import RecoveryService
from ..safety import SecretScanner
from ..security import LocalSession
from ..vault import CredentialVault


@dataclass(frozen=True, slots=True)
class LegacyCompatibilityBindings:
    """Explicit v0.2 services kept while use cases migrate behind module facades."""

    projects: ProjectService
    ingestion: TxtIngestionService
    adaptation: AdaptationService
    bibles: BibleService
    prompting: PromptingService
    continuity: ContinuityService
    novelai: NovelAIService
    generation_queue: GenerationQueueService
    book_production: BookProductionService
    asset_store: AssetStore
    pages: PageService
    asset_library: AssetLibraryService
    revisions: RevisionService
    exports: ExportService
    recovery: RecoveryService
    generation_executor: GenerationExecutor


@dataclass(frozen=True, slots=True)
class AppContainer:
    """All concrete runtime dependencies, constructed only in the composition root."""

    settings: Settings
    database: Database
    vault: CredentialVault
    local_session: LocalSession
    secret_scanner: SecretScanner
    durable_work: SQLiteDurableWorkUnitOfWork
    durable_worker: DurableWorker
    outbox: SQLiteOutboxStore
    recovery_coordinator: RecoveryCoordinator
    lineage: LineageFacade
    layout: LayoutFacade
    adaptation_facade: AdaptationFacade
    composition: CompositionFacade
    legacy: LegacyCompatibilityBindings
