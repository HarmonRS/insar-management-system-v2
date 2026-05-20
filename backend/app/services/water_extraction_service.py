"""Water extraction processors for the flood-analysis pipeline."""
from __future__ import annotations

from typing import Any


def run_otsu_water_extraction(
    *,
    input_path: str,
    output_dir: str,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Run the fast Otsu water-extraction processor.

    This wraps the legacy implementation while exposing the terminology used by
    the flood pipeline: extraction, processor and threshold_value.
    """
    from .water_detect_service import run_water_detection

    result = run_water_detection(
        geo_tiff_path=input_path,
        output_dir=output_dir,
        job_id=job_id,
    )
    result["processor"] = "otsu"
    if "threshold_value" not in result and "otsu_threshold_db" in result:
        result["threshold_value"] = result.get("otsu_threshold_db")
    return result


def run_envi_water_extraction(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Placeholder for the future ENVI/SARscape precise extractor."""
    raise NotImplementedError("ENVI/SARscape water extraction is not wired yet")
