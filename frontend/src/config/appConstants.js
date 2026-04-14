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

export const LEFT_GROUP_LABELS = {
  data: '数据管理',
  production: '生产规划',
  insar_analysis: 'InSAR形变分析',
  ai_analysis: 'AI分析',
  water: '水体监测',
  ops: '运行维护',
};

export const LEFT_GROUP_SECTIONS = {
  production: [
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
    {
      key: 'dinsar',
      label: 'D-InSAR',
      tabs: ['dinsar_production', 'dinsar_products'],
    },
    {
      key: 'psinsar',
      label: 'PS-InSAR',
      tabs: ['ps_production', 'ps_products'],
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
      label: 'PS-InSAR',
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
  data: ['ingest', 'data', 'hazard'],
  production: LEFT_GROUP_SECTIONS.production.flatMap(section => section.tabs),
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

export const LEFT_TAB_SECTION = Object.entries(LEFT_GROUP_SECTIONS).reduce((acc, [, sections]) => {
  sections.forEach(section => {
    section.tabs.forEach(tab => {
      acc[tab] = section.key;
    });
  });
  return acc;
}, {});

export const ADMIN_ONLY_TABS = new Set([
  'ingest',
  'pairing',
  'pairs',
  'ps_results',
  'batches',
  'copier',
  'dinsar_production',
  'dinsar_products',
  'ps_production',
  'ps_products',
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
