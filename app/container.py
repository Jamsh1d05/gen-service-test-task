from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import Settings
from app.db import create_engine, create_sessionmaker
from app.providers.gateway import HttpProviderGateway, ProviderGateway
from app.services.dispatcher import Dispatcher
from app.services.generation import GenerationService
from app.services.orchestrator import Orchestrator
from app.services.payments import PaymentService
from app.services.queries import Queries
from app.services.watchdog import Watchdog


@dataclass
class Container:
    settings: Settings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    gateway: ProviderGateway
    orchestrator: Orchestrator
    dispatcher: Dispatcher
    generation: GenerationService
    payments: PaymentService
    queries: Queries
    watchdog: Watchdog

    @classmethod
    def build(cls, settings: Settings, gateway: ProviderGateway | None = None) -> "Container":
        engine = create_engine(settings)
        sessionmaker = create_sessionmaker(engine)
        gateway = gateway or HttpProviderGateway(settings.provider_urls, settings.provider_http_timeout)
        orchestrator = Orchestrator(sessionmaker, settings.provider_timeout)
        dispatcher = Dispatcher(orchestrator, gateway, settings.public_base_url)
        return cls(
            settings=settings,
            engine=engine,
            sessionmaker=sessionmaker,
            gateway=gateway,
            orchestrator=orchestrator,
            dispatcher=dispatcher,
            generation=GenerationService(sessionmaker, orchestrator, settings.max_active_tasks),
            payments=PaymentService(sessionmaker),
            queries=Queries(sessionmaker),
            watchdog=Watchdog(
                sessionmaker,
                orchestrator,
                dispatcher,
                settings.watchdog_interval,
                settings.watchdog_batch_size,
            ),
        )

    async def aclose(self) -> None:
        await self.dispatcher.aclose()
        await self.gateway.aclose()
        await self.engine.dispose()
