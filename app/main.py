import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.config import Settings, get_settings
from app.container import Container
from app.providers.gateway import ProviderGateway
from app.services import errors

_ERROR_STATUS: dict[type[errors.ServiceError], int] = {
    errors.NotFound: status.HTTP_404_NOT_FOUND,
    errors.NoEligibleLeg: status.HTTP_400_BAD_REQUEST,
    errors.InsufficientFunds: status.HTTP_402_PAYMENT_REQUIRED,
    errors.TooManyActiveTasks: status.HTTP_429_TOO_MANY_REQUESTS,
    errors.PaymentConflict: status.HTTP_409_CONFLICT,
}


def create_app(settings: Settings | None = None, gateway: ProviderGateway | None = None) -> FastAPI:
    settings = settings or get_settings()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = Container.build(settings, gateway)
        app.state.container = container
        watchdog = (
            asyncio.create_task(container.watchdog.run_forever()) if settings.watchdog_enabled else None
        )
        try:
            yield
        finally:
            if watchdog is not None:
                watchdog.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watchdog
            await container.aclose()

    app = FastAPI(title="Generation service", lifespan=lifespan)
    app.include_router(router)

    @app.exception_handler(errors.ServiceError)
    async def _service_error(_: Request, exc: errors.ServiceError) -> JSONResponse:
        code = _ERROR_STATUS.get(type(exc), status.HTTP_400_BAD_REQUEST)
        return JSONResponse({"detail": str(exc)}, status_code=code)

    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
