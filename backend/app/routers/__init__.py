from __future__ import annotations

from fastapi import APIRouter

from . import (
    ai,
    aoi,
    assets,
    auth,
    dinsar,
    dinsar_products,
    dinsar_production,
    flood,
    hazard,
    health,
    idl,
    landsar_lt1_production,
    license,
    logs,
    monitor,
    orbit,
    pairing,
    ps_products,
    result_deliveries,
    sbas_insar_products,
    radar,
    root_registry,
    sbas_insar_production,
    stats,
    task_batches,
    tasks_runtime,
    timeseries_production,
    tools,
    unpack,
    water,
    workflow,
)


def include_all_routers(router: APIRouter) -> None:
    """将所有子路由注册到给定的 APIRouter。"""
    router.include_router(health.router)
    router.include_router(auth.router)
    router.include_router(license.router)
    router.include_router(tasks_runtime.router)
    router.include_router(workflow.router)
    router.include_router(task_batches.router)
    router.include_router(tools.router)
    router.include_router(unpack.router)
    router.include_router(monitor.router)
    router.include_router(orbit.router)
    router.include_router(assets.router)
    router.include_router(root_registry.router)
    router.include_router(radar.router)
    router.include_router(aoi.router)
    router.include_router(pairing.router)
    router.include_router(dinsar.router)
    router.include_router(dinsar_products.router)
    router.include_router(result_deliveries.router)
    router.include_router(dinsar_production.router)
    router.include_router(landsar_lt1_production.router)
    router.include_router(sbas_insar_production.router)
    router.include_router(sbas_insar_products.router)
    router.include_router(timeseries_production.router)
    router.include_router(ps_products.router)
    router.include_router(ai.router)
    router.include_router(stats.router)
    router.include_router(idl.router)
    router.include_router(hazard.router)
    router.include_router(flood.router)
    router.include_router(water.router)
    router.include_router(logs.router)
