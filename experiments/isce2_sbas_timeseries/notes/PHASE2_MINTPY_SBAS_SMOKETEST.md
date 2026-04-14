# Phase 2 MintPy SBAS Smoke Test

Updated: 2026-04-05

Follow-up note:

- publish-style geocode/export continuation is now recorded separately in:
  - `notes/PHASE3_PUBLISH_EXPORT_SMOKETEST.md`

## Goal

Validate that the LT-1 sample stack can continue from `stripmapStack` interferogram products into MintPy SBAS outputs under `Ubuntu-24.04`.

## Sample

- group key:
  - `LT1A|STRIP1|HH|DESCENDING|E123.3_N46.1`
- dates:
  - `20250118`
  - `20250315`
  - `20250510`
  - `20250705`
  - `20250830`
- pair count:
  - `10`
- reference date:
  - `20250510`
- reference point:
  - `y/x = 1994,52`

## Successful Run

Successful SBAS smoke-test work directory:

- `/mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1/stack_work/mintpy_sbas_v5`

Successful WSL command:

```text
wsl -d Ubuntu-24.04 bash /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scripts/run_mintpy_sbas_smoketest_ubuntu2404.sh /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/configs/sample_smallbaseline_lt1_e123p3_n46p1.cfg /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1/stack_work/mintpy_sbas_v5
```

This run completed through:

- `load_data`
- `modify_network`
- `reference_point`
- `quick_overview`
- `invert_network`
- `reference_date`
- `velocity`

Disabled for this first offline smoke test:

- unwrap-error correction
- solid-earth-tide correction
- ionosphere correction
- troposphere correction
- deramp
- topographic residual correction
- geocode

## Output Snapshot

Generated under `mintpy_sbas_v5/`:

- `timeseries.h5`
  - size: `82576752` bytes
- `velocity.h5`
  - size: `83086408` bytes
- `temporalCoherence.h5`
  - size: `16632424` bytes
- `maskTempCoh.h5`
  - size: `4136792` bytes
- `avgSpatialCoh.h5`
  - size: `16631784` bytes
- `numTriNonzeroIntAmbiguity.h5`
  - size: `16632000` bytes
- `numTriNonzeroIntAmbiguity.png`
  - size: `232878` bytes

Quality summary from the successful run:

- strict valid pixels for inversion:
  - `1219001 / 4076199`
  - `29.91%`
- reliable pixels in `maskTempCoh.h5` with threshold `0.7`:
  - `62987`

## Required Runtime Decisions

### 1. Keep MintPy separate from ISCE2

- keep stack processing in WSL conda env:
  - `isce2`
- keep MintPy in WSL conda env:
  - `mintpy`

Reason:

- avoids mutating the already working ISCE2 processing env on the development machine

### 2. Bridge only the top-level `isce` package

Required helper:

- `scripts/run_mintpy_with_isce_ubuntu2404.sh`

Current rule:

- do not add the entire `isce2` `site-packages` into `PYTHONPATH`
- only bridge the top-level `isce` package into a cache directory

Reason:

- adding the whole `site-packages` caused MintPy to import `h5py` from the wrong env and fail during `load_data`

### 3. Do not load `wrapPhase` for this LT-1 smoke test

Current config rule:

- `mintpy.load.intFile = None`

Reason:

- loading `filt_*.int` into MintPy `wrapPhase` caused HDF5 type-conversion failure during `load_data`

### 4. Build a strict `maskAllValid.h5` before inversion

Required helper:

- `scripts/create_mintpy_all_ifgram_mask.py`

Current rule:

- keep only pixels that are finite and non-zero in all unwrapped interferograms
- also require non-zero connected components in all interferograms

Reason:

- this reduces unstable partial-network pixels before SBAS inversion

### 5. Use the repo-local patched launcher

Required helper:

- `scripts/run_smallbaselineApp_patched.py`

Current workaround:

- patch `mintpy.ifgram_inversion.estimate_timeseries()` at runtime
- coerce shape-`(1,)` inversion-quality output into a scalar for the single-pixel partial-network branch

Reason:

- MintPy `1.6.2` hit a `ValueError: setting an array element with a sequence`
- failure point:
  - `mintpy/ifgram_inversion.py`
  - partial-network pixel branch inside `run_ifgram_inversion_patch()`

Current judgment:

- this is a MintPy runtime issue in the current environment
- it is better to keep the workaround in repo-local launcher code than silently editing the third-party env

## Current Boundary

This smoke test now confirms:

- LT-1 `stripmapStack` outputs can be loaded by MintPy in the dedicated `mintpy` env
- the LT-1 sample stack can be inverted into radar-coordinate `timeseries.h5`
- the LT-1 sample stack can generate radar-coordinate `velocity.h5`
- the current repo-local workaround chain is reproducible in `Ubuntu-24.04`

This smoke test does not yet confirm:

- geocoded SBAS exports
- atmospheric or DEM-residual correction quality
- product publishing into backend `psinsar` catalog
- frontend rendering of published SBAS products

## System Embedding Implications

The current experiment suggests the future backend runtime contract should be:

1. Run ISCE2 stack workflow in WSL `isce2`.
2. Run MintPy through the repo-controlled bridge runner instead of calling upstream `smallbaselineApp.py` directly.
3. Generate a strict inversion mask after `load_data`.
4. Persist the following files as first-class workflow artifacts:
   - `timeseries.h5`
   - `velocity.h5`
   - `temporalCoherence.h5`
   - `maskTempCoh.h5`
   - `numTriNonzeroIntAmbiguity.h5`
   - `numTriNonzeroIntAmbiguity.png`
5. Convert these into a stable publish manifest before catalog registration.

Current sample publish-manifest draft:

- `configs/sample_psinsar_manifest_lt1_e123p3_n46p1.json`
