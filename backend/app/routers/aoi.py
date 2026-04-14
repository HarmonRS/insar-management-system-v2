from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

from ..config import settings
from . import dependencies as deps

router = APIRouter()


@router.get("/aoi/regions/children")
async def list_aoi_region_children(parent_tree_id: Optional[str] = "1"):
    deps._load_region_index()
    parent = (parent_tree_id or "1").strip() or "1"
    children = (deps._REGION_CHILDREN_CACHE or {}).get(parent, [])
    return {
        "parent_tree_id": parent,
        "children": children,
    }


@router.get("/aoi/regions/{tree_id}/geometry")
async def get_aoi_region_geometry(tree_id: str):
    return deps._resolve_region_aoi_payload(tree_id)
