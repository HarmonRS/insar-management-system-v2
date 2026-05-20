# Gamma IPTA LT1 SBAS Trial Runbook

Date: 2026-05-18

## Goal

Validate whether the current local LT1 data pool and WSL Gamma installation can produce one usable SBAS/IPTA trial result with the official Gamma toolchain.

This is not yet a production integration design. The immediate goal is to run one conservative stack, inspect failures and product quality, then decide what should be productized in the system.

Actual trial root used in this repository:

```text
D:\Code\Insar_management_system_v2\backend\runtime\gamma_ipta_trials\lt1b_r114_e1312_n438_20240516_20251002
```

## Current Environment

- WSL distro configured by the project: `Ubuntu-24.04`.
- Gamma install found in WSL: `/usr/local/GAMMA_SOFTWARE-20240627`.
- Installed package name present on disk: `GAMMA_SOFTWARE-20240627_MSP_ISP_DIFF_IPTA.linux64_ubuntu2404.tar.gz`.
- Installed Gamma modules include `MSP`, `ISP`, `DIFF`, `DISP`, and `IPTA`.
- `GEO` is not a separate directory in this install, but DIFF contains the relevant geocoding tools, including `gc_map`, `geocode`, and `geocode_back`.
- Project Gamma environment script: `deploy/wsl/profiles/gamma_env.sh`.
- Project WSL Python: `/home/administrator/miniconda3/envs/insar_wsl_v1/bin/python`.

Important command checks already performed:

- `IPTA/bin/ts_rate` runs and prints usage. No license-denied error observed.
- `IPTA/bin/multi_def_pt` runs and prints usage. No license-denied error observed.
- `ISP/bin/par_LT1_SLC` exists and prints LT1 SLC conversion usage.
- `ISP/scripts/LT1_precision_orbit.py` runs with the project conda Python and prints usage.

## Data Pool Findings

Configured LT1 source pool:

- `D:\LuTan1_Image_Pool`

Configured LT1 precise orbit pool:

- `D:\orbit_pools\envi\LT1A`
- `D:\orbit_pools\envi\LT1B`

High-level inventory:

- LT1 scene directories found: `1500`.
- LT1A orbit TXT files found: `896`, spanning `20230508` to `20251219`.
- LT1B orbit TXT files found: `858`, spanning `20230510` to `20251219`.

Metadata caveat:

- `*.meta.xml` and `*_Check.xml` are not safe to parse as whole XML documents in PowerShell because many files contain malformed Chinese text near the tail, for example bad `usePreciseOrbit` closing text.
- The needed `productInfo` block is structurally valid. For stack discovery, parse only `<productInfo>...</productInfo>`.
- Gamma `par_LT1_SLC` should still use the original `.meta.xml`; do not rewrite source metadata unless a Gamma run proves the malformed tail is a blocker.

## Candidate Stack

First trial should use a narrow single-center stack, not the broad system time-series grouping.

Recommended trial stack:

- Satellite: `LT1B`
- Relative orbit: `114`
- Direction: `DESCENDING`
- Imaging mode: `STRIP1`
- Polarization: `HH`
- Approximate center: `E131.2 / N43.8`
- Scene count at this center: `7`
- Scenes with precise orbit TXT currently present: `5`

Use the 5 scenes with available precise orbit first:

| Date | Orbit TXT | Scene |
| --- | --- | --- |
| 20240516 | yes | `LT1B_MONO_SYC_STRIP1_012047_E131.2_N43.8_20240516_SLC_HH_S2A_0000399289` |
| 20240711 | yes | `LT1B_MONO_SYC_STRIP1_012880_E131.2_N43.8_20240711_SLC_HH_S2A_0000450956` |
| 20240905 | yes | `LT1B_MONO_SYC_STRIP1_013713_E131.2_N43.8_20240905_SLC_HH_S2A_0000501650` |
| 20250417 | yes | `LT1B_MONO_SYC_STRIP1_017045_E131.2_N43.8_20250417_SLC_HH_S2A_0000713375` |
| 20251002 | yes | `LT1B_MONO_SYC_STRIP1_019544_E131.2_N43.8_20251002_SLC_HH_S2A_0000891257` |

