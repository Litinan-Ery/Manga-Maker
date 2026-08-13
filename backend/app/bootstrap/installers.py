from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Protocol

from fastapi import FastAPI

from ..platform.recovery.contracts import RecoveryTrigger
from .container import AppContainer
from .legacy import install_legacy_compatibility_bindings


class ModuleInstaller(Protocol):
    name: ClassVar[str]

    def install(self, app: FastAPI, container: AppContainer) -> None: ...

    async def start(self, container: AppContainer) -> None: ...

    async def stop(self, container: AppContainer) -> None: ...


@dataclass(frozen=True, slots=True)
class RuntimeLifecycleInstaller:
    name: ClassVar[str] = "runtime_lifecycle"

    def install(self, app: FastAPI, container: AppContainer) -> None:
        del app, container

    async def start(self, container: AppContainer) -> None:
        container.database.migrate()
        container.recovery_coordinator.run(RecoveryTrigger.STARTUP)
        container.legacy.recovery.reconcile_startup()

    async def stop(self, container: AppContainer) -> None:
        container.durable_worker.stop()
        await container.legacy.generation_executor.shutdown()
        container.vault.lock()


@dataclass(frozen=True, slots=True)
class LegacyCompatibilityInstaller:
    name: ClassVar[str] = "legacy_v02_compatibility"

    def install(self, app: FastAPI, container: AppContainer) -> None:
        install_legacy_compatibility_bindings(app, container)

    async def start(self, container: AppContainer) -> None:
        del container

    async def stop(self, container: AppContainer) -> None:
        del container


def default_module_installers() -> tuple[ModuleInstaller, ...]:
    return RuntimeLifecycleInstaller(), LegacyCompatibilityInstaller()
