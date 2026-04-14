# Phase 0 Sample Stack Selection

Updated: 2026-04-03

## Selected baseline sample

Current baseline sample stack:

- group key:
  - `LT1A|STRIP1|HH|DESCENDING|E123.3_N46.1`
- manifest:
  - `experiments/isce2_sbas_timeseries/configs/sample_stack_e123p3_n46p1.json`

## Sample summary

- satellite:
  - `LT1A`
- mode:
  - `STRIP1`
- polarization:
  - `HH`
- orbit direction:
  - `DESCENDING`
- scene count:
  - `5`
- dates:
  - `20250118`
  - `20250315`
  - `20250510`
  - `20250705`
  - `20250830`
- recommended reference date:
  - `20250510`
- receiving stations observed:
  - `SYC`
  - `KSC`

## Why this sample is useful

- It already satisfies a minimal SBAS smoke-test stack size.
- All scenes share the same:
  - satellite
  - imaging mode
  - polarization
  - orbit direction
  - tile key
- The dates are evenly spaced enough to act as a first time-series experiment set.

## Important adjacent-tile signal

This sample is not isolated.

The same date sequence also appears in multiple neighboring descending tiles, including:

- `E123.5_N46.6`
- `E123.6_N47.0`
- `E123.8_N47.5`
- `E123.9_N48.0`
- `E124.5_N49.9`
- `E124.7_N50.3`
- `E124.8_N50.8`
- `E125.0_N51.3`
- `E125.1_N51.8`
- `E125.3_N52.2`
- `E125.5_N52.7`

These neighboring tiles share the same 5 acquisition dates:

- `20250118`
- `20250315`
- `20250510`
- `20250705`
- `20250830`

## Implication

This strongly suggests the data pool contains a larger repeated strip-family, not just isolated scenes.

Recommended experiment order:

1. Start with one tile-level stack smoke test using `E123.3_N46.1`.
2. If stack-prep works, expand to multiple adjacent tiles with the same date family.
3. Only after that, test a wider strip or mosaic strategy.

## Current risk judgment

The main remaining uncertainty is still not scene selection.

The main uncertainty is:

- how to convert LT-1 per-scene `tiff + meta.xml` folders
- into a stack layout and sensor input form acceptable to the ISCE2 stripmap stack workflow
