# Gamma SBAS expert workflow audit 2026-06-08

## Scope

Audited run:

- `run_id`: `sbas_a5d51de3808a`
- workflow manifest: `backend/runtime/sbas_insar_production/runs/sbas_a5d51de3808a/manifest.json`
- command manifest: `backend/runtime/sbas_insar_production/runs/sbas_a5d51de3808a/gamma_command_manifest.json`
- scripts: `backend/runtime/sbas_insar_production/runs/sbas_a5d51de3808a/scripts/*.sh`
- result catalog product: `gamma_sbas_sbas_a5d51de3808a`

The audit checks whether the production path follows `LT1_GAMMA_SBAS_逐命令处理流程.docx` as represented by `docs/GAMMA_SBAS_EXPERT_CORRECT_IMPLEMENTATION_ROUTE_20260607.md`.

## Result

The Gamma processing chain is now aligned with the expert-document workflow at the command-chain level. The run completed all 12 expert workflow steps and the result catalog registers only the expert-chain products as the primary SBAS result.

This is not the old mixed Gamma/PyINT path. The old invalid run and old invalid catalog record were removed.

## Step Mapping

| Step | Expert workflow requirement | Current run evidence | Status |
| --- | --- | --- | --- |
| 01 Workspace | Expert directories: `RAW`, `SLC`, `dem`, `rslc_prep`, `mli_dir`, `diff_dir`, `diff1_dir`, `sbas`, `publish`, `logs`, `scripts`, `state` | All directories exist under the run root | Pass |
| 02 Import LT1 SLC | `par_LT1_SLC`, copy original par, `ORB_filt_spline.py`, `SLC_corners`, browse checks | Script uses `par_LT1_SLC` and `ORB_filt_spline.py`; SLC products exist | Pass |
| 03 Reference MLI | `multi_look`, width/line checks, `ras_dB`, `SLC_corners` | Command audit passed with `multi_look`, `ras_dB`, `SLC_corners`; `mli.ave` products exist | Pass |
| 04 DEM Lookup | `dem_import`, `fill_gaps`, `gc_map2`, `pixel_area`, `create_diff_par`, `offset_pwrm`, `offset_fitm`, `gc_map_fine`, `geocode`, `geocode_back` | Command audit passed; `20241007.lt_fine`, `20241007.hgt`, `20241007_seg.dem_par` exist | Pass |
| 05 Coreg Prep | Copy reference SLC to RSLC prep and initialize `rslc_tab` | `rslc_prep/rslc_tab` exists | Pass |
| 06 Coregister Scenes | `create_offset`, `init_offset_orbit`, `init_offset`, `offset_pwr`, `offset_fit`, `SLC_interp` | Command audit passed with all required commands | Pass |
| 07 RMLI Average | `mk_mli_all`, width/line checks, `ras_dB` | Command audit passed; `mli.ave`, `mli.ave.par` exist | Pass |
| 08 Diff Network | `base_calc`, `base_plot`, `mk_diff_2d` | Command audit passed; `itab`, `bprep_file` exist | Pass |
| 09 Filter Unwrap | `mk_adf_2d`, `ave_image`, `rascc_mask`, two `mk_unw_2d` passes | Command audit passed with required commands | Pass |
| 10 Detrend ATM | `create_diff_par`, `quad_fit`, `quad_sub`, `atm_mod_2d`, `fill_gaps`, `atm_sim_2d`, `sub_phase` | Command audit passed; `unw_atmsub_tab` exists | Pass |
| 11 SBAS Inversion | Three `mb` passes with complex conversion and `unw_model` correction | Script runs three `mb` calls, `real_to_cpx -`, `unw_model`; `diff.sigma_ts`, `itab_ts`, `ras/diff.tab` exist | Pass with documented compatibility note |
| 12 Outputs Points | `replace_values`, `mask_data`, `dispmap`, `ts_rate`, `geocode_back`, `data2geotiff`, `disp_prt_2d` | Command audit passed; `geo_los_def_rate.tif`, `geo_los_def_rate_rgb.tif`, `items.txt`, `disp_point.txt` exist | Pass |

## Compatibility Note

The expert route originally referenced `unw_to_cpx`. The installed Gamma environment used for this run does not provide `unw_to_cpx`, and the implementation uses:

```bash
real_to_cpx - <pair>.unw.atmsub <pair>.unw.atmsub.cpx <width> 1
```

This is used only at the same workflow point to create the complex input consumed by `unw_model`. The command manifest, production code, and audit route have been updated to state this explicitly.

## Primary Products

The valid expert-chain products are:

- `publish/geotiff/geo_los_def_rate.tif`
- `publish/geotiff/geo_los_def_rate_rgb.tif`
- `publish/points/items.txt`
- `publish/points/disp_point.txt`

System-generated visualization derivatives are:

- `publish/geotiff/geo_los_def_rate_rgb_preview.png`
- `publish/geotiff/geo_los_def_rate_hls_colorbar.png`
- `publish/monitor_points/expert_point_001_timeseries.png`
- `publish/monitor_points/expert_point_001_timeseries.csv`
- `publish/monitor_points/expert_point_001_metadata.json`

These derivatives are not substitutes for the expert Gamma products. They are catalog and UI display assets derived after the expert outputs exist.

## Catalog Status

After cleanup and rebuild:

- SBAS catalog run count: 1
- registered product: `gamma_sbas_sbas_a5d51de3808a`
- product status: `READY`
- health status: `OK`
- issue count: 0

## Residual Risk

The command chain now follows the expert workflow. Remaining validation should be scientific review of parameter choices and result interpretation, especially:

- reference window size and selected reference point
- baseline network selection and itab approval policy
- deformation sign convention for business reporting
- whether the expert accepts `real_to_cpx -` as the local Gamma equivalent of the documented complex conversion step
