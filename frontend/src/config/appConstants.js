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
  updateWhenIdle: true,
  keepBuffer: 4,
};

export const TILE_LAYER_BOUNDS = [
  [43.425877000, 121.182221000],
  [53.563624000, 135.095670000],
];

export const NATIONAL_BOUNDARY_GEOJSON_URL = buildTileServerUrl('/geojson/全国行政区.geojson');

export const getBaseLayerConfig = key => BASE_LAYERS[key] || BASE_LAYERS[TILE_LAYER_DEFAULT_KEY];

export const PRODUCTION_WORKSPACE_TAB = 'production_management';
export const PRODUCTION_WORKSPACE_LEGACY_TABS = [
  'dinsar_production',
  'dinsar_products',
  'ps_production',
  'ps_products',
];

export const PRODUCTION_WORKSPACE_VIEWS = [
  {
    key: 'dinsar_runs',
    label: 'D-InSAR 运行',
    description: '运行任务编排、引擎切换与过程监控',
  },
  {
    key: 'timeseries_runs',
    label: '时序InSAR 运行',
    description: '当前默认接入 SBAS 流程，后续可扩展 PS-InSAR / SBAS-InSAR',
  },
  {
    key: 'dinsar_products',
    label: 'D-InSAR 产物',
    description: '结果提取、标准目录发布与产物编目',
  },
  {
    key: 'timeseries_products',
    label: '时序InSAR 产物',
    description: '当前统一登记时序产物，后续兼容多类型时序成果',
  },
];

export const PRODUCTION_WORKSPACE_ENTRY_TO_VIEW = Object.freeze({
  [PRODUCTION_WORKSPACE_TAB]: 'dinsar_runs',
  dinsar_production: 'dinsar_runs',
  dinsar_products: 'dinsar_products',
  ps_production: 'timeseries_runs',
  ps_products: 'timeseries_products',
});

export const PRODUCTION_WORKSPACE_ROUTE_TABS = new Set([
  PRODUCTION_WORKSPACE_TAB,
  ...PRODUCTION_WORKSPACE_LEGACY_TABS,
]);

export const LEFT_GROUP_LABELS = {
  data: '数据管理',
  production_planning: '生产规划',
  production_management: '生产管理',
  insar_analysis: 'InSAR形变分析',
  ai_analysis: 'AI分析',
  water: '水体监测',
  ops: '运行维护',
};

export const LEFT_GROUP_SECTIONS = {
  production_planning: [
    {
      key: 'planning',
      label: '规划编组',
      tabs: ['pairing', 'pairs', 'ps_results', 'batches'],
    },
    {
      key: 'dispatch',
      label: '数据分发',
      tabs: ['copier'],
    },
  ],
  insar_analysis: [
    {
      key: 'dinsar',
      label: 'D-InSAR',
      tabs: ['dinsar_results', 'dinsar_analysis'],
    },
    {
      key: 'psinsar',
      label: '时序InSAR',
      tabs: ['psinsar_results', 'psinsar_analysis'],
    },
  ],
  ai_analysis: [
    {
      key: 'deformation_ai',
      label: '形变智能分析',
      tabs: ['ai_quality', 'ai_diagnosis'],
    },
    {
      key: 'vision_ai',
      label: '遥感视觉分析',
      tabs: ['landslide_segmentation', 'uav_image_analysis'],
    },
  ],
};

export const LEFT_GROUP_TABS = {
  data: ['ingest', 'asset_inventory', 'data', 'hazard'],
  production_planning: LEFT_GROUP_SECTIONS.production_planning.flatMap(section => section.tabs),
  production_management: [PRODUCTION_WORKSPACE_TAB],
  insar_analysis: LEFT_GROUP_SECTIONS.insar_analysis.flatMap(section => section.tabs),
  ai_analysis: LEFT_GROUP_SECTIONS.ai_analysis.flatMap(section => section.tabs),
  water: ['water'],
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
  'water',
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
  is_envi_processed: '',
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
