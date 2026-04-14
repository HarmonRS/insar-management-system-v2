# Phase 0 Stack Findings

Updated: 2026-04-03

## Confirmed

- Official stack tooling exists in the `isce2` env:
  - `/home/administrator/miniconda3/envs/isce2/share/isce2/stripmapStack`
  - `/home/administrator/miniconda3/envs/isce2/share/isce2/topsStack`
- `stackStripMap.py` exists and is the stripmap stack entry point.
- `prepStripmap4timeseries.py` exists in the official `stripmapStack` toolset.
- `stackStripMap.py` expects:
  - an SLC root directory via `-s/--slc_directory`
  - a DEM via `-d/--dem`
  - an optional reference date via `-m/--reference_date`
  - temporal and baseline thresholds
- The script scans date subdirectories under the SLC root.
- Default behavior looks for `<date>.raw` inside each acquisition directory.
- With `--nofocus`, it instead looks for `<date>.slc`.
- Deeper code inspection confirms:
  - `topo.py` opens `<date>/data`
  - `geo2rdr.py` opens each secondary `<date>/data`
  - `refineSecondaryTiming` uses both `<date>.slc` and the acquisition directory as metadata roots

## Important implication

Official stack processing expects a stack-style input layout such as:

```text
SLC/
  YYYYMMDD/
    YYYYMMDD.raw
```

or, when data are already focused:

```text
SLC/
  YYYYMMDD/
    YYYYMMDD.slc
    YYYYMMDD.slc.xml
    data
```

This is different from the current repository's custom LT-1 single-pair production flow.

## Time-series bridge signal

- `prepStripmap4timeseries.py` takes:
  - pair/interferogram directories
  - baseline directory
  - geometry directory
  - shelve metadata directory
- The script writes `.rsc` sidecars and explicitly references `pysar`-style downstream usage.

This is useful because it confirms the official stripmap stack toolset already contains a bridge from stack outputs toward time-series preparation.
The weak point is still the LT-1 stack input/preparation stage, not the existence of a downstream time-series bridge.

## Existing repo bridge signal

The repository's current LT-1 single-pair pipeline already proves one important thing:

- `stripmapApp.py` can be driven with:
  - `sensor name = LUTAN1`
  - direct `tiff` input path
  - direct `orbitFile` XML path

See:

- `backend/app/isce2_pipeline/run_lt1_dinsar_pipeline.py`

The generated XML writes:

- `Reference -> tiff`
- `Reference -> orbitFile`
- `Secondary -> tiff`
- `Secondary -> orbitFile`

This suggests a promising adapter direction:

- do not try to pretend LT-1 is ALOS or another officially prepared raw sensor
- instead, explore generating LT-1-aware stack configs directly from:
  - scene `tiff`
  - scene `meta.xml`
  - converted orbit XML

That does not prove the official stack driver will accept this without modification.
But it is the strongest current indication for how a custom LT-1 `stack-prep` layer should be shaped.

## LT-1 / LUTAN1 signal so far

- ISCE core does include a `Lutan1.py` sensor module.
- But no direct `lutan` match was found in the `stripmapStack` helper scripts.
- The `stripmapStack` README examples and preparation hints mention:
  - `prepRawALOS.py`
  - `prepRawSensor.py`
- The README explicitly states automatic raw-data preparation support is currently oriented to:
  - ALOS
  - CSK
- `prepRawSensors.py` automatic raw detection currently covers:
  - Envisat
  - ERS CEOS
  - ERS ENV
  - ALOS1
  - CSK
- `prepSlcSensors.py` automatic SLC detection currently covers:
  - Envisat
  - ALOS1
  - CSK
  - RSAT2
  - TSX/TDX
- No LT-1 or LUTAN1 hook was found in these official stack preparation scripts.

## Interim conclusion

Current evidence suggests:

- ISCE2 core can parse LT-1/LUTAN1 at the sensor level.
- Official `stripmapStack` tooling is present.
- But the official stack preparation helpers do not currently advertise LT-1/LUTAN1 support.
- There is no direct evidence yet that LT-1 can be fed into the official stack helpers without an adapter step.
- `--nofocus` does not remove the need for acquisition metadata preparation.
  - It still needs a per-date `data` shelve and an ISCE-style `.slc` image.

This means the working assumption should be:

- `LT-1 stack via official stripmapStack` is possible but unproven
- an LT-1-specific stack preparation or conversion layer is required unless an existing hidden tool can materialize `data` + `.slc` directly for LUTAN1

## Next checks

1. Implement a dry-run LT-1 stack-prep workspace generator against the selected sample manifest.
2. Design a scene materializer that transforms one LT-1 scene into:
   - `YYYYMMDD.slc`
   - `YYYYMMDD.slc.xml`
   - `data`
3. Decide whether that materializer should:
   - call ISCE/LUTAN1 directly, or
   - reuse parts of the existing pair pipeline
4. After materialization is proven, run `stackStripMap.py --nofocus` on one tile-level stack
