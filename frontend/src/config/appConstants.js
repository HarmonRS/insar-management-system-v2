const TILE_BASE = String(import.meta.env.VITE_TILE_SERVER_URL || '')
  .trim()
  .replace(/\/+$/, '');
const TILE_TOKEN = String(import.meta.env.VITE_TILE_SERVER_TOKEN || '').trim();

const buildTileServerUrl = relativePath => {
  const normalizedPath = relativePath.startsWith('/') ? relativePath : `/${relativePath}`;
  const url = `${TILE_BASE}${normalizedPath}`;
  if (!TILE_TOKEN) {
    return url;
  }
  const separator = url.includes('?') ? '&' : '?';
  return `${url}${separator}token=${encodeURIComponent(TILE_TOKEN)}`;
};

export const BASE_LAYERS = {
  google_image: {
    label: 'Google 影像',
    url: buildTileServerUrl('/tiles/google_image/{z}/{x}/{y}.webp'),
    attribution: 'Google Imagery (Offline)',
  },
  gaode_image: {
    label: '高德影像',
    url: buildTileServerUrl('/tiles/gaode_image/{z}/{x}/{y}.webp'),
    attribution: 'Gaode Imagery (Offline)',
  },
  gaode_shp: {
    label: '高德矢量',
    url: buildTileServerUrl('/tiles/gaode_shp/{z}/{x}/{y}.webp'),
    attribution: 'Gaode Vector (Offline)',
  },
};

export const TILE_LAYER_DEFAULT_KEY = 'gaode_shp';

export const TILE_LAYER_OPTIONS = {
  minZoom: 0,
  maxZoom: 16,
  maxNativeZoom: 16,
  tms: false,
  updateWhenIdle: false,
  updateWhenZooming: false,
  keepBuffer: 2,
  reuseTiles: true,
  crossOrigin: 'anonymous',
};

export const TILE_LAYER_BOUNDS = [
  [43.425877000, 121.182221000],
  [53.563624000, 135.095670000],
];

export const NATIONAL_BOUNDARY_GEOJSON_URL = buildTileServerUrl('/geojson/全国行政区.geojson');

export const getBaseLayerConfig = key => BASE_LAYERS[key] || BASE_LAYERS[TILE_LAYER_DEFAULT_KEY];

export const PRODUCTION_WORKSPACE_TAB = 'production_management';
export const PRODUCTION_WORKSPACE_LEGACY_TABS = [
  'pairing',
  'pairs',
  'ps_results',
  'batches',
  'copier',
  'dinsar_production',
  'dinsar_products',
  'ps_production',
  'ps_products',
];

export const PRODUCTION_WORKSPACE_DINSAR_VIEWS = [
  {
    key: 'dinsar_pairing',
    label: '配对规划',
    description: '按时间基线、空间关系、覆盖重叠率和 AOI 生成 D-InSAR 候选干涉对。',
  },
  {
    key: 'dinsar_pairs',
    label: '候选对与批次',
    description: '审查候选干涉对，选择可进入生产的任务，并在同一流程中管理 D-InSAR 批次。',
  },
  {
    key: 'dinsar_prepare',
    label: '生产准备',
    description: '按批次解包源压缩包到本机 Task_Pool，或导出源压缩包分发包。',
  },
  {
    key: 'dinsar_runs',
    label: '生产运行',
    description: '运行任务编排、引擎切换与过程监控。',
  },
  {
    key: 'dinsar_products',
    label: '结果管理',
    description: '结果提取、标准目录发布与产品编目。',
  },
];

export const PRODUCTION_WORKSPACE_SBAS_VIEWS = [
  {
    key: 'sbas_insar_planning',
    label: '序列规划',
    description: '按生产区域发现 SBAS 候选序列，审查覆盖、时序密度、精轨和公共重叠范围。',
  },
  {
    key: 'sbas_insar_batches',
    label: '候选栈与 Run',
    description: '查看候选序列、Manifest 与已创建的 SBAS 生产 Run。',
  },
  {
    key: 'sbas_insar_prepare',
    label: '生产准备',
    description: '配置 DEM、处理器参数、Workflow Manifest 与生产前检查。',
  },
  {
    key: 'sbas_insar_runs',
    label: '生产运行',
    description: '跟踪 LandSAR/Gamma SBAS Run 状态、任务执行和阶段产物。',
  },
  {
    key: 'sbas_insar_products',
    label: '结果管理',
    description: '管理 Gamma SBAS LOS velocity、uncertainty、coverage 和监测点产品目录。',
  },
];

export const PRODUCTION_WORKSPACE_WORKBENCHES = [
  {
    key: 'dinsar_workbench',
    label: 'D-InSAR 工作台',
    description: '将配对规划、候选对与批次、生产准备、生产运行和结果管理集中到一条 D-InSAR 工作流。',
    defaultView: 'dinsar_pairing',
    views: PRODUCTION_WORKSPACE_DINSAR_VIEWS,
  },
  {
    key: 'sbas_workbench',
    label: 'SBAS-InSAR 工作台',
    description: '围绕 SBAS 序列发现、生产执行和结果 catalog 管理组织时序 InSAR 生产。',
    defaultView: 'sbas_insar_planning',
    views: PRODUCTION_WORKSPACE_SBAS_VIEWS,
  },
];

