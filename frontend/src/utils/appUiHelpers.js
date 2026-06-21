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
      return '入库监控';
    case 'asset_inventory':
      return '资产库存';
    case 'data':
      return '数据列表';
    case 'hazard':
      return '灾害点';
    case 'pairing':
      return 'D-InSAR配对规划';
    case 'pairs':
      return `D-InSAR候选对与批次 (${pairCount})`;
    case 'ps_results':
      return `SBAS序列规划 (${psResultCount})`;
    case 'batches':
      return 'D-InSAR候选对与批次';
    case 'copier':
      return 'D-InSAR生产准备';
    case 'production_management':
      return '生产管理';
    case 'idl':
      return 'D-InSAR生产（旧）';
    case 'dinsar_production':
      return 'D-InSAR生产运行';
    case 'dinsar_products':
      return 'D-InSAR结果管理';
    case 'ps_production':
      return 'SBAS-InSAR生产工作流';
    case 'ps_products':
      return 'SBAS-InSAR结果管理';
    case 'dinsar_results':
      return `D-InSAR结果 (${dinsarTotal})`;
    case 'dinsar_analysis':
      return 'D-InSAR分析';
    case 'psinsar_results':
      return 'SBAS-InSAR结果';
    case 'psinsar_analysis':
      return 'SBAS-InSAR分析';
    case 'ai_quality':
      return 'AI质量评估';
    case 'ai_diagnosis':
      return 'D-InSAR诊断';
    case 'landslide_segmentation':
      return '滑坡语义分割';
    case 'uav_image_analysis':
      return '无人机影像分析';
    case 'water':
      return '水体监测（旧）';
    case 'flood_analysis':
      return '洪涝灾害分析';
    case 'health':
      return '运维自检';
    case 'users':
      return '用户管理';
    case 'audit':
      return '审计日志';
    default:
      return tabKey;
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

export const formatUtc = (isoString, language) => {
  if (!isoString) return language === 'en' ? 'Unknown' : '未知';
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) return language === 'en' ? 'Unknown' : '未知';
  const pad = n => String(n).padStart(2, '0');
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())} UTC`;
};

export const formatYmd = (rawValue, language) => {
  const value = String(rawValue ?? '').trim();
  if (!value) return '-';

  const compactMatch = value.match(/^(\d{4})(\d{2})(\d{2})$/);
  if (compactMatch) {
    const [, yyyy, mm, dd] = compactMatch;
    return language === 'en'
      ? `${yyyy}-${mm}-${dd}`
      : `${yyyy}年${mm}月${dd}日`;
  }

  const dashMatch = value.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (dashMatch) {
    const [, yyyy, mm, dd] = dashMatch;
    return language === 'en'
      ? `${yyyy}-${mm}-${dd}`
      : `${yyyy}年${mm}月${dd}日`;
  }

  return value;
};

export const getPageHintText = (totalPages, language) => (
  language === 'en'
    ? `Tip: valid page range is 1-${totalPages}. Press Enter to jump.`
    : `提示：可跳转页码范围为 1-${totalPages}，按 Enter 可快速跳转。`
);

export const getPageInputErrorText = (rawValue, totalPages, language) => {
  const value = String(rawValue ?? '').trim();
  if (!value) {
    return language === 'en' ? 'Please enter a page number.' : '请输入页码。';
  }
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue) || !Number.isInteger(numericValue)) {
    return language === 'en' ? 'Page number must be an integer.' : '页码必须为整数。';
  }
  if (numericValue < 1 || numericValue > totalPages) {
    return language === 'en'
      ? `Page must be between 1 and ${totalPages}.`
      : `页码必须在 1-${totalPages} 之间。`;
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