Do not include these two in the first run unless the missing orbits are added:

| Date | Orbit TXT | Scene |
| --- | --- | --- |
| 20250612 | no | `LT1B_MONO_SYC_STRIP1_017878_E131.2_N43.8_20250612_SLC_HH_S2A_0000772122` |
| 20250807 | no | `LT1B_MONO_SYC_STRIP1_018711_E131.2_N43.8_20250807_SLC_HH_S2A_0000831367` |

Common bounding box across the broader 13-scene `LT1B relOrbit 114 / E131-N44` candidate:

- lon: `130.8615 .. 131.1638`
- lat: `43.7127 .. 44.0987`

For the narrow `E131.2/N43.8` trial stack, overlap is visually/metadata-wise much tighter:

- Each scene center is around `131.20E, 43.79N`.
- Each scene bbox is roughly `130.81..131.62E`, `43.48..44.10N`.

Secondary candidate if the first stack fails:

- `LT1B relOrbit 114 DESCENDING STRIP1 HH`, center `E130.8/N43.9`.
- 5 dates, 4 with orbit: `20250425`, `20250620`, `20250815`, `20251010`.
- One scene is `MONO_MH1` while the others are `MONO_SYC`; keep it as secondary, not first choice.

## Current System Pairing Limitations

The current time-series stack selection is useful for broad discovery but is too coarse for Gamma IPTA production.

Observed code behavior:

- LT1A/LT1B are normalized into the same satellite family `LT1`.
- The compatibility key only uses direction, satellite family, imaging mode, and polarization.
- Relative orbit, absolute track family, scene center/strip identity, receiving station, and detailed LT1 product variant are not hard grouping keys.
- The stable-stack selector tries to recover by common AOI overlap and pairwise network connectivity.
- The SBAS network selector uses time-baseline, center-distance and overlap thresholds, but does not use Gamma-derived perpendicular baseline at planning time.
- Time-series processors currently accepted by the service are only `isce2_stack_mintpy` and `sarscape_sbas`; there is no `gamma_ipta_sbas` processor code yet.

For the Gamma IPTA trial, do not rely on the current automatic PS stack plan as the source of truth. Use a manually audited stack manifest first.

## Trial Run Checklist

### 1. Create Isolated Work Directory

Actual root:

```text
D:\Code\Insar_management_system_v2\backend\runtime\gamma_ipta_trials\lt1b_r114_e1312_n438_20240516_20251002
```

Keep these subdirectories:

```text
input\scenes
input\orbits
gamma\slc
gamma\mli
gamma\diff
gamma\ipta
logs
publish
```

For the first trial, prefer symlinks or a manifest that points to source scenes. Avoid duplicating large TIFF files unless Gamma scripts require local flat layout.

### 2. Build Scene Manifest

For each selected scene, record:

- scene directory
- `.tiff`
- `.meta.xml`
- precise orbit TXT
- date
- relative orbit
- direction
- mode
- polarization
- center lon/lat
- bbox

This manifest becomes the hand-audited truth for the trial.

### 3. Convert LT1 Products To Gamma SLC

For each selected scene:

```bash
source /mnt/d/Code/Insar_management_system_v2/deploy/wsl/profiles/gamma_env.sh
par_LT1_SLC <scene.tiff> <scene.meta.xml> <yyyymmdd>.slc.par <yyyymmdd>.slc
```

Then apply precise orbit:

```bash
/home/administrator/miniconda3/envs/insar_wsl_v1/bin/python \
  /usr/local/GAMMA_SOFTWARE-20240627/ISP/scripts/LT1_precision_orbit.py \
  <yyyymmdd>.slc.par <orbit_txt_or_orbit_folder>
```

### 4. Build SLC/MLI Tables

Create Gamma tables for the stack:

```text
SLC_tab
RMLI_tab
```

Use multilook settings conservative enough for a first run. The goal is robustness and fast feedback, not final product resolution.

### 5. Baseline And Pair Network

Use Gamma baseline tools first, not the system's center-distance approximation:

- `base_init`
- `base_perp`
- IPTA baseline tools such as `base_orbit_pt`, `base_par_pt`, `base_ls_pt` as needed by the official IPTA path.

For the first 5-scene stack, start with a simple connected small-baseline network:

- adjacent pairs by time
- add one or two skip pairs only if coherence and baseline look acceptable

Expected adjacent temporal intervals:

- `20240516 -> 20240711`: 56 days
- `20240711 -> 20240905`: 56 days
- `20240905 -> 20250417`: 224 days
- `20250417 -> 20251002`: 168 days

The large seasonal gaps are acceptable for a trial only if perpendicular baseline and coherence are reasonable. If they are poor, switch to a denser 2025-only or 2024-only local test, even with fewer dates.

### 6. Differential Interferograms

Use official Gamma DIFF commands/scripts for:

- coregistration
- interferogram generation
- simulated topographic phase
- differential phase
- filtering
- coherence
- unwrapping if needed by the chosen IPTA path

Do not implement custom SBAS inversion in the management system.

### 7. IPTA Processing

Use Gamma IPTA commands for point/stack time-series processing. Confirm exact command sequence against the installed:

```text
/usr/local/GAMMA_SOFTWARE-20240627/IPTA/html/IPTA_users_guide.pdf
```

Commands observed in the local IPTA module include:

- `multi_def_pt`
- `ts_rate`
- `ts_rate_pt`
- `base_ls_pt`
- `base_par_pt`
- `ph_base_pt`
- `atm_mod_pt`
- `pt2geo`
- `dis_ipta`

### 8. Review Outputs

Minimum acceptance checks for the first run:

- every selected scene converts to SLC
- precise orbit update succeeds for every SLC
- all intended pairs generate interferograms
- coherence is not uniformly poor
- unwrapping or IPTA point solution is not globally unstable
- one geocoded velocity or displacement-rate raster/vector product can be inspected
- logs and command manifests are complete enough to reproduce the run

## Trial Progress On 2026-05-18

### Files Created For This Trial

- `backend/runtime/gamma_ipta_trials/lt1b_r114_e1312_n438_20240516_20251002/input/scene_manifest.json`
- `backend/runtime/gamma_ipta_trials/lt1b_r114_e1312_n438_20240516_20251002/scripts/01_prepare_slc.sh`
- `backend/runtime/gamma_ipta_trials/lt1b_r114_e1312_n438_20240516_20251002/scripts/02_mli_and_baseline.sh`
- `backend/runtime/gamma_ipta_trials/lt1b_r114_e1312_n438_20240516_20251002/scripts/03_coreg_one_pair.sh`
- `backend/runtime/gamma_ipta_trials/lt1b_r114_e1312_n438_20240516_20251002/scripts/04_cc_stats.py`
- `backend/runtime/gamma_ipta_trials/lt1b_r114_e1312_n438_20240516_20251002/scripts/05_coreg_adjacent_pairs.sh`
- `backend/runtime/gamma_ipta_trials/lt1b_r114_e1312_n438_20240516_20251002/scripts/06_prepare_rdc_dem.sh`
- `backend/runtime/gamma_ipta_trials/lt1b_r114_e1312_n438_20240516_20251002/scripts/07_coreg_common_ref_stack.sh`
- `backend/runtime/gamma_ipta_trials/lt1b_r114_e1312_n438_20240516_20251002/scripts/08_diff_unwrap_common_ref.sh`
- `backend/runtime/gamma_ipta_trials/lt1b_r114_e1312_n438_20240516_20251002/scripts/09_mb_ts_rate.sh`
- `backend/runtime/gamma_ipta_trials/lt1b_r114_e1312_n438_20240516_20251002/scripts/10_float_stats.py`
- `backend/runtime/gamma_ipta_trials/lt1b_r114_e1312_n438_20240516_20251002/scripts/11_make_timeseries_previews.sh`
- `backend/runtime/gamma_ipta_trials/lt1b_r114_e1312_n438_20240516_20251002/scripts/12_geocode_export_timeseries.sh`
- `backend/runtime/gamma_ipta_trials/lt1b_r114_e1312_n438_20240516_20251002/scripts/13_phase_to_los.py`
- `backend/runtime/gamma_ipta_trials/lt1b_r114_e1312_n438_20240516_20251002/scripts/14_build_trial_summary.py`
- `backend/runtime/gamma_ipta_trials/lt1b_r114_e1312_n438_20240516_20251002/scripts/15_make_los_velocity_maps.sh`
- `backend/runtime/gamma_ipta_trials/lt1b_r114_e1312_n438_20240516_20251002/scripts/16_plot_monitor_point_timeseries.py`
- `backend/runtime/gamma_ipta_trials/lt1b_r114_e1312_n438_20240516_20251002/scripts/17_make_geocoded_web_previews.sh`

### Completed

- Created a hand-audited 5-scene LT1B manifest for `relOrbit 114 / DESCENDING / STRIP1 / HH / E131.2 N43.8`.
- Converted all 5 LT1 TIFF products with Gamma `par_LT1_SLC`.
- Applied precise orbit updates with Gamma `LT1_precision_orbit.py`.
- Built `gamma/slc/SLC_tab`.
- Built 8x8 multilooked MLI products with Gamma `multi_look`.
- Built `gamma/mli/RMLI_tab`.
- Generated all-pair and adjacent-pair baseline tables with Gamma `base_calc`.
- Ran adjacent-pair coregistration for all 4 selected adjacent pairs using Gamma `SLC_coreg.py`.
- Generated 4 interferograms and coherence products using Gamma `create_offset`, `SLC_intf`, and `cc_wave`.
- Generated browse previews using Gamma `rasmph` and `raspwr`.
- Computed coherence statistics for each adjacent pair.
- Reused the existing Copernicus30 DEM cache covering `E131.2/N43.8`.
- Generated a reference-geometry RDC DEM for `20240905` using Gamma `gc_map1`, `geocode`, and `gc_map_fine`.
- Built a common-reference RSLC/RMLI stack in `20240905` geometry.
- Generated common-reference differential interferograms with Gamma `phase_sim_orb` and `SLC_diff_intf`.
- Filtered common-reference differential interferograms with Gamma `adf`.
- Unwrapped common-reference differential interferograms with Gamma `mcf`.
- Ran Gamma IPTA `mb` to solve the image-based phase time-series.
- Ran Gamma IPTA `ts_rate` to estimate a linear phase-rate map.
- Generated Gamma preview rasters for `ts_rate`, `sigma_rate`, and `hgt_correction`.
- Exported phase-rate, sigma, height-correction, and LOS-rate rasters to EPSG:4326 GeoTIFF.
- Generated explicit LOS velocity maps in `mm/year` for both away-from-radar-positive and toward-radar-positive conventions.
- Generated one example monitoring-point LOS displacement curve as PNG, CSV, and metadata JSON.
- Generated north-up WGS84 web preview PNGs from the geocoded LOS GeoTIFF products.

### Key Outputs

- SLC stack: `gamma/slc/SLC_tab`
- MLI stack: `gamma/mli/RMLI_tab`
- All-pair baseline table: `gamma/diff/bperp_all_pairs.txt`
- Adjacent-pair table: `gamma/diff/itab_adjacent`
- Pair-specific RSLC products: `gamma/rslc/*_to_*.rslc`
- Pair-specific quality reports: `gamma/rslc/*_to_*.rslc.coreg_quality`
- Adjacent interferograms: `gamma/int/*.int`
- Adjacent coherence rasters: `gamma/int/*.cc`
- Adjacent interferogram previews: `gamma/int/*.int.bmp`
- Adjacent coherence previews: `gamma/int/*.cc.bmp`
- Coherence statistics: `logs/*_cc_stats.txt`
- Reference RDC DEM: `gamma/dem/20240905_8rlks.rdc.dem`
- Common-reference stack tables: `gamma/common_20240905/SLC_tab`, `gamma/common_20240905/RMLI_tab`
- Common-reference differential stack: `gamma/common_20240905/diff/*/*_8rlks.diff_filt.unw`
- Gamma `mb` time-series list: `gamma/common_20240905/timeseries/diff_ts.tab`
- Gamma `mb` phase time-series: `gamma/common_20240905/timeseries/diff_ts_*.diff`
- Gamma `mb` residual sigma: `gamma/common_20240905/timeseries/sigma_ts`
- Gamma `mb` height correction: `gamma/common_20240905/timeseries/hgt_correction`
- Gamma `ts_rate` output: `gamma/common_20240905/timeseries/ts_rate`
- Gamma `ts_rate` sigma output: `gamma/common_20240905/timeseries/sigma_rate`
- Preview rasters: `gamma/common_20240905/timeseries/*.bmp`
- Geocoded GeoTIFF exports: `publish/geotiff/*.tif`
- Explicit LOS velocity previews: `publish/geotiff/los_rate_toward_mm_per_year.bmp`, `publish/geotiff/los_rate_away_mm_per_year.bmp`
- Geocoded web previews: `publish/geotiff/los_rate_toward_mm_per_year.geo_preview.png`, `publish/geotiff/los_sigma_mm_per_year.geo_preview.png`
- Monitoring-point curve: `publish/monitor_points/auto_low_sigma_high_rate_timeseries.png`
- Monitoring-point values: `publish/monitor_points/auto_low_sigma_high_rate_timeseries.csv`
- Trial summary: `publish/trial_summary.json`

