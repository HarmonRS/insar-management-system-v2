export const normalizeTaskStatus = value => (value || '').toString().toUpperCase();
export const normalizePreviewStatus = value => (value || 'NONE').toString().toUpperCase();

export const getPreviewStatusText = item => {
  if (item.previewFallbackInUse) return '回退';
  switch (normalizePreviewStatus(item.previewStatus)) {
    case 'READY':
      return '正常';
    case 'FAILED':
      return '失败';
    default:
      return '未建';
  }
};

export const getPreviewStatusClass = item => {
  if (item.previewFallbackInUse) return 'fallback';
  const status = normalizePreviewStatus(item.previewStatus);
  if (status === 'READY') return 'ready';
  if (status === 'FAILED') return 'failed';
  return 'none';
};

export const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

export const getLeftTabLabel = (tabKey, metrics = {}) => {
  const {
    pairCount = 0,
    psResultCount = 0,
    dinsarTotal = 0,
  } = metrics;

  switch (tabKey) {
    case 'ingest':
      return '数据接入';
    case 'asset_inventory':
      return '资产台账';
    case 'data':
      return '影像检索';
    case 'hazard':
      return '灾害点库';
    case 'statistics':
      return '综合统计';
    case 'pairing':
      return 'D-InSAR 配对规划';
    case 'pairs':
      return `D-InSAR 候选对与批次 (${pairCount})`;
    case 'ps_results':
      return `SBAS 序列规划 (${psResultCount})`;
    case 'batches':
      return 'D-InSAR 候选对与批次';
    case 'copier':
      return 'D-InSAR 生产准备';
    case 'production_management':
      return '生产管理';
    case 'idl':
      return 'D-InSAR 生产';
    case 'dinsar_production':
      return 'D-InSAR 生产运行';
    case 'dinsar_products':
      return 'D-InSAR 成果目录';
    case 'ps_production':
      return 'SBAS-InSAR 生产工作流';
    case 'ps_products':
      return 'SBAS-InSAR 成果目录';
    case 'dinsar_results':
      return `D-InSAR 结果判读 (${dinsarTotal})`;
    case 'dinsar_analysis':
      return 'D-InSAR 专题分析';
    case 'psinsar_results':
      return 'SBAS-InSAR 结果';
    case 'psinsar_analysis':
      return 'SBAS 形变分析';
    case 'result_extraction':
      return '结果提取';
    case 'ai_quality':
      return 'AI 质量评估';
    case 'ai_diagnosis':
      return 'D-InSAR 诊断';
    case 'landslide_segmentation':
      return '滑坡语义分割';
    case 'uav_image_analysis':
      return '无人机影像分析';
    case 'water':
      return '水体监测';
    case 'flood_analysis':
      return '洪涝灾害分析';
    case 'health':
      return '运行维护';
    case 'users':
      return '用户管理';
    case 'audit':
      return '审计日志';
    default:
      return tabKey;
  }
};

export const getLeftTabDescription = (tabKey) => {
  switch (tabKey) {
    case 'ingest':
      return '监控源数据、精轨和派生资产的接入任务，集中处理扫描、登记和运行记录。';
    case 'asset_inventory':
      return '查看源产品、精密轨道、绑定状态和开放问题，作为生产前的数据资产台账。';
    case 'data':
      return '按卫星、日期、轨道、AOI 和产品属性检索 SAR 影像，并在地图上核对覆盖范围。';
    case 'hazard':
      return '管理灾害点位与专题分析对象，为形变判读和洪涝分析提供空间参照。';
    case 'statistics':
      return '汇总源数据、生产成果、质量判读和缓存一致性，形成面向生产管理的统计视图。';
    case 'dinsar_results':
      return '核对 D-InSAR 形变结果、质量评分、标签和空间分布，支撑成果判读。';
    case 'dinsar_analysis':
      return '围绕选定 D-InSAR 结果开展专题分析、诊断和报告辅助。';
    case 'psinsar_analysis':
      return '查看 SBAS-InSAR 速率场、监测点和时序曲线，开展区域形变分析。';
    case 'result_extraction':
      return '统一提取三类正射生产成果、D-InSAR 成果和 SBAS-InSAR 成果，作为系统对外成果交付出口。';
    case 'flood_analysis':
      return '围绕洪涝场景开展水体提取、过程分析和专题制图。';
    case 'health':
      return '检查核心服务、数据目录、生产索引和运行环境，定位影响生产的阻断项。';
    case 'users':
      return '维护系统用户、角色与访问权限。';
    case 'audit':
      return '查看关键操作和生产任务的审计记录。';
    default:
      return '当前模块用于支撑科研工程生产流程。';
  }
};

