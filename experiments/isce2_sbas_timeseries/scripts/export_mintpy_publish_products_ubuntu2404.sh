#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo "Usage: $0 <mintpy-work-dir-wsl> <publish-dir-wsl> [group-key]" >&2
  exit 1
fi

MINTPY_WORK_DIR="$1"
PUBLISH_DIR="$2"
GROUP_KEY="${3:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MINTPY_RUNNER="${MINTPY_RUNNER:-$SCRIPT_DIR/run_mintpy_with_isce_ubuntu2404.sh}"
PUBLISH_BUILDER="$SCRIPT_DIR/build_mintpy_publish_bundle.py"

GEO_LAT_STEP="${GEO_LAT_STEP:--0.000185185}"
GEO_LON_STEP="${GEO_LON_STEP:-0.000185185}"
GEO_INTERP_METHOD="${GEO_INTERP_METHOD:-nearest}"

ASSETS_DIR="$PUBLISH_DIR/assets"
PREVIEW_DIR="$PUBLISH_DIR/preview"
METADATA_DIR="$PUBLISH_DIR/metadata"

mkdir -p "$ASSETS_DIR" "$PREVIEW_DIR" "$METADATA_DIR"

LOOKUP_FILE="$MINTPY_WORK_DIR/inputs/geometryRadar.h5"

echo "MintPy publish export"
echo "Work dir:     $MINTPY_WORK_DIR"
echo "Publish dir:  $PUBLISH_DIR"
echo "Lookup file:  $LOOKUP_FILE"
echo "Runner:       $MINTPY_RUNNER"
echo "Geo step:     $GEO_LAT_STEP, $GEO_LON_STEP"
echo "Interp:       $GEO_INTERP_METHOD"

for src in velocity.h5 temporalCoherence.h5 maskTempCoh.h5 timeseries.h5; do
  bash "$MINTPY_RUNNER" geocode.py \
    "$MINTPY_WORK_DIR/$src" \
    -l "$LOOKUP_FILE" \
    --lalo "$GEO_LAT_STEP" "$GEO_LON_STEP" \
    -i "$GEO_INTERP_METHOD" \
    --outdir "$ASSETS_DIR" \
    --update
done

bash "$MINTPY_RUNNER" save_gdal.py "$ASSETS_DIR/geo_velocity.h5" -d velocity -o "$ASSETS_DIR/velocity.tif"
bash "$MINTPY_RUNNER" save_gdal.py "$ASSETS_DIR/geo_temporalCoherence.h5" -d temporalCoherence -o "$ASSETS_DIR/temporalCoherence.tif"
bash "$MINTPY_RUNNER" save_gdal.py "$ASSETS_DIR/geo_maskTempCoh.h5" -d mask -o "$ASSETS_DIR/maskTempCoh.tif"

cp "$MINTPY_WORK_DIR/smallbaselineApp.cfg" "$METADATA_DIR/smallbaselineApp.cfg"
cp "$MINTPY_WORK_DIR/numTriNonzeroIntAmbiguity.png" "$PREVIEW_DIR/numTriNonzeroIntAmbiguity.png"

if [[ -n "$GROUP_KEY" ]]; then
  bash "$MINTPY_RUNNER" python "$PUBLISH_BUILDER" \
    --mintpy-work-dir "$MINTPY_WORK_DIR" \
    --publish-dir "$PUBLISH_DIR" \
    --group-key "$GROUP_KEY"
else
  bash "$MINTPY_RUNNER" python "$PUBLISH_BUILDER" \
    --mintpy-work-dir "$MINTPY_WORK_DIR" \
    --publish-dir "$PUBLISH_DIR"
fi