export const PRODUCTION_WORKSPACE_VIEWS = [
  ...PRODUCTION_WORKSPACE_DINSAR_VIEWS,
  ...PRODUCTION_WORKSPACE_SBAS_VIEWS,
  {
    key: 'lt1_production',
    label: '陆探一生产占位',
    description: 'LT-1 源压缩包本机登记，按需 materialize 到 Task_Pool；D-InSAR/SBAS 生产不走 UNC。',
  },
  {
    key: 'sentinel1_production',
    label: 'Sentinel-1 生产占位',
    description: 'Sentinel-1 ZIP/SAFE 与 EOF 精轨本机管理，按需解包；当前 SBAS 仅保留规划能力。',
  },
  {
    key: 'gf3_native_registration',
    label: '高分三结果登记',
    description: 'GF3 在外部 SARscape 服务生产，本机只登记复制回来的 _geo 二进制并生成 WebP。',
  },
];

export const PRODUCTION_WORKSPACE_ENTRY_TO_VIEW = Object.freeze({
  [PRODUCTION_WORKSPACE_TAB]: 'dinsar_pairing',
  pairing: 'dinsar_pairing',
  pairs: 'dinsar_pairs',
  ps_results: 'sbas_insar_planning',
  batches: 'dinsar_pairs',
  copier: 'dinsar_prepare',
  dinsar_production: 'dinsar_runs',
  dinsar_products: 'dinsar_products',
  ps_production: 'sbas_insar_runs',
  ps_products: 'sbas_insar_products',
});

export const PRODUCTION_WORKSPACE_ROUTE_TABS = new Set([
  PRODUCTION_WORKSPACE_TAB,
  ...PRODUCTION_WORKSPACE_LEGACY_TABS,
]);

export const LEFT_GROUP_LABELS = {
  data: '数据资产',
  production_management: '生产管理',
  result_extraction: '结果提取',
  insar_analysis: '形变分析',
  statistics: '综合统计',
  flood_analysis: '灾害分析',
  ops: '运行维护',
};

export const LEFT_GROUP_SECTIONS = {
  insar_analysis: [
    {
      key: 'dinsar',
      label: 'D-InSAR 判读',
      tabs: ['dinsar_results', 'dinsar_analysis'],
    },
    {
      key: 'psinsar',
      label: 'SBAS 形变',
      tabs: ['psinsar_analysis'],
    },
  ],
};

export const LEFT_GROUP_TABS = {
  data: ['ingest', 'asset_inventory', 'data', 'hazard'],
  production_management: [PRODUCTION_WORKSPACE_TAB],
  result_extraction: ['result_extraction'],
  insar_analysis: LEFT_GROUP_SECTIONS.insar_analysis.flatMap(section => section.tabs),
  statistics: ['statistics'],
  flood_analysis: ['flood_analysis'],
  ops: ['health', 'users', 'audit'],
};

export const LEFT_TAB_GROUP = Object.entries(LEFT_GROUP_TABS).reduce((acc, [group, tabs]) => {
  tabs.forEach(tab => {
    acc[tab] = group;
  });
  return acc;
}, {});

PRODUCTION_WORKSPACE_LEGACY_TABS.forEach(tab => {
  LEFT_TAB_GROUP[tab] = 'production_management';
});

export const LEFT_TAB_SECTION = Object.entries(LEFT_GROUP_SECTIONS).reduce((acc, [, sections]) => {
  sections.forEach(section => {
    section.tabs.forEach(tab => {
      acc[tab] = section.key;
    });
  });
  return acc;
}, {});

export const FULL_WIDTH_LEFT_TABS = new Set([
  ...PRODUCTION_WORKSPACE_ROUTE_TABS,
  'statistics',
  'result_extraction',
]);

export const ADMIN_ONLY_TABS = new Set([
  'ingest',
  'asset_inventory',
  'pairing',
  'pairs',
  'ps_results',
  'batches',
  'copier',
  PRODUCTION_WORKSPACE_TAB,
  ...PRODUCTION_WORKSPACE_LEGACY_TABS,
  'users',
  'audit',
]);

export const DEFAULT_LIST_PAGE_SIZE = 200;
export const PAGE_SIZE_OPTIONS = [50, 100, 200, 500];
export const BATCH_API_PAGE_LIMIT = 500;
export const BATCH_API_MAX_PAGES = 200;

export const SATELLITE_GROUPS = [
  { key: 'LT-1', label: 'LT-1', prefixes: ['LT1'] },
  { key: 'S1', label: 'Sentinel-1', prefixes: ['S1'] },
  { key: 'GF-3', label: 'GF-3', prefixes: ['GF3'] },
];

export const RADAR_SEARCH_DEFAULTS = {
  satellite: '',
  satellite_mode: '',
  receiving_station: '',
  imaging_mode: '',
  orbit_circle: '',
  acquisition_time_utc: '',
  product_type: '',
  polarization: '',
  product_level: '',
  product_unique_id: '',
  orbit_direction: '',
  has_orbit_data: '',
  imaging_date_from: '',
  imaging_date_to: '',
};

export const RADAR_SEARCH_OPTIONS_DEFAULTS = {
  satellite: [],
  satellite_mode: [],
  receiving_station: [],
  imaging_mode: [],
  orbit_circle: [],
  acquisition_time_utc: [],
  product_type: [],
  polarization: [],
  product_level: [],
  product_unique_id: [],
  orbit_direction: [],
  imaging_dates: [],
};