export const getSelectedRegionTreeId = selection => (
  selection?.city || selection?.province || ''
);

export const getRegionNameByTreeId = (list, treeId) => (
  (list || []).find(item => item.tree_id === treeId)?.name || ''
);

export const getRegionDisplayName = (selection, options) => {
  const provinceName = getRegionNameByTreeId(options?.provinces, selection?.province);
  const cityName = getRegionNameByTreeId(options?.cities, selection?.city);
  return [provinceName, cityName].filter(Boolean).join(' / ');
};

export const buildRadarSearchFormData = ({ limit, offset, criteria, aoiMode, regionTreeId, aoiToken, files }) => {
  const formData = new FormData();
  formData.append('limit', String(limit));
  formData.append('offset', String(offset));

  Object.entries(criteria || {}).forEach(([key, rawValue]) => {
    if (rawValue === null || rawValue === undefined) return;
    const normalized = String(rawValue).trim();
    if (!normalized) return;
    formData.append(key, normalized);
  });

  if (aoiMode === 'region') {
    if (aoiToken) {
      formData.append('aoi_token', aoiToken);
    } else if (regionTreeId) {
      formData.append('region_tree_id', regionTreeId);
    }
  } else if (aoiMode === 'shp') {
    const fileList = files && files.length ? Array.from(files) : [];
    if (fileList.length > 0) {
      fileList.forEach(file => formData.append('files', file));
    } else if (aoiToken) {
      formData.append('aoi_token', aoiToken);
    }
  }

  return formData;
};

export const normalizeRadarSearchCriteria = (draft, defaults) => {
  const normalized = {};
  Object.entries(defaults || {}).forEach(([key]) => {
    const value = draft?.[key];
    normalized[key] = value === null || value === undefined ? '' : String(value).trim();
  });
  return normalized;
};

export const formatUtc = (isoString) => {
  if (!isoString) return '未知';
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) return '未知';
  const pad = n => String(n).padStart(2, '0');
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())} UTC`;
};

export const formatYmd = (rawValue) => {
  const value = String(rawValue ?? '').trim();
  if (!value) return '-';

  const compactMatch = value.match(/^(\d{4})(\d{2})(\d{2})$/);
  if (compactMatch) {
    const [, yyyy, mm, dd] = compactMatch;
    return `${yyyy}年${mm}月${dd}日`;
  }

  const dashMatch = value.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (dashMatch) {
    const [, yyyy, mm, dd] = dashMatch;
    return `${yyyy}年${mm}月${dd}日`;
  }

  return value;
};

export const getPageHintText = (totalPages) => (
  `提示：可跳转页码范围为 1-${totalPages}，按 Enter 可快速跳转。`
);

export const getPageInputErrorText = (rawValue, totalPages) => {
  const value = String(rawValue ?? '').trim();
  if (!value) {
    return '请输入页码。';
  }
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue) || !Number.isInteger(numericValue)) {
    return '页码必须为整数。';
  }
  if (numericValue < 1 || numericValue > totalPages) {
    return `页码必须在 1-${totalPages} 之间。`;
  }
  return '';
};

export const parseDatesFromName = (name, formatYmdFn) => {
  const matches = name.match(/(\d{8})/g);
  if (matches && matches.length >= 2) {
    return {
      master: formatYmdFn(matches[0]),
      slave: formatYmdFn(matches[1]),
    };
  }
  return null;
};

export const getStatusClass = (value, hasHealthStatus) => {
  if (!hasHealthStatus) return 'warn';
  return value ? 'ok' : 'fail';
};