### Baseline Notes

`base_calc` generated 10 all-pair entries. The adjacent temporal network is:

| Pair | Delta days | Bperp from `base_calc` |
| --- | ---: | ---: |
| 20240516 -> 20240711 | 56 | 646.09670 |
| 20240711 -> 20240905 | 56 | -236.76960 |
| 20240905 -> 20250417 | 224 | 249.35880 |
| 20250417 -> 20251002 | 168 | 59.18150 |

The first validated pair was `20240711 -> 20240905` because it has a 56-day interval and moderate perpendicular baseline in this stack.

### Adjacent-Pair Quality

Final `SLC_coreg.py` quality-test summaries:

| Pair | Accepted offsets | Final std range | Final std azimuth |
| --- | ---: | ---: | ---: |
| 20240516 -> 20240711 | 2476 / 2688 | 0.0861 | 0.1120 |
| 20240711 -> 20240905 | 2483 / 2656 | 0.0370 | 0.1245 |
| 20240905 -> 20250417 | 2322 / 2688 | 0.0487 | 0.0617 |
| 20250417 -> 20251002 | 2274 / 2688 | 0.0304 | 0.0533 |

Coherence statistics from Gamma big-endian float files:

| Pair | valid `[0,1]` | p25 | median | p75 | p99 | Comment |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 20240516 -> 20240711 | 100% | 0.127489 | 0.212285 | 0.323520 | 0.649398 | Weakest pair; high Bperp and low coherence |
| 20240711 -> 20240905 | 100% | 0.629307 | 0.733220 | 0.802884 | 0.909973 | Strong pair |
| 20240905 -> 20250417 | 100% | 0.339094 | 0.485239 | 0.612798 | 0.833928 | Usable trial pair |
| 20250417 -> 20251002 | 100% | 0.394165 | 0.582921 | 0.734878 | 0.932300 | Usable trial pair |

All adjacent pairs generated expected Gamma products. The full 5-scene chain can continue, but the first pair is a quality risk for unwrapping/IPTA. A more conservative follow-up is to run the 4-scene sub-stack from `20240711` to `20251002`, or keep the 5-scene stack but down-weight or exclude `20240516 -> 20240711` if later unwrapping/IPTA residuals are poor.

Implementation note:

- `SLC_coreg.py` writes the refined offset parameter file to a secondary-date path such as `gamma/slc/20240711.slc.off`.
- Trial scripts copy that file to pair-specific paths such as `gamma/rslc/20240711_to_20240905.rslc.off`.
- Future skip-pair or non-adjacent networks must use pair-specific offset files to avoid accidental reuse after the same secondary scene is coregistered to another reference.

### Common-Reference Time-Series Trial

The image-based Gamma IPTA path was also run using a common `20240905` reference geometry. This is closer to the official `mb -> ts_rate` time-series chain than the first adjacent-pair wrapped interferogram check.

Common-reference inputs and products:

- reference geometry: `20240905`
- DEM source: existing Copernicus30 cache under `backend/runtime/pyint_dem_cache`
- RDC DEM: `gamma/dem/20240905_8rlks.rdc.dem`
- common-reference SLC table: `gamma/common_20240905/SLC_tab`
- common-reference MLI table: `gamma/common_20240905/RMLI_tab`
- common-reference differential ITAB:

```text
1 3 1 1
2 3 2 1
3 4 3 1
3 5 4 1
```

Filtered differential coherence statistics:

| Pair | valid `[0,1]` | p25 | median | p75 | p99 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 20240516 -> 20240905 | 100% | 0.497369 | 0.884586 | 0.945308 | 0.980583 |
| 20240711 -> 20240905 | 100% | 0.919149 | 0.968188 | 0.979449 | 0.990185 |
| 20240905 -> 20250417 | 100% | 0.366279 | 0.790519 | 0.909991 | 0.978094 |
| 20240905 -> 20251002 | 100% | 0.193104 | 0.815562 | 0.917209 | 0.972355 |

Gamma `mb` outputs:

- `gamma/common_20240905/timeseries/diff_ts_001.diff` through `diff_ts_005.diff`
- `gamma/common_20240905/timeseries/diff_ts.tab`
- `gamma/common_20240905/timeseries/itab_ts`
- `gamma/common_20240905/timeseries/sigma_ts`
- `gamma/common_20240905/timeseries/hgt_correction`

Gamma `ts_rate` outputs:

- `gamma/common_20240905/timeseries/ts_rate`
- `gamma/common_20240905/timeseries/ts_const`
- `gamma/common_20240905/timeseries/sigma_rate`
- `gamma/common_20240905/timeseries/ts_rate.bmp`
- `gamma/common_20240905/timeseries/sigma_rate.bmp`
- `gamma/common_20240905/timeseries/hgt_correction.bmp`

GeoTIFF exports:

- `publish/geotiff/ts_rate_rad_per_year.tif`
- `publish/geotiff/sigma_rate_rad_per_year.tif`
- `publish/geotiff/sigma_ts_rad.tif`
- `publish/geotiff/hgt_correction_m.tif`
- `publish/geotiff/los_rate_m_per_year.tif`
- `publish/geotiff/los_sigma_m_per_year.tif`
- `publish/geotiff/los_rate_away_mm_per_year.tif`
- `publish/geotiff/los_rate_toward_mm_per_year.tif`
- `publish/geotiff/los_sigma_mm_per_year.tif`
- `publish/geotiff/los_rate_toward_mm_per_year.geo_preview.png`
- `publish/geotiff/los_sigma_mm_per_year.geo_preview.png`
- `publish/trial_summary.json`

Float output statistics using Gamma big-endian float:

| File | Non-zero pixels | p25 | median | p75 | p99 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `ts_rate` | 8,868,956 | -1.663257 | -0.271621 | 0.869116 | 2.787572 |
| `sigma_rate` | 8,868,956 | 0.396037 | 0.677578 | 1.010539 | 2.298520 |
| `sigma_ts` | 8,892,237 | 0.000704 | 0.001557 | 0.002535 | 0.518188 |
| `hgt_correction` | 8,892,250 | -16.989384 | 7.596325 | 36.411520 | 98.636933 |

Explicit LOS velocity output statistics in `mm/year`:

| File | Non-zero pixels | p01 | p25 | median | p75 | p99 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `los_rate_toward_mm_per_year.rdc` | 8,868,956 | -52.779640 | -16.455760 | 5.142855 | 31.491957 | 84.590944 |
| `los_sigma_mm_per_year.rdc` | 8,868,956 | 0.530715 | 7.498536 | 12.829198 | 19.133450 | 43.519972 |

This is a successful first local Gamma official-chain time-series trial. It is still a technical validation run, not a production-grade SBAS product: the stack has only 5 dates, the network is minimal, and reference region and unwrapping masks were conservative defaults.

The exported GeoTIFFs are EPSG:4326, `2222 x 2237`, Float32, LZW-compressed Cloud Optimized GeoTIFFs with `NoData=0`.

The `*.bmp` files generated by Gamma `rasdt_pwr` are RDC processing-geometry browse images. They are useful for quick processing QA but are not map products and should not be used as the default UI map preview. The UI/default web preview should use the `*.geo_preview.png` files generated from the EPSG:4326 GeoTIFF products.

### LOS Sign Convention

