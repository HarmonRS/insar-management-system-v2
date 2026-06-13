# LandSAR SBAS-InSAR Archive Record - 2026-06-06

## Decision

The current LandSAR license does not include SBAS-InSAR capability. LandSAR SBAS production is archived and should not be used as a production path unless the runtime/license is replaced with one that supports SBAS-InSAR.

Future SBAS production work should focus on Gamma SBAS.

## Archived Runs

### Run 1

- Task ID: `2f772b33-e463-415d-a0e5-3153b092ed74`
- Job ID: `1e89b910-437d-4d0c-9957-a908bcbf0651`
- Task type: `SBAS_LANDSAR_WORKFLOW`
- Run ID: `landsar_sbas_20260605T175848009769Z_sbas_be0008c47ac5`
- Status: `FAILED`
- Started at: `2026-06-06 01:58:33`
- Ended at: `2026-06-06 02:42:14`
- Failure summary: LandSAR SBAS workflow failed after LT-1 import; `InSAR_Console.exe` returned `Cannot read this ID` for configured SBAS proID `280039`.

### Run 2

- Task ID: `51adf126-3d4b-4404-94af-1c12c2103f11`
- Job ID: `03eee99a-3bbc-4d81-9fdc-bf6199751834`
- Task type: `SBAS_LANDSAR_WORKFLOW`
- Run ID: `landsar_sbas_20260606T083925060668Z_sbas_be0008c47ac5`
- Status: `FAILED`
- Started at: `2026-06-06 16:38:20`
- Ended at: `2026-06-06 17:03:29`
- Failure summary: LandSAR SBAS runtime unsupported. LT-1 import completed for 7 scenes, but `InSAR_Console.exe` did not accept process `SBAS Stream` / proID `280039`.

## Data Stack

- Stack ID: `sbas_be0008c47ac5`
- Scene count: `7`
- Dates: `20240516`, `20240711`, `20240905`, `20250417`, `20250612`, `20250807`, `20251002`
- DEM: `D:\DEM\HeiLongJiang10M_DEM.tif`

## Cleanup Scope

The following records and generated artifacts were removed after this archive note was created:

- `system_tasks` records for the two task IDs above.
- `system_jobs` records for the two job IDs above.
- `task_logs` rows for the two task IDs above.
- Run result directories under `D:\production_results\timeseries\sbas_landsar\runs`.
- LandSAR working directories under `D:\LandSAR_Work\sbas`.

No matching rows were found in `result_products`, `result_catalog_states`, or `ps_timeseries_runs`.

## Follow-Up

- Keep LandSAR SBAS disabled or clearly marked unsupported in production operations.
- Continue SBAS production through Gamma SBAS only.
- Revisit LandSAR SBAS only after a supported license/runtime is available and proID/process compatibility is verified before full production execution.
