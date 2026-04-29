# ISCE2 LT-1 Enhancement Alignment 2026-04-27

## Purpose

This note records why the managed `ISCE2` `lt1_stripmap` production profile now
enables the built-in stripmap enhancement steps by default, and how that choice
relates to the existing SARscape `custom6` production chain.

## Background

The SARscape `custom6` chain already goes beyond a bare minimum D-InSAR run.
Its production semantics include:

1. Interferogram generation
2. Filtering and coherence
3. Orbital trend / residual phase frequency removal
4. Phase unwrapping
5. GCP-based refinement and reflattening
6. Phase to displacement and geocoding

This means the current LT-1 production baseline in the system is not a
scientifically "raw" interferometric export. It is an operationally enhanced
delivery chain.

## ISCE2 Mapping

ISCE2 stripmap does not expose the exact same SARscape modules, but it does
provide native enhancement steps that address the same operational risk class:
residual misregistration and geometry-driven long-wavelength artifacts.

The relevant built-in ISCE2 controls are:

- `doDenseOffsets`
- `doRubbersheetingRange`
- `doRubbersheetingAzimuth`
- `do split spectrum`
- `do dispersive`
- `rubberSheetSNRThreshold`
- `rubberSheetFilterSize`

When enabled, the stripmap workflow:

- estimates dense offsets from cross-correlation
- filters / masks those offsets
- updates the geometry offset fields
- performs a fine resampling pass using the corrected offsets
- unwraps low/high-band interferograms and estimates a dispersive ionosphere term
- geocodes the ionosphere-corrected nondispersive phase for delivery

This is not identical to SARscape's `RemoveResidualPhaseFrequency` plus
`RefinementAndReflattening`, but it is the closest native ISCE2 enhancement
path inside the standard stripmap application.

## Production Decision

The managed `ISCE2` `lt1_stripmap` profile now treats these steps as part of the
default LT-1 production workflow:

- split-spectrum ionosphere correction: enabled
- dense offsets: enabled
- range rubbersheeting: enabled
- azimuth rubbersheeting: enabled

Default numeric parameters:

- `rubberSheetSNRThreshold = 5.0`
- `rubberSheetFilterSize = 9`
- `denseWindowWidth = 64`
- `denseWindowHeight = 64`
- `denseSearchWidth = 20`
- `denseSearchHeight = 20`
- `denseSkipWidth = 32`
- `denseSkipHeight = 32`

These defaults are stored as profile semantics in code, not as loose `.env`
feature toggles.

## Runtime Dependency

The stripmap ionosphere implementation imports `cv2` and `scipy`.

Deployment check:

```bash
/home/administrator/miniconda3/envs/insar_wsl_v1/bin/python -c "import cv2, scipy; print('ionosphere_ok')"
```

Repair command for an existing runtime:

```bash
conda install -n insar_wsl_v1 -c conda-forge opencv scipy
```

The range rubbersheeting implementation in ISCE2 imports
`astropy.convolution` from `runRubbersheetRange.py`. The shared WSL conda
runtime therefore must include `astropy`.

Deployment check:

```bash
/home/administrator/miniconda3/envs/insar_wsl_v1/bin/python -c "from astropy.convolution import convolve; print('astropy_ok')"
```

Repair command for an existing runtime:

```bash
conda install -n insar_wsl_v1 -c conda-forge astropy
```

## Boundary

This change does **not** mean that every long-wavelength ramp problem is solved.

It only means the default managed ISCE2 LT-1 profile now includes the native
registration-enhancement path that was previously omitted.

If a run still shows a strong residual scene-wide ramp after rubbersheeting,
that should be treated as a separate quality / post-processing issue and should
be diagnosed explicitly rather than silently hidden inside export logic.

## Operational Implication

When comparing current SARscape and ISCE2 LT-1 products:

- SARscape `custom6` remains the more explicitly refined chain
- ISCE2 `lt1_stripmap` is no longer a bare stripmap baseline
- both engines now include standard enhancement intent in default production

This makes cross-engine behavior more defensible for LT-1 operational delivery.

## Operator Controls

As of 2026-04-29, the production UI no longer hides these choices behind code
defaults only.

The ISCE2 production panel now exposes the managed LT-1 profile parameters in
three groups:

- `Execution`
- `Delivery`
- `Enhancement`

The user-visible controls now cover:

- split-spectrum ionosphere correction on/off
- dense offsets on/off
- range rubbersheeting on/off
- azimuth rubbersheeting on/off
- reference normalization mode (`coh_median` or `none`)
- deramp mode (`plane` or `none`)

This keeps the default managed behavior unchanged, while allowing operators to
fall back toward a more conservative stripmap delivery path when a specific
scene looks worse after enhancement.
