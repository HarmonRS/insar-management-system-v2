from __future__ import annotations

from fastapi import APIRouter, Depends

from .routers import include_all_routers
from .routers.dependencies import _require_auth, _require_license

router = APIRouter(dependencies=[Depends(_require_license), Depends(_require_auth)])
include_all_routers(router)
