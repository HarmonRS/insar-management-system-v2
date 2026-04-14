# Frontend Navigation Architecture

## 1. Purpose

This document is the source of truth for the left-side navigation structure in the frontend.

It explains:

- the first-level menu groups
- the second-level working domains
- the leaf tabs bound to actual pages
- the reserved entries for future modules
- the files that must be updated when navigation changes

The goal is to keep module boundaries stable as the system expands beyond D-InSAR into PS-InSAR and broader AI analysis workflows.

## 2. Current First-Level Groups

The current first-level menu groups are:

- `data`: 数据管理
- `production`: 生产规划
- `insar_analysis`: InSAR形变分析
- `ai_analysis`: AI分析
- `water`: 水体监测
- `ops`: 运行维护

Definition file:

- `frontend/src/config/appConstants.js`

## 3. Three-Level Structure

Only some first-level groups use second-level working domains.

### 3.1 生产规划

```text
生产规划
├─ 规划编组
│  ├─ 配对规划
│  ├─ 任务规划
│  ├─ PS时序栈
│  └─ 任务批次
├─ 数据分发
│  └─ 数据分发
├─ D-InSAR
│  ├─ D-InSAR生产
│  └─ D-InSAR产物
└─ PS-InSAR
   ├─ PS-InSAR生产
   └─ PS-InSAR产物
```

Notes:

- `PS时序栈` belongs to production planning, not analysis.
- `D-InSAR产物` is distinct from `D-InSAR结果`.
- `PS-InSAR生产` and `PS-InSAR产物` are reserved entries for future implementation.

### 3.2 InSAR形变分析

```text
InSAR形变分析
├─ D-InSAR
│  ├─ D-InSAR结果
│  └─ D-InSAR分析
└─ PS-InSAR
   ├─ PS-InSAR结果
   └─ PS-InSAR分析
```

Notes:

- This group is for business-facing result browsing and deformation analysis.
- AI diagnosis no longer belongs here.
- `D-InSAR分析`, `PS-InSAR结果`, and `PS-InSAR分析` are currently reserved placeholders.

### 3.3 AI分析

```text
AI分析
├─ 形变智能分析
│  ├─ AI质量评估
│  └─ D-InSAR诊断
└─ 遥感视觉分析
   ├─ 滑坡语义分割
   └─ 无人机影像分析
```

Notes:

- This group owns model-centric and intelligent-analysis capabilities.
- `D-InSAR诊断` is the renamed placement of the old AI diagnosis page.
- `滑坡语义分割` and `无人机影像分析` are reserved placeholders for future AI modules.

## 4. Navigation Design Rules

The navigation follows these rules:

- First-level groups represent stable business domains.
- Second-level domains represent workflow clusters inside a domain.
- Leaf tabs represent actual pages bound to `leftPanelTab`.
- The state source of truth remains `leftPanelTab`; group and section are derived from the tab key.
- New features should be added as leaf tabs under an existing domain whenever possible.
- A new first-level group should be introduced only when the feature becomes a long-term standalone capability cluster.

## 5. Naming Rules

To avoid future ambiguity, follow these naming constraints:

- Use `结果` for result browsing, querying, and visualization pages.
- Use `产物` for extraction, packaging, publishing, and catalog management pages.
- Use `分析` for interpretation, statistics,专题分析, and human-facing analytical workflows.
- Use `诊断` for model-assisted fault analysis or AI-driven case reasoning.
- Do not reuse `PS结果` as a generic label.
  Use `PS时序栈` in production planning.
  Use `PS-InSAR结果` in analysis.

## 6. Files to Update When Navigation Changes

When adding or moving a tab, update these files together:

- `frontend/src/config/appConstants.js`
  Defines first-level groups, second-level sections, tab ownership, and visibility.
- `frontend/src/utils/appUiHelpers.js`
  Defines display labels for leaf tabs.
- `frontend/src/App.jsx`
  Renders the group tabs, section tabs, and page content.
- `frontend/src/App.css`
  Styles the first-level, second-level, and leaf-tab navigation.

If the new tab is a real page instead of a placeholder, also add or update the corresponding panel component.

## 7. Reserved Leaf Tabs

The following leaf tabs are intentionally reserved for future work:

- `ps_production`
- `ps_products`
- `dinsar_analysis`
- `psinsar_results`
- `psinsar_analysis`
- `landslide_segmentation`
- `uav_image_analysis`

Reserved tabs should remain visible in the information architecture if they help stabilize the long-term module layout.

## 8. Implementation Notes

- Admin-only visibility is controlled by `ADMIN_ONLY_TABS` in `appConstants.js`.
- Section ownership is derived by `LEFT_TAB_SECTION`.
- Group ownership is derived by `LEFT_TAB_GROUP`.
- Groups without second-level sections still render as a two-level navigation.
- Groups with configured sections render as three-level navigation.

## 9. Future Extension Guidance

Recommended future additions:

- Put PS-InSAR processing forms, task submission, and runtime monitoring under `ps_production`.
- Put PS-InSAR extraction, packaging, registration, and catalog maintenance under `ps_products`.
- Put thematic deformation analysis and reporting under `dinsar_analysis` and `psinsar_analysis`.
- Put optical, UAV, or segmentation-based AI modules under `ai_analysis`, not under `insar_analysis`.

If a new feature belongs to intelligent interpretation or computer vision, prefer adding it under `AI分析`.
If a new feature belongs to result browsing or deformation business analysis, prefer adding it under `InSAR形变分析`.
