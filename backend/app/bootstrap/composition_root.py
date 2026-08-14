from __future__ import annotations

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
from ..modules.composition.adapters.legacy import LegacyCompositionFacade
from ..modules.layout.sqlite import SQLiteLayoutStore
from ..modules.lineage.sqlite import SQLiteLineageStore
from ..novelai.service import NovelAIService
from ..pages.service import PageService
from ..platform.durable_work.handlers import HandlerRegistry
from ..platform.durable_work.outbox import SQLiteOutboxStore
from ..platform.durable_work.recovery_probe import DurableRuntimeIntegrityProbe
from ..platform.durable_work.retry import ExponentialBackoffPolicy
from ..platform.durable_work.sqlite import SQLiteDurableWorkUnitOfWork
from ..platform.durable_work.wakeup import InProcessWorkerWakeup
from ..platform.durable_work.worker import DurableWorker
from ..platform.recovery.coordinator import RecoveryCoordinator
from ..projects import ProjectService
from ..prompting.service import PromptingService
from ..recovery import RecoveryService
from ..safety import SecretScanner
from ..security import LocalSession
from ..shared_kernel import SystemClock, Uuid7IdFactory
from ..vault import CredentialVault
from .container import AppContainer, LegacyCompatibilityBindings
from .legacy_adapters import LegacyAdaptationFacade


def build_app_container(settings: Settings) -> AppContainer:
    """Construct every real adapter and legacy service at the single composition root."""

    settings.ensure_directories()
    database = Database(settings.database_path)
    clock = SystemClock()
    id_factory = Uuid7IdFactory()
    durable_work = SQLiteDurableWorkUnitOfWork(database, clock=clock, id_factory=id_factory)
    outbox = SQLiteOutboxStore(database, clock=clock, id_factory=id_factory)
    recovery_coordinator = RecoveryCoordinator(
        database,
        probes=(DurableRuntimeIntegrityProbe(clock, id_factory),),
        clock=clock,
        id_factory=id_factory,
    )
    lineage = SQLiteLineageStore(database, clock=clock, id_factory=id_factory)
    layout = SQLiteLayoutStore(
        database,
        settings.projects_dir,
        clock=clock,
        id_factory=id_factory,
    )
    durable_worker = DurableWorker(
        owner="local-runtime",
        unit_of_work=durable_work,
        handlers=HandlerRegistry(),
        clock=clock,
        id_factory=id_factory,
        retry_policy=ExponentialBackoffPolicy(),
        wakeup=InProcessWorkerWakeup(),
    )
    vault = CredentialVault(settings.vault_path)
    local_session = LocalSession.create()
    projects = ProjectService(database, settings.projects_dir)
    ingestion = TxtIngestionService(database, projects)
    adaptation = AdaptationService(database, ingestion, vault)
    adaptation_facade = LegacyAdaptationFacade(adaptation)
    bibles = BibleService(database, projects, adaptation)
    prompting = PromptingService(database, adaptation, bibles, layout, lineage)
    continuity = ContinuityService(database)
    novelai = NovelAIService(database, vault)
    generation_queue = GenerationQueueService(database, bibles, prompting)
    book_production = BookProductionService(database, generation_queue)
    asset_store = AssetStore(database, generation_queue, lineage)
    pages = PageService(database)
    composition = LegacyCompositionFacade(pages)
    asset_library = AssetLibraryService(database)
    revisions = RevisionService(database, generation_queue, pages)
    secret_scanner = SecretScanner(vault)
    exports = ExportService(database, projects, secret_scanner)
    recovery = RecoveryService(
        database,
        projects,
        generation_queue,
        exports,
        vault,
        book_production,
    )
    generation_executor = GenerationExecutor(
        database,
        generation_queue,
        vault,
        asset_store,
        revision_finalizer=revisions,
    )
    legacy = LegacyCompatibilityBindings(
        projects=projects,
        ingestion=ingestion,
        adaptation=adaptation,
        bibles=bibles,
        prompting=prompting,
        continuity=continuity,
        novelai=novelai,
        generation_queue=generation_queue,
        book_production=book_production,
        asset_store=asset_store,
        pages=pages,
        asset_library=asset_library,
        revisions=revisions,
        exports=exports,
        recovery=recovery,
        generation_executor=generation_executor,
    )
    return AppContainer(
        settings=settings,
        database=database,
        vault=vault,
        local_session=local_session,
        secret_scanner=secret_scanner,
        durable_work=durable_work,
        durable_worker=durable_worker,
        outbox=outbox,
        recovery_coordinator=recovery_coordinator,
        lineage=lineage,
        layout=layout,
        adaptation_facade=adaptation_facade,
        composition=composition,
        legacy=legacy,
    )
