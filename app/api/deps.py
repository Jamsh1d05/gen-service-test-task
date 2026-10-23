from typing import Annotated, TypeVar

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError

from app import security
from app.container import Container

M = TypeVar("M", bound=BaseModel)


def get_container(request: Request) -> Container:
    return request.app.state.container


ContainerDep = Annotated[Container, Depends(get_container)]


async def signed_body(
    request: Request,
    container: ContainerDep,
    x_signature: Annotated[str | None, Header()] = None,
) -> bytes:
    body = await request.body()
    if not security.verify(body, x_signature, container.settings.webhook_secret):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid signature")
    return body


SignedBody = Annotated[bytes, Depends(signed_body)]


def parse_body(model: type[M], body: bytes) -> M:
    try:
        return model.model_validate_json(body)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc
