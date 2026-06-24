const TASK_UI_POLICIES = {
  SCAN_DATA: { label: '同步源数据', featureScope: 'data_monitor' },
  SCAN_DINSAR: { label: '扫描 D-InSAR 结果', featureScope: 'dinsar_products' },
  DINSAR_RESULT_SCAN: { label: 'D-InSAR 结果扫描', featureScope: 'dinsar_products' },
  AI_TRAIN: { label: '训练 AI 模型', featureScope: 'insar_analysis' },
  AI_PREDICT: { label: '全量质量评估', featureScope: 'insar_analysis' },
  AI_ANALYZE: { label: 'AI 智能诊断', featureScope: 'insar_analysis' },
  AI_DIAGNOSIS: { label: 'D-InSAR诊断', featureScope: 'insar_analysis' },
  AI_WARMUP: { label: 'AI 模型预热', featureScope: 'insar_analysis' },
  COPY_DATA: { label: '数据分发拷贝', featureScope: 'data_monitor' },
  SCAN_HAZARD: { label: '灾害点同步', featureScope: 'hazard' },
  UNPACK_ARCHIVES: { label: 'LT-1 批量解包', featureScope: 'data_monitor' },
  UNPACK_SENTINEL1: { label: 'Sentinel-1 批量解包', featureScope: 'data_monitor' },
  GF3_UNPACK: { label: 'GF3 数据解包', featureScope: 'data_monitor' },
  GF3_BATCH_PROCESS: { label: 'GF3 数据预处理', featureScope: 'data_monitor' },
  GF3_SARSCAPE_PRODUCE: { label: 'GF3 SARscape 生产', featureScope: 'data_monitor' },
  GF3_SARSCAPE_SYNC: { label: 'GF3 _geo 原生入库', featureScope: 'data_monitor' },
  GF3_QUICKLOOK_WEBP: { label: 'GF3 _geo WebP', featureScope: 'data_monitor' },
  GF3_SARSCAPE_CLEAN: { label: 'GF3 中间清理', featureScope: 'data_monitor' },
  SCAN_ASSET_INVENTORY: { label: '资产库存扫描', featureScope: 'asset_inventory' },
  AUDIT_SOURCE_ARCHIVE_INTEGRITY: { label: '压缩包完整性审计', featureScope: 'asset_inventory' },
  IDL_IMPORT: { label: 'ENVI 数据导入', featureScope: 'dinsar_production' },
  IDL_DINSAR: { label: 'ENVI D-InSAR 生产', featureScope: 'dinsar_production' },
  IDL_RUN_DINSAR: { label: 'ENVI D-InSAR 生产', featureScope: 'dinsar_production' },
  ISCE2_RUN: { label: 'D-InSAR 历史任务', featureScope: 'dinsar_production' },
  PYINT_RUN: { label: 'PyINT D-InSAR 生产', featureScope: 'dinsar_production' },
  LANDSAR_RUN: { label: 'LandSAR D-InSAR 生产', featureScope: 'dinsar_production' },
  SBAS_GAMMA_WORKFLOW: { label: 'Gamma SBAS 工作流', featureScope: 'sbas_insar' },
  SBAS_LANDSAR_WORKFLOW: { label: 'LandSAR SBAS 工作流', featureScope: 'sbas_insar' },
  SBAS_COREGISTRATION: { label: 'SBAS 配准', featureScope: 'sbas_insar' },
  SBAS_RDC_DEM: { label: 'SBAS RDC DEM', featureScope: 'sbas_insar' },
  SBAS_INTERFEROGRAMS: { label: 'SBAS 干涉图', featureScope: 'sbas_insar' },
  SBAS_IPTA_TIMESERIES: { label: 'SBAS IPTA 时序', featureScope: 'sbas_insar' },
  REBUILD_SBAS_INSAR_CATALOG: { label: 'SBAS 结果目录重建', featureScope: 'sbas_products' },
};

const PREFIX_POLICIES = [
  { prefix: 'WATER_GEOCODE_', label: '水体地理编码', featureScope: 'water' },
  { prefix: 'WATER_DETECT_', label: '水体检测', featureScope: 'water' },
  { prefix: 'WATER_FLOOD_', label: '洪涝检测', featureScope: 'water' },
  { prefix: 'FLOOD_SCENE_PREPROCESS_', label: '洪涝场景预处理', featureScope: 'flood' },
  { prefix: 'FLOOD_WATER_EXTRACTION_', label: '洪涝水体提取', featureScope: 'flood' },
  { prefix: 'FLOOD_DETECTION_', label: '洪涝检测', featureScope: 'flood' },
  { prefix: 'GF3_PROCESS_', label: 'GF3 场景处理', featureScope: 'water' },
];

export function getTaskUiPolicy(taskType) {
  const normalized = String(taskType || '').trim().toUpperCase();
  if (!normalized) {
    return {
      taskType: '',
      label: '后台任务',
      featureScope: 'unknown',
      globalVisible: true,
      globalBlocking: false,
      localBlocking: true,
    };
  }
  const exact = TASK_UI_POLICIES[normalized];
  const prefix = PREFIX_POLICIES.find((item) => normalized.startsWith(item.prefix));
  const policy = exact || prefix || {};
  return {
    taskType: normalized,
    label: policy.label || normalized,
    featureScope: policy.featureScope || 'unknown',
    globalVisible: policy.globalVisible ?? true,
    globalBlocking: policy.globalBlocking ?? false,
    localBlocking: policy.localBlocking ?? true,
  };
}

export function isTaskGloballyBlocking(taskType) {
  return !!getTaskUiPolicy(taskType).globalBlocking;
}

export function getTaskTypeLabel(taskType) {
  return getTaskUiPolicy(taskType).label;
}
