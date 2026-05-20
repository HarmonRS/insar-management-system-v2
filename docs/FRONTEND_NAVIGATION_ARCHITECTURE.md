# Frontend Navigation Architecture

## 1. Purpose

This document is the source of truth for the left-side navigation structure and the production workspace view model in the frontend.

It explains:

- the first-level menu groups
- the sectioned groups and their leaf tabs
- the special-case production workspace entry
- the reserved entries and legacy route aliases
- the files that must be updated when navigation changes

The goal is to keep module boundaries stable as the system expands across D-InSAR, time-series InSAR, AI analysis, and operations workflows.

## 2. Current First-Level Groups

The current first-level menu groups are:

- `data`: 数据管理
- `production_planning`: 生产规划
- `production_management`: 生产管理
- `insar_analysis`: InSAR形变分析
- `ai_analysis`: AI分析
- `flood_analysis`: 洪涝灾害分析
- `ops`: 运行维护

Definition files:

- `frontend/src/config/appConstants.js`
- `frontend/src/utils/appUiHelpers.js`

## 3. Navigation Model

The current frontend uses two navigation patterns:

1. Sectioned navigation:
   first-level group -> second-level section -> leaf tab
2. Workspace navigation:
   first-level group -> single leaf tab -> internal workspace view switcher

### 3.1 生产规划

This is a sectioned group.

```text
生产规划
├─ 规划编组
│  ├─ 配对规划 (`pairing`)
│  ├─ 任务规划 (`pairs`)
│  ├─ 时序候选栈 (`ps_results`)
│  └─ 任务批次 (`batches`)
└─ 数据分发
   └─ 数据分发 (`copier`)
```

Notes:

- `ps_results` here means planning-stage candidate stacks, not analysis-facing result pages.
- This group no longer hosts D-InSAR production or product pages.

### 3.2 生产管理

This is a workspace group, not a multi-tab planning tree.

```text
生产管理
└─ 生产管理 (`production_management`)
   ├─ D-InSAR运行 (`dinsar_runs`)
   ├─ SBAS-InSAR Production (`sbas_insar_production`)
   └─ D-InSAR产物 (`dinsar_products`)
```

Notes:

- The left navigation contains only one tab for this group: `production_management`.
- Internal workspace views are controlled by `PRODUCTION_WORKSPACE_VIEWS`.
- Route alias mapping is controlled by `PRODUCTION_WORKSPACE_ENTRY_TO_VIEW`.
- Legacy route tabs such as `dinsar_production`, `ps_production`, and `ps_products` map into this workspace and should not be treated as standalone left-nav entries.
- The old ISCE2/MintPy `timeseries_runs` and `timeseries_products` workspace views are deprecated and hidden; SBAS production is handled by the Gamma `sbas_insar_production` view.

### 3.3 InSAR形变分析

This is a sectioned group.

```text
InSAR形变分析
├─ D-InSAR
│  ├─ D-InSAR结果 (`dinsar_results`)
│  └─ D-InSAR分析 (`dinsar_analysis`)
└─ 时序InSAR
   ├─ 时序InSAR结果 (`psinsar_results`)
   └─ 时序InSAR分析 (`psinsar_analysis`)
```

Notes:

- This group is for business-facing result browsing and interpretation.
- AI diagnosis does not belong here.
- `dinsar_analysis`, `psinsar_results`, and `psinsar_analysis` are currently reserved placeholders.

### 3.4 AI分析

This is a sectioned group.

```text
AI分析
├─ 形变智能分析
│  ├─ AI质量评估 (`ai_quality`)
│  └─ D-InSAR诊断 (`ai_diagnosis`)
└─ 遥感视觉分析
   ├─ 滑坡语义分割 (`landslide_segmentation`)
   └─ 无人机影像分析 (`uav_image_analysis`)
```

Notes:

- `ai_diagnosis` is the actual tab key; its display label is `D-InSAR诊断`.
- `landslide_segmentation` and `uav_image_analysis` remain reserved placeholders.

### 3.5 无二级分组的一级入口

The following groups do not define second-level sections:

- `data`
  leaf tabs: `ingest`, `data`, `hazard`
- `flood_analysis`
  leaf tabs: `flood_analysis`
- `ops`
  leaf tabs: `health`, `users`, `audit`

## 4. Source-Of-Truth Rules

The navigation follows these rules:

- `LEFT_GROUP_LABELS` defines the first-level group vocabulary.
- `LEFT_GROUP_SECTIONS` defines second-level sections where they exist.
- `LEFT_GROUP_TABS` defines which leaf tabs belong to each group.
- `LEFT_TAB_GROUP` and `LEFT_TAB_SECTION` are derived maps and should not be edited manually.
- `leftPanelTab` remains the route/state source of truth for the selected leaf entry.
- `production_management` is a special case: one left-nav tab owns multiple internal workspace views.
- New features should be added under an existing group whenever possible.
- A new first-level group should be introduced only for a durable, independent capability area.

## 5. Naming Rules

To avoid future ambiguity, use these naming constraints:

- Use `结果` for browsing, querying, and result-facing visualization pages.
- Use `产物` for extraction, publishing, packaging, and catalog-management pages.
- Use `运行` for task submission, engine selection, execution control, and runtime monitoring views.
- Use `分析` for interpretation, statistics, and analyst-facing thematic workflows.
- Use `诊断` for model-assisted fault analysis or AI-driven reasoning pages.
- Use `时序候选栈` only for planning-stage candidate stacks under `production_planning`.
- Use `时序InSAR结果` for analysis-facing result pages under `insar_analysis`.

## 6. Files To Update When Navigation Changes

When adding or moving a tab, update these files together:

- `frontend/src/config/appConstants.js`
  Defines first-level groups, sections, tab ownership, workspace view mappings, and admin-only visibility.
- `frontend/src/utils/appUiHelpers.js`
  Defines display labels for leaf tabs.
- `frontend/src/components/app/AppSidePanel.jsx`
  Renders the side-panel navigation and group/section switching behavior.
- `frontend/src/App.jsx`
  Connects route state with panel rendering.
- `frontend/src/ProductionWorkspace.jsx`
  Owns the internal production workspace view switcher.
- `frontend/src/App.css`
  Styles the navigation hierarchy and workspace entry state.

If the new tab is a real page instead of a placeholder, also add or update the corresponding panel component.

## 7. Reserved Entries And Legacy Route Aliases

Reserved leaf tabs:

- `dinsar_analysis`
- `psinsar_results`
- `psinsar_analysis`
- `landslide_segmentation`
- `uav_image_analysis`

Legacy route aliases mapped into `production_management`:

- `dinsar_production`
- `dinsar_products`
- `ps_production`
- `ps_products`

These aliases exist for compatibility, but they are not first-class left-nav entries anymore.

## 8. Future Extension Guidance

Recommended future additions:

- Use `flood_analysis` as the combined first-level group for water extraction, flood detection, overlay analysis, and flood results. The legacy `water` route may remain in code for compatibility, but it is no longer a first-class left-nav entry.
- Put new production execution or product-governance capability under `production_management` as an internal workspace view unless a separate first-level domain is clearly required.
- Put planning, batching, pairing, and dispatch preparation capability under `production_planning`.
- Put result browsing and analyst-facing deformation interpretation under `insar_analysis`.
- Put intelligent interpretation, diagnosis, segmentation, and computer-vision modules under `ai_analysis`.

If a new feature belongs to intelligent interpretation or computer vision, prefer `AI分析`.
If a new feature belongs to result browsing or deformation business analysis, prefer `InSAR形变分析`.
