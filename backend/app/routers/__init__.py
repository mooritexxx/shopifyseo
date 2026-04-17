"""API routers."""
from contextlib import contextmanager
from typing import Generator

from fastapi import HTTPException, status


@contextmanager
def field_regen_errors() -> Generator[None, None, None]:
    """Translate ValueError/RuntimeError from field-regeneration calls into HTTP 400/500."""
    try:
        yield
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
