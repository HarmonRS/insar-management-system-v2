# Codebase Cleanup Audit

Updated: 2026-04-25

## Scope

This audit focuses on three cleanup questions:

1. Which files are clearly transitional or unused?
2. Which documentation files are authoritative versus historical?
3. Are the reported "garbled comments" real source corruption or terminal/display issues?

## Confirmed Cleanup Completed In This Wave

The frontend had active implementations living under transitional filenames while older same-purpose files remained in place.

Completed normalization:

- Promoted active files from transitional names back to canonical names:
  - `frontend/src/DinsarProductsPanel.jsx`
  - `frontend/src/components/DinsarCatalogPanel.jsx`
  - `frontend/src/panels/DinsarResultPanel.jsx`
  - `frontend/src/components/ResultExportModal.jsx`
  - `frontend/src/components/panels/DinsarResultRow.jsx`
  - `frontend/src/LogManagementPanel.jsx`
- Removed superseded duplicate implementations that were no longer referenced.
- Updated imports so runtime entry points no longer depend on `.rewrite` or `.clean` suffixes.
- Updated `INIT.md` so it no longer describes the old temporary naming scheme as current reality.

## High-Confidence Findings

### 1. Transitional frontend files had become the real implementation

Before cleanup, the live entry points imported:

- `DinsarProductsPanel.rewrite.jsx`
- `DinsarResultPanel.rewrite.jsx`
- `LogManagementPanel.clean.jsx`

while older canonical filenames still existed beside them.

This is a maintenance hazard because:

- file names no longer reflect runtime truth
- engineers can patch the wrong file
- stale files increase review and search noise

### 2. Several "unused file" suspicions were correct, but not all of them

Confirmed pattern:

- some canonical frontend files were effectively dead
- some `.rewrite` or `.clean` files were not dead at all; they were the active implementation

Conclusion:

- `rewrite` / `clean` suffix is not enough to classify a file as removable
- reference tracing is required before deletion

### 3. Most observed Chinese garbling is a tooling/display problem, not necessarily source corruption

Key project files such as:

- `README.md`
- `docs/DEPLOYMENT.md`
- `docs/CURRENT_STATUS_20260425.md`
- `backend/app/main.py`
- `backend/app/config.py`

read correctly when opened as UTF-8.

This indicates that a significant part of the reported garbling comes from PowerShell/default encoding behavior rather than broken source text.

### 4. Some operational notes were stale even when the code was fine

`INIT.md` still described the temporary `LogManagementPanel.clean.jsx` workflow after the codebase had already stabilized enough for normalization.

That kind of drift is small, but it compounds quickly in a repo with many dated design notes.

## Remaining Cleanup Candidates

These items were not removed automatically in this wave, but they should be considered next.

### A. Documentation governance

Current situation:

- `docs/INDEX.md` does a reasonable job separating current docs from historical docs
- the repository still contains many dated design, TODO, experiment, and archive documents

Recommended next step:

- keep `docs/INDEX.md` as the contract
- move any newly superseded design notes to `docs/archive/`
- avoid leaving outdated process notes at repo root unless they are still operational

### B. Large-file refactors

Large files remain a maintainability risk even when they are active:

- `frontend/src/App.jsx`
- `backend/app/services/timeseries_service.py`
- `backend/app/models/orm.py`
- `backend/app/services/dinsar_production_service.py`

Recommended next step:

- split by responsibility, not by arbitrary line count
- keep public contracts stable while extracting helpers/modules

### C. Historical compatibility layers

There are still intentional legacy bridges in the backend, for example:

- compatibility catalog/data bridges
- legacy manifest normalization paths
- legacy environment variables in WSL runtime definitions

These should not be removed blindly. They need a separate compatibility retirement review driven by real production usage.

## Recommendations

### Phase 1: Done

- remove dead duplicate frontend files
- normalize active transitional filenames
- correct stale operational notes

### Phase 2: Safe repository hygiene

- review root-level notes such as `INIT.md` for whether they still belong at repo root
- move superseded design/process notes to `docs/archive/`
- add a lightweight naming rule: no long-lived `.rewrite`, `.clean`, `.tmp`, `.bak` files in active UI paths

### Phase 3: Controlled architecture cleanup

- split oversized service files
- document which compatibility layers are still required by production data
- retire legacy code only after proving there is no runtime dependency

## Practical Rule Going Forward

Use this decision order for cleanup:

1. Trace imports or runtime references.
2. Normalize active files back to canonical names.
3. Delete only the files that are both superseded and unreferenced.
4. Update the nearest authoritative document in the same change.
