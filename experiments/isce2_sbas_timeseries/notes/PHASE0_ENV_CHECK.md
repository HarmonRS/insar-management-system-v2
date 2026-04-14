# Phase 0 Environment Check

Updated: 2026-04-03

## Confirmed

- Project workspace:
  - Windows repo root: `Z:\Code\Insar_management_system_v2`
  - WSL mount path: `/mnt/z/Code/Insar_management_system_v2`
- Windows project Python env:
  - `C:\Users\Administrator\.conda\envs\InSAR`
- WSL experiment distro:
  - `Ubuntu-24.04`
- WSL project access:
  - `/mnt/z/Code/Insar_management_system_v2`
  - `/mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries`
- WSL system Python:
  - `Python 3.12.3`
- WSL conda root:
  - `/home/administrator/miniconda3`
- WSL conda envs found:
  - `base`
  - `isce2`
- ISCE2 env package:
  - `isce2 2.6.4`
- ISCE2 Python import path:
  - `/home/administrator/miniconda3/envs/isce2/lib/python3.11/site-packages/isce/__init__.py`
- Lutan1 sensor module:
  - `/home/administrator/miniconda3/envs/isce2/lib/python3.11/site-packages/isce/components/isceobj/Sensor/Lutan1.py`
- Official stack directories present:
  - `/home/administrator/miniconda3/envs/isce2/share/isce2/stripmapStack`
  - `/home/administrator/miniconda3/envs/isce2/share/isce2/topsStack`

## Confirmed Gaps

- `conda` is not currently on the default shell `PATH` inside `Ubuntu-24.04`.
  - Use `/home/administrator/miniconda3/bin/conda` directly in scripts.
- `MintPy` is not installed in the `isce2` env yet.
  - `conda list -n isce2 mintpy` returned no match.
- Calling `conda list -n isce2 ...` from a WSL bash script triggered a segmentation fault once.
  - For experiment scripts, prefer `conda run -n isce2 python ...` checks over `conda list`.

## Implication

Phase 0 can start immediately for:

- LT-1 / ISCE2 stack compatibility checks
- workspace and path validation
- command-chain drafting

But the full SBAS chain cannot run end to end until one of these is true:

- `MintPy` is installed into `isce2`, or
- a separate WSL env with `MintPy` is prepared

## Next Checks

1. Verify ISCE2 stack scripts actually exist in `Ubuntu-24.04`.
2. Read `stripmapStack/README.md` and `stackStripMap.py` to identify required stack inputs.
3. Decide whether `MintPy` should share the `isce2` env or live in a separate env.
4. Verify LT-1 / LUTAN1 support is usable for stack-mode inputs, not only single-pair mode.
5. Draft a minimal stack experiment command chain under this folder.