Gamma `ts_rate` is a phase-rate raster in `rad/year`. Converting phase rate to LOS displacement rate requires a sign convention:

- `los_rate_away_mm_per_year = phase_rate * wavelength / (4*pi) * 1000`
- `los_rate_toward_mm_per_year = -phase_rate * wavelength / (4*pi) * 1000`

Gamma `dispmap` documents two conventions:

- `sflg=0`, the default: motion away from radar is negative, so motion toward radar is positive; deformation and unwrapped phase have opposite signs.
- `sflg=1`: motion away from radar is positive; deformation and unwrapped phase have the same sign.

For system productization, use explicit names and prefer `los_rate_toward_mm_per_year` as the default display product because it matches Gamma `dispmap` default `sflg=0`. Keep the away-positive version available when another downstream convention requires direct phase-sign products.

### Monitoring Point Curve

An example monitoring point was selected automatically from low-sigma, high-rate, non-edge pixels:

- radar pixel: range `336`, azimuth `2290`
- approximate lon/lat: `131.4340324903`, `43.8008322757`
- reference date in the plotted time series: `20240711`
- LOS convention: toward radar positive, away from radar negative
- fitted LOS velocity: `50.1085 mm/year`
- fitted LOS velocity sigma: `0.0063 mm/year`

Generated outputs:

- `publish/monitor_points/auto_low_sigma_high_rate_timeseries.png`
- `publish/monitor_points/auto_low_sigma_high_rate_timeseries.csv`
- `publish/monitor_points/auto_low_sigma_high_rate_metadata.json`

The plotted values are:

| Date | LOS displacement, toward-positive mm |
| --- | ---: |
| 20240516 | -7.673957 |
| 20240711 | 0.000000 |
| 20240905 | 7.673922 |
| 20250417 | 38.412669 |
| 20251002 | 61.464305 |

This is a single example point only. It is not a validated monitoring-point network and should not be interpreted as a representative area-wide deformation curve. The automatic selection favors a non-edge pixel with relatively high absolute velocity and low fitted sigma so the curve is visually inspectable. Production monitoring points need one of these inputs:

- user-clicked map lon/lat
- imported engineering monitoring-point layer
- a configured regular grid or point-of-interest set
- a quality-filtered automatic point sampler with spacing, coherence/sigma thresholds, and manual review

Until that is implemented, the single curve is a capability demonstration and should be labeled as such in the UI.

### Current Open Items

- Review the `ts_rate.bmp`, `sigma_rate.bmp`, `hgt_correction.bmp`, LOS velocity BMPs, monitoring-point PNG, and exported GeoTIFFs visually in GIS.
- Tune reference region, coherence thresholds, and pair network before treating the result as production.
- Decide whether to keep the weak/long 2025 pair, add skip-pairs, or use a denser data sequence when more LT1 precise orbits are available.
- Keep system integration as orchestration around Gamma commands; do not implement custom SBAS inversion in application code.

## Productization Decisions After Trial

If the 5-scene trial succeeds, add a new managed processor instead of bending existing ISCE/SARscape flows:

```text
processor_code = gamma_ipta_sbas
engine_code = gamma
workflow = gamma_ipta_sbas
```

Required system changes:

- Add a Gamma IPTA stack manifest builder.
- Add LT1-specific hard grouping keys:
  - satellite platform, not only family, unless cross-satellite LT1A/LT1B is explicitly validated
  - relative orbit
  - orbit direction
  - imaging mode
  - polarization
  - scene strip/center bucket
  - product variant or station/submode where it affects compatibility
- Add a Gamma baseline audit step before final pair network selection.
- Persist selected Gamma pair network separately from the coarse planning graph.
- Keep system code as orchestration only; Gamma remains the processing authority.

## Current Recommendation

Use the manual audited `LT1B relOrbit 114 DESCENDING STRIP1 HH / E131.2 N43.8` stack and the trial scripts as the reference path for productization.

Do not start Gamma IPTA production from the current automatic time-series plan. It can be used for discovery, but production stack selection needs the hard grouping keys and Gamma baseline audit described above.

For the next engineering step, add a managed `gamma_ipta_sbas` processor that orchestrates the Gamma commands rather than reimplementing SBAS inversion in application code.
