import { useCallback, useEffect, useMemo, useState } from 'react';

import UnifiedDatePicker from './components/UnifiedDatePicker';
import { getSearchOptions, searchRadarData } from './api/radar';
import {
  createFloodProduct,
  getFloodActiveRadarIds,
  getFloodDetectionPreview,
  getFloodDetections,
  getFloodImpact,
  getFloodDoneRadarIds,
  getFloodProductManifest,
  getFloodScenes,
  getFloodProducts,
  getFloodWaterExtractionPreview,
  getFloodWaterExtractions,
  resetFloodScene,
  runFloodOverlay,
  searchFloodDisasterPairs,
  submitFloodDetection,
  submitFloodPreprocess,
  submitFloodWaterExtraction,
} from './api/flood';
import { getRegionChildren } from './api/aoi';
import {
  buildRadarSearchFormData,
  formatYmd,
  normalizeRadarSearchCriteria,
} from './utils/appUiHelpers';
import { RADAR_SEARCH_DEFAULTS } from './config/appConstants';

const SEARCH_PAGE_SIZE = 30;
const LIST_PAGE_SIZE = 20;

const VIEWS = [
  { key: 'extract', label: '水体提取' },
  { key: 'detect', label: '洪涝检测' },
  { key: 'impact', label: '影响评估' },
  { key: 'results', label: '成果管理' },
];

const SENSOR_ROWS = [
  { name: 'LT-1', status: 'Gamma 标准化可用', tone: 'ok', capability: 'ENVI/SARscape' },
  { name: 'GF3', status: '分析就绪链路可用', tone: 'ok', capability: 'SARscape + GDAL' },
  { name: 'Sentinel-1', status: '待接入', tone: 'muted', capability: '导入适配器' },
];

const STATUS_META = {
  PENDING: { label: '等待中', tone: 'muted' },
  RUNNING: { label: '处理中', tone: 'info' },
  DONE: { label: '完成', tone: 'ok' },
  FAILED: { label: '失败', tone: 'bad' },
};

const palette = {
  bg: '#f6f8fb',
  panel: '#ffffff',
  panelSoft: '#f8fafc',
  border: '#d8e0ea',
  borderStrong: '#b8c4d4',
  text: '#111827',
  text2: '#334155',
  muted: '#64748b',
  subtle: '#94a3b8',
  primary: '#2563eb',
  primarySoft: '#dbeafe',
  teal: '#0f766e',
  tealSoft: '#ccfbf1',
  red: '#dc2626',
  redSoft: '#fee2e2',
  amber: '#d97706',
  amberSoft: '#ffedd5',
  green: '#16a34a',
  greenSoft: '#dcfce7',
};

const pageStyle = {
  minHeight: '100%',
  padding: 14,
  boxSizing: 'border-box',
  background: palette.bg,
  color: palette.text,
  fontSize: 13,
};

const panelStyle = {
  background: palette.panel,
  border: `1px solid ${palette.border}`,
  borderRadius: 8,
  padding: 12,
  marginBottom: 12,
};

const sectionStyle = {
  padding: '12px 0',
  borderTop: `1px solid ${palette.border}`,
};

const rowStyle = {
  background: palette.panel,
  border: `1px solid ${palette.border}`,
  borderRadius: 6,
  padding: 10,
};

const inputStyle = {
  width: '100%',
  boxSizing: 'border-box',
  border: `1px solid ${palette.border}`,
  borderRadius: 6,
  padding: '6px 8px',
  background: '#fff',
  color: palette.text,
  fontSize: 12,
};

function asStatus(value) {
  return String(value || '').toUpperCase();
}

function isActiveStatus(value) {
  const status = asStatus(value);
  return status === 'PENDING' || status === 'RUNNING';
}

function compactDate(value) {
  return String(value || '').replaceAll('-', '').trim();
}

function formatPercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-';
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function formatArea(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-';
  return `${Number(value).toFixed(2)} km²`;
}

function getErrorText(error, fallback = '操作失败') {
  return error?.response?.data?.detail || error?.message || fallback;
}

function getToneStyle(tone) {
  if (tone === 'ok') return { color: '#166534', background: palette.greenSoft, border: '#bbf7d0' };
  if (tone === 'info') return { color: '#1d4ed8', background: palette.primarySoft, border: '#bfdbfe' };
  if (tone === 'warn') return { color: '#b45309', background: palette.amberSoft, border: '#fed7aa' };
  if (tone === 'bad') return { color: '#b91c1c', background: palette.redSoft, border: '#fecaca' };
  return { color: palette.muted, background: palette.panelSoft, border: palette.border };
}

function StatusBadge({ status, tone, children }) {
  const meta = status ? STATUS_META[asStatus(status)] : null;
  const style = getToneStyle(tone || meta?.tone || 'muted');
  return (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      border: `1px solid ${style.border}`,
      borderRadius: 999,
      padding: '2px 7px',
      fontSize: 11,
      fontWeight: 600,
      color: style.color,
      background: style.background,
      whiteSpace: 'nowrap',
    }}>
      {children || meta?.label || status || '-'}
    </span>
  );
}

function buttonStyle(kind = 'secondary', disabled = false, extra = {}) {
  const styles = {
    primary: { color: '#fff', background: palette.primary, border: palette.primary },
    secondary: { color: palette.text2, background: '#fff', border: palette.borderStrong },
    quiet: { color: palette.muted, background: palette.panelSoft, border: palette.border },
    danger: { color: palette.red, background: '#fff', border: '#fecaca' },
    success: { color: palette.green, background: '#fff', border: '#bbf7d0' },
  }[kind] || {};
  return {
    border: `1px solid ${styles.border}`,
    background: styles.background,
    color: styles.color,
    borderRadius: 6,
    padding: '6px 10px',
    fontSize: 12,
    fontWeight: 600,
    cursor: disabled ? 'not-allowed' : 'pointer',
    opacity: disabled ? 0.55 : 1,
    whiteSpace: 'nowrap',
    ...extra,
  };
}

function EmptyState({ children }) {
  return (
    <div style={{
      border: `1px dashed ${palette.borderStrong}`,
      borderRadius: 6,
      padding: 16,
      color: palette.muted,
      fontSize: 12,
      textAlign: 'center',
      background: palette.panelSoft,
    }}>
      {children}
    </div>
  );
}

function SectionHeader({ title, actions }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 10 }}>
      <h3 style={{ margin: 0, fontSize: 14, lineHeight: 1.25 }}>{title}</h3>
      {actions && <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>{actions}</div>}
    </div>
  );
}

function KeyValue({ label, value, strong = false }) {
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{ color: palette.muted, fontSize: 11, marginBottom: 2 }}>{label}</div>
      <div style={{
        color: strong ? palette.text : palette.text2,
        fontSize: strong ? 14 : 12,
        fontWeight: strong ? 700 : 500,
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
      }}>
        {value || '-'}
      </div>
    </div>
  );
}

function RegionSelector({ options, selection, onProvinceChange, onCityChange, disabled = false }) {
  const provinces = options?.provinces || [];
  const cities = options?.cities || [];
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
      <select
        value={selection?.province || ''}
        onChange={event => onProvinceChange(event.target.value)}
        style={inputStyle}
        disabled={disabled}
      >
        <option value="">省/自治区</option>
        {provinces.map(item => (
          <option key={item.tree_id} value={item.tree_id}>{item.name}</option>
        ))}
      </select>
      <select
        value={selection?.city || ''}
        onChange={event => onCityChange(event.target.value)}
        style={inputStyle}
        disabled={disabled || !selection?.province}
      >
        <option value="">市/州（可选）</option>
        {cities.map(item => (
          <option key={item.tree_id} value={item.tree_id}>{item.name}</option>
        ))}
      </select>
    </div>
  );
}

function normalizeMapPreview(preview) {
  if (!preview) return null;
  const image = preview.image_b64 || preview.png_base64;
  let bounds = preview.bounds;
  if (bounds && !Array.isArray(bounds)) {
    bounds = [bounds.min_lat, bounds.min_lon, bounds.max_lat, bounds.max_lon];
  }
  if (!image || !Array.isArray(bounds) || bounds.length !== 4) return null;
  return { image_b64: image, bounds };
}

function SceneRow({ scene, readOnly, onShowMap, onExtractWater, onReset }) {
  const status = asStatus(scene.status);
  const canExtract = status === 'DONE';
  const [coverageVisible, setCoverageVisible] = useState(false);
  const handleToggleCoverage = () => {
    const visible = onShowMap(scene);
    if (typeof visible === 'boolean') setCoverageVisible(visible);
  };
  return (
    <div style={rowStyle}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 8, alignItems: 'start' }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <strong>场景 #{scene.id}</strong>
            <StatusBadge status={scene.status} />
            <span style={{ color: palette.muted }}>{scene.satellite || '-'}</span>
            <span style={{ color: palette.muted }}>{formatYmd(scene.imaging_date, 'zh')}</span>
            {scene.polarization && <span style={{ color: palette.subtle }}>{scene.polarization}</span>}
          </div>
          <div style={{ color: palette.subtle, fontSize: 11, marginTop: 5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={scene.geo_path || scene.error_msg || ''}>
            {[scene.imaging_mode, scene.product_level, scene.geo_path ? scene.geo_path.split(/[\\/]/).pop() : scene.error_msg || `Radar ID ${scene.radar_data_id}`].filter(Boolean).join(' · ')}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          <button type="button" style={buttonStyle(coverageVisible ? 'success' : 'quiet', !scene.coverage_polygon)} disabled={!scene.coverage_polygon} onClick={handleToggleCoverage}>
            {coverageVisible ? '清除范围' : '显示范围'}
          </button>
          <button type="button" style={buttonStyle('primary', readOnly || !canExtract)} disabled={readOnly || !canExtract} onClick={() => onExtractWater(scene)}>执行提取</button>
          {isActiveStatus(scene.status) && (
            <button type="button" style={buttonStyle('danger', readOnly)} disabled={readOnly} onClick={() => onReset(scene)}>重置</button>
          )}
        </div>
      </div>
    </div>
  );
}

function WaterResultRow({ item, onShowMap }) {
  return (
    <div style={rowStyle}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 8, alignItems: 'center' }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <strong>水体提取 #{item.id}</strong>
            <StatusBadge status={item.status} />
            {item.scene_id && <span style={{ color: palette.muted }}>场景 #{item.scene_id}</span>}
            {item.satellite && <span style={{ color: palette.muted }}>{item.satellite}</span>}
            {item.imaging_date && <span style={{ color: palette.muted }}>{formatYmd(item.imaging_date, 'zh')}</span>}
            {item.polarization && <span style={{ color: palette.subtle }}>{item.polarization}</span>}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 8, marginTop: 8 }}>
            <KeyValue label="面积" value={formatArea(item.water_area_km2)} strong />
            <KeyValue label="像素" value={item.water_pixel_count?.toLocaleString?.() || '-'} />
            <KeyValue label="阈值" value={item.otsu_threshold_db != null ? Number(item.otsu_threshold_db).toFixed(2) : '-'} />
          </div>
          {item.error_msg && <div style={{ color: palette.red, fontSize: 11, marginTop: 6 }}>{item.error_msg}</div>}
        </div>
        <button type="button" style={buttonStyle('secondary', asStatus(item.status) !== 'DONE')} disabled={asStatus(item.status) !== 'DONE'} onClick={() => onShowMap(item)}>显示图层</button>
      </div>
    </div>
  );
}

function FloodProductRow({ product, onOpenManifest }) {
  return (
    <div style={rowStyle}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 8, alignItems: 'center' }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
            <strong>{product.product_id || `成果 #${product.id}`}</strong>
            <StatusBadge status={product.status} />
            {product.detection_id && <span style={{ color: palette.muted }}>洪涝检测 #{product.detection_id}</span>}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 8, marginTop: 8 }}>
            <KeyValue label="新增淹没面积" value={formatArea(product.flood_area_km2 || product.summary?.flood_area_km2)} strong />
            <KeyValue label="影响面积" value={formatArea(product.affected_area_km2)} />
            <KeyValue label="发布时间" value={formatYmd(product.created_at, 'zh')} />
          </div>
        </div>
        <button type="button" style={buttonStyle('secondary')} onClick={() => onOpenManifest(product)}>查看清单</button>
      </div>
    </div>
  );
}

function FloodEventRow({ event, mapLayerVis, mapLoading, productBusy, onShowMap, onToggleLayer, onSelectImpact, onCreateProduct }) {
  const done = asStatus(event.status) === 'DONE';
  const preDate = formatYmd(event.pre_imaging_date, 'zh');
  const postDate = formatYmd(event.post_imaging_date, 'zh');
  const layers = mapLayerVis[event.id];
  return (
    <div style={rowStyle}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', minWidth: 0 }}>
          <strong>洪涝检测 #{event.id}</strong>
          <StatusBadge status={event.status} />
          <span style={{ color: palette.muted }}>{event.pre_satellite || event.post_satellite || '-'}</span>
        </div>
        <button type="button" style={buttonStyle('secondary', !done || mapLoading)} disabled={!done || mapLoading} onClick={() => onShowMap(event)}>
          {mapLoading ? '显示中' : '显示图层'}
        </button>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 8, marginTop: 10 }}>
        <KeyValue label="灾前" value={preDate} />
        <KeyValue label="灾后" value={postDate} />
        <KeyValue label="新增淹没面积" value={formatArea(event.flood_area_km2)} strong />
        <KeyValue label="稳定水体" value={formatArea(event.stable_water_area_km2)} />
      </div>
      {event.error_msg && <div style={{ color: palette.red, fontSize: 11, marginTop: 8 }}>{event.error_msg}</div>}
      {(layers || onSelectImpact || onCreateProduct) && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 9 }}>
          {layers && [
            ['pre', '灾前影像'],
            ['post', '灾后影像'],
            ['classified', '分类结果'],
          ].map(([key, label]) => (
            <button
              key={key}
              type="button"
              style={buttonStyle(layers[key] ? 'success' : 'quiet', false, { padding: '3px 8px', fontSize: 11 })}
              onClick={() => onToggleLayer(event.id, key, !layers[key])}
            >
              {layers[key] ? '隐藏' : '显示'}{label}
            </button>
          ))}
          {onSelectImpact && (
            <button type="button" style={buttonStyle('secondary', false, { padding: '3px 8px', fontSize: 11 })} onClick={() => onSelectImpact(event)}>
              影响评估
            </button>
          )}
          {onCreateProduct && (
            <button type="button" style={buttonStyle('primary', productBusy, { padding: '3px 8px', fontSize: 11 })} disabled={productBusy} onClick={() => onCreateProduct(event)}>
              {productBusy ? '发布中' : '发布成果'}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export default function FloodAnalysisWorkspace({
  readOnly = false,
  onTaskStart,
  floodPanel = {},
  language = 'zh',
}) {
  const [activeView, setActiveView] = useState('extract');
  const [message, setMessage] = useState(null);
  const [actionBusy, setActionBusy] = useState('');

  const [searchOptions, setSearchOptions] = useState({ satellite: [], imaging_mode: [], product_level: [], polarization: [] });
  const [radarDraft, setRadarDraft] = useState({ ...RADAR_SEARCH_DEFAULTS });
  const [radarResults, setRadarResults] = useState([]);
  const [radarTotal, setRadarTotal] = useState(0);
  const [radarPage, setRadarPage] = useState(0);
  const [radarLoading, setRadarLoading] = useState(false);
  const [radarSearched, setRadarSearched] = useState(false);
  const [selectedRadars, setSelectedRadars] = useState([]);
  const [sourceCoverageVisibleIds, setSourceCoverageVisibleIds] = useState(() => new Set());
  const [sourcePreviewVisibleIds, setSourcePreviewVisibleIds] = useState(() => new Set());

  const [scenes, setScenes] = useState([]);
  const [scenesTotal, setScenesTotal] = useState(0);
  const [scenesPage, setScenesPage] = useState(0);
  const [scenesLoading, setScenesLoading] = useState(false);
  const [doneRadarIds, setDoneRadarIds] = useState([]);
  const [activeRadarIds, setActiveRadarIds] = useState([]);

  const [waterResults, setWaterResults] = useState([]);
  const [waterTotal, setWaterTotal] = useState(0);
  const [waterPage, setWaterPage] = useState(0);
  const [waterLoading, setWaterLoading] = useState(false);

  const [sourceAoiMode, setSourceAoiMode] = useState('none');
  const [sourceRegionOptions, setSourceRegionOptions] = useState({ provinces: [], cities: [] });
  const [sourceRegionSelection, setSourceRegionSelection] = useState({ province: '', city: '' });
  const [regionLoading, setRegionLoading] = useState(false);
  const [regionError, setRegionError] = useState('');

  const [disasterName, setDisasterName] = useState('');
  const [disasterDate, setDisasterDate] = useState('');
  const [preWindowDays, setPreWindowDays] = useState(30);
  const [postWindowDays, setPostWindowDays] = useState(30);
  const [minAoiCoverage, setMinAoiCoverage] = useState(0.2);
  const [pairRegionOptions, setPairRegionOptions] = useState({ provinces: [], cities: [] });
  const [pairRegionSelection, setPairRegionSelection] = useState({ province: '', city: '' });
  const [pairSearchSummary, setPairSearchSummary] = useState(null);

  const [overlapThreshold, setOverlapThreshold] = useState(0.3);
  const [refine, setRefine] = useState(false);
  const [pairSearching, setPairSearching] = useState(false);
  const [candidatePairs, setCandidatePairs] = useState([]);
  const [selectedPairIdx, setSelectedPairIdx] = useState(null);

  const [floodEvents, setFloodEvents] = useState([]);
  const [floodLoading, setFloodLoading] = useState(false);
  const [floodProducts, setFloodProducts] = useState([]);
  const [productLoading, setProductLoading] = useState(false);
  const [productBusyId, setProductBusyId] = useState(null);
  const [mapLoadingId, setMapLoadingId] = useState(null);
  const [mapLayerVis, setMapLayerVis] = useState({});
  const [selectedImpactEventId, setSelectedImpactEventId] = useState('');
  const [impactLoading, setImpactLoading] = useState(false);
  const [overlayRunning, setOverlayRunning] = useState(false);
  const [impactResult, setImpactResult] = useState(null);
  const [resultMode, setResultMode] = useState('flood');

  const readyScenes = useMemo(() => scenes.filter(item => asStatus(item.status) === 'DONE'), [scenes]);
  const doneFloodEvents = useMemo(() => floodEvents.filter(item => asStatus(item.status) === 'DONE'), [floodEvents]);
  const runningCount = useMemo(() => (
    scenes.filter(item => isActiveStatus(item.status)).length
    + waterResults.filter(item => isActiveStatus(item.status)).length
    + floodEvents.filter(item => isActiveStatus(item.status)).length
  ), [scenes, waterResults, floodEvents]);

  const selectedPair = selectedPairIdx === null ? null : candidatePairs[selectedPairIdx];
  const selectedImpactEvent = useMemo(
    () => doneFloodEvents.find(item => String(item.id) === String(selectedImpactEventId)) || doneFloodEvents[0] || null,
    [doneFloodEvents, selectedImpactEventId],
  );
  const selectedSourceRegionTreeId = sourceRegionSelection.city || sourceRegionSelection.province || '';
  const selectedPairRegionTreeId = pairRegionSelection.city || pairRegionSelection.province || '';

  const showMessage = useCallback((type, text) => {
    setMessage({ type, text });
  }, []);

  const loadRegionProvinces = useCallback(async () => {
    setRegionLoading(true);
    setRegionError('');
    try {
      const data = await getRegionChildren('1');
      const provinces = data.children || [];
      setSourceRegionOptions(prev => ({ ...prev, provinces }));
      setPairRegionOptions(prev => ({ ...prev, provinces }));
    } catch (error) {
      setRegionError(getErrorText(error, '行政区加载失败'));
    } finally {
      setRegionLoading(false);
    }
  }, []);

  const updateRegionProvince = useCallback(async (target, provinceTreeId) => {
    const setSelection = target === 'source' ? setSourceRegionSelection : setPairRegionSelection;
    const setOptions = target === 'source' ? setSourceRegionOptions : setPairRegionOptions;
    setSelection({ province: provinceTreeId, city: '' });
    setOptions(prev => ({ ...prev, cities: [] }));
    if (!provinceTreeId) return;

    setRegionLoading(true);
    setRegionError('');
    try {
      const data = await getRegionChildren(provinceTreeId);
      setOptions(prev => ({ ...prev, cities: data.children || [] }));
    } catch (error) {
      setRegionError(getErrorText(error, '行政区加载失败'));
    } finally {
      setRegionLoading(false);
    }
  }, []);

  const updateRegionCity = useCallback((target, cityTreeId) => {
    const setSelection = target === 'source' ? setSourceRegionSelection : setPairRegionSelection;
    setSelection(prev => ({ ...prev, city: cityTreeId }));
  }, []);

  const loadStatusIds = useCallback(async () => {
    try {
      const [done, active] = await Promise.all([getFloodDoneRadarIds(), getFloodActiveRadarIds()]);
      setDoneRadarIds(done || []);
      setActiveRadarIds(active || []);
    } catch {
      setDoneRadarIds([]);
      setActiveRadarIds([]);
    }
  }, []);

  const loadScenes = useCallback(async (page = scenesPage) => {
    setScenesLoading(true);
    try {
      const res = (await getFloodScenes(LIST_PAGE_SIZE, page * LIST_PAGE_SIZE)).data;
      setScenes(res.items || []);
      setScenesTotal(res.total || 0);
      setScenesPage(page);
    } catch (error) {
      setScenes([]);
      setScenesTotal(0);
      showMessage('error', `分析就绪场景加载失败：${getErrorText(error)}`);
    } finally {
      setScenesLoading(false);
    }
  }, [scenesPage, showMessage]);

  const loadWaterResults = useCallback(async (page = waterPage) => {
    setWaterLoading(true);
    try {
      const res = (await getFloodWaterExtractions(LIST_PAGE_SIZE, page * LIST_PAGE_SIZE)).data;
      setWaterResults(res.items || []);
      setWaterTotal(res.total || 0);
      setWaterPage(page);
    } catch (error) {
      setWaterResults([]);
      setWaterTotal(0);
      showMessage('error', `水体提取成果加载失败：${getErrorText(error)}`);
    } finally {
      setWaterLoading(false);
    }
  }, [waterPage, showMessage]);

  const loadFloodEvents = useCallback(async () => {
    setFloodLoading(true);
    try {
      const res = (await getFloodDetections()).data;
      setFloodEvents(res.items || []);
    } catch (error) {
      setFloodEvents([]);
      showMessage('error', `洪涝检测成果加载失败：${getErrorText(error)}`);
    } finally {
      setFloodLoading(false);
    }
  }, [showMessage]);

  const loadFloodProducts = useCallback(async () => {
    setProductLoading(true);
    try {
      const res = (await getFloodProducts({ limit: LIST_PAGE_SIZE, offset: 0 })).data;
      setFloodProducts(res.items || []);
    } catch (error) {
      setFloodProducts([]);
      showMessage('error', `发布成果加载失败：${getErrorText(error)}`);
    } finally {
      setProductLoading(false);
    }
  }, [showMessage]);

  const refreshAll = useCallback(async () => {
    await Promise.all([
      loadStatusIds(),
      loadScenes(scenesPage),
      loadWaterResults(waterPage),
      loadFloodEvents(),
      loadFloodProducts(),
    ]);
  }, [loadFloodEvents, loadFloodProducts, loadScenes, loadStatusIds, loadWaterResults, scenesPage, waterPage]);

  useEffect(() => {
    getSearchOptions()
      .then(data => setSearchOptions({
        satellite: data.satellite || [],
        imaging_mode: data.imaging_mode || [],
        product_level: data.product_level || [],
        polarization: data.polarization || [],
      }))
      .catch(() => {});
    loadRegionProvinces();
    refreshAll();
  }, [loadRegionProvinces, refreshAll]);

  useEffect(() => {
    if (!runningCount) return undefined;
    const timer = window.setInterval(() => {
      refreshAll();
    }, 6000);
    return () => window.clearInterval(timer);
  }, [refreshAll, runningCount]);

  useEffect(() => {
    if (!selectedImpactEventId && doneFloodEvents[0]) {
      setSelectedImpactEventId(String(doneFloodEvents[0].id));
    }
  }, [doneFloodEvents, selectedImpactEventId]);

  const updateRadarDraft = (key, value) => {
    setRadarDraft(prev => ({ ...prev, [key]: value }));
  };

  const runRadarSearch = async (page = 0) => {
    setRadarLoading(true);
    setRadarSearched(true);
    try {
      if (sourceAoiMode === 'region' && !selectedSourceRegionTreeId) {
        throw new Error('请选择用于筛选的行政区。');
      }
      const criteria = normalizeRadarSearchCriteria(radarDraft, RADAR_SEARCH_DEFAULTS);
      const formData = buildRadarSearchFormData({
        limit: SEARCH_PAGE_SIZE,
        offset: page * SEARCH_PAGE_SIZE,
        criteria,
        aoiMode: sourceAoiMode,
        regionTreeId: sourceAoiMode === 'region' ? selectedSourceRegionTreeId : '',
      });
      const data = await searchRadarData(formData);
      setRadarResults(data.items || []);
      setRadarTotal(data.total || 0);
      setRadarPage(page);
    } catch (error) {
      setRadarResults([]);
      setRadarTotal(0);
      showMessage('error', `雷达数据查询失败：${getErrorText(error)}`);
    } finally {
      setRadarLoading(false);
    }
  };

  const resetRadarSearch = () => {
    setRadarDraft({ ...RADAR_SEARCH_DEFAULTS });
    setSourceAoiMode('none');
    setSourceRegionSelection({ province: '', city: '' });
    setSourceRegionOptions(prev => ({ ...prev, cities: [] }));
    setRadarResults([]);
    setRadarTotal(0);
    setRadarPage(0);
    setRadarSearched(false);
    setSelectedRadars([]);
  };

  const toggleRadarSelection = (item) => {
    setSelectedRadars(prev => (
      prev.some(row => row.id === item.id)
        ? prev.filter(row => row.id !== item.id)
        : [...prev, item]
    ));
  };

  const updateVisibleIdSet = (setter, itemId, visible) => {
    setter(prev => {
      const next = new Set(prev);
      if (visible) next.add(String(itemId));
      else next.delete(String(itemId));
      return next;
    });
  };

  const handleToggleSourceCoverage = (item) => {
    const handler = floodPanel.onShowSourceSceneOnMap || floodPanel.onShowOnMap;
    if (!handler) return;
    const visible = handler(item);
    if (typeof visible === 'boolean') {
      updateVisibleIdSet(setSourceCoverageVisibleIds, item.id, visible);
      showMessage(visible ? 'success' : 'info', visible ? `范围框 #${item.id} 已显示。` : `范围框 #${item.id} 已清除。`);
    }
  };

  const handleToggleSourcePreview = (item) => {
    const handler = floodPanel.onShowSourcePreviewOnMap || floodPanel.onShowSourceSceneOnMap || floodPanel.onShowOnMap;
    if (!handler) return;
    const visible = handler(item);
    if (typeof visible === 'boolean') {
      updateVisibleIdSet(setSourcePreviewVisibleIds, item.id, visible);
      showMessage(visible ? 'success' : 'info', visible ? `源影像 #${item.id} 已显示。` : `源影像 #${item.id} 已清除。`);
    }
  };

  const handleSubmitPreprocess = async () => {
    if (readOnly || selectedRadars.length === 0) return;
    setActionBusy('geocode');
    showMessage('info', `正在提交 ${selectedRadars.length} 景分析就绪影像生成任务...`);
    onTaskStart?.(null, '正在提交分析就绪影像生成任务...');
    let ok = 0;
    let fail = 0;
    for (const radar of selectedRadars) {
      try {
        const res = await submitFloodPreprocess({ radar_data_id: radar.id });
        ok += 1;
        if (res.data?.task_id) onTaskStart?.(res.data.task_id, '分析就绪影像生成任务已启动');
      } catch {
        fail += 1;
      }
    }
    setActionBusy('');
    setSelectedRadars([]);
    await refreshAll();
    showMessage(fail ? 'warn' : 'success', fail ? `提交完成：${ok} 成功，${fail} 失败。` : `已提交 ${ok} 个分析就绪影像生成任务。`);
  };

  const handleResetScene = async (scene) => {
    if (readOnly) return;
    setActionBusy(`reset_scene_${scene.id}`);
    try {
      await resetFloodScene(scene.id);
      await refreshAll();
      showMessage('success', `场景 #${scene.id} 已重置为失败状态。`);
    } catch (error) {
      showMessage('error', `场景重置失败：${getErrorText(error)}`);
    } finally {
      setActionBusy('');
    }
  };

  const handleExtractWater = async (scene) => {
    if (readOnly || asStatus(scene.status) !== 'DONE') return;
    setActionBusy(`water_${scene.id}`);
    showMessage('info', `正在提交场景 #${scene.id} 的水体提取任务...`);
    onTaskStart?.(null, '正在提交水体提取任务...');
    try {
      const res = await submitFloodWaterExtraction({ scene_id: scene.id });
      if (res.data?.task_id) onTaskStart?.(res.data.task_id, '水体提取任务已启动');
      await loadWaterResults(0);
      showMessage('success', `场景 #${scene.id} 的水体提取任务已提交。`);
    } catch (error) {
      showMessage('error', `水体提取提交失败：${getErrorText(error)}`);
    } finally {
      setActionBusy('');
    }
  };

  const handleShowWaterResult = async (item) => {
    setMapLoadingId(`water_${item.id}`);
    try {
      const preview = normalizeMapPreview(await getFloodWaterExtractionPreview(item.id));
      if (!preview) throw new Error('预览数据不完整');
      floodPanel.onShowFloodOnMap?.(
        { id: `water_${item.id}`, label: `水体提取 #${item.id}` },
        { classified: preview },
      );
      showMessage('success', `水体提取 #${item.id} 已显示在地图中。`);
    } catch (error) {
      showMessage('error', `水体图层显示失败：${getErrorText(error)}`);
    } finally {
      setMapLoadingId(null);
    }
  };

  const handleFindPairs = async () => {
    setPairSearching(true);
    setCandidatePairs([]);
    setSelectedPairIdx(null);
    setPairSearchSummary(null);
    try {
      if (!compactDate(disasterDate)) {
        throw new Error('请选择灾害发生日期。');
      }
      if (!selectedPairRegionTreeId) {
        throw new Error('请选择灾害影响位置。');
      }
      const data = await searchFloodDisasterPairs({
        disaster_name: disasterName || undefined,
        disaster_date: compactDate(disasterDate),
        region_tree_id: selectedPairRegionTreeId,
        pre_window_days: Number(preWindowDays) || 30,
        post_window_days: Number(postWindowDays) || 30,
        min_aoi_coverage_ratio: Number(minAoiCoverage) || 0,
        min_pair_overlap_ratio: Number(overlapThreshold) || 0,
        polarization: radarDraft.polarization || undefined,
        imaging_mode: radarDraft.imaging_mode || undefined,
        product_level: radarDraft.product_level || undefined,
        require_same_polarization: true,
        require_same_imaging_mode: false,
      });
      const pairs = data.candidate_pairs || data.pairs || [];
      setPairSearchSummary(data.summary || null);
      setCandidatePairs(pairs);
      setSelectedPairIdx(pairs.length ? 0 : null);
      showMessage(
        pairs.length ? 'success' : 'warn',
        pairs.length ? `找到 ${pairs.length} 组候选配对。` : (data.warnings?.[0] || '没有找到满足条件的配对。'),
      );
    } catch (error) {
      showMessage('error', `配对推荐失败：${getErrorText(error)}`);
    } finally {
      setPairSearching(false);
    }
  };

  const handleSubmitFloodDetection = async () => {
    if (readOnly || !selectedPair) return;
    setActionBusy('flood_detect');
    onTaskStart?.(null, '正在提交洪涝检测任务...');
    try {
      const res = await submitFloodDetection({
        pre_scene_id: selectedPair.pre.id,
        post_scene_id: selectedPair.post.id,
        refine,
      });
      if (res.data?.task_id) onTaskStart?.(res.data.task_id, '洪涝检测任务已启动');
      await loadFloodEvents();
      showMessage('success', `洪涝检测任务已提交：场景 #${selectedPair.pre.id} -> #${selectedPair.post.id}`);
      setActiveView('results');
      setResultMode('flood');
    } catch (error) {
      showMessage('error', `洪涝检测提交失败：${getErrorText(error)}`);
    } finally {
      setActionBusy('');
    }
  };

  const handleShowFloodEvent = async (event) => {
    setMapLoadingId(event.id);
    try {
      const [pre, post, classified] = await Promise.all([
        getFloodDetectionPreview(event.id, 'pre').catch(() => null),
        getFloodDetectionPreview(event.id, 'post').catch(() => null),
        getFloodDetectionPreview(event.id, 'classified').catch(() => null),
      ]);
      const layers = {
        pre: normalizeMapPreview(pre),
        post: normalizeMapPreview(post),
        classified: normalizeMapPreview(classified),
      };
      floodPanel.onShowFloodOnMap?.(event, layers);
      setMapLayerVis(prev => ({
        ...prev,
        [event.id]: {
          pre: !!layers.pre,
          post: !!layers.post,
          classified: !!layers.classified,
        },
      }));
      showMessage('success', `洪涝检测 #${event.id} 已显示在地图中。`);
    } catch (error) {
      showMessage('error', `洪涝图层显示失败：${getErrorText(error)}`);
    } finally {
      setMapLoadingId(null);
    }
  };

  const handleShowPairCoverage = (pair) => {
    if (!pair?.pre?.coverage_polygon || !pair?.post?.coverage_polygon) {
      showMessage('warn', '该候选配对缺少覆盖范围，无法显示。');
      return;
    }
    floodPanel.onShowFloodPairOnMap?.(pair);
    showMessage('success', '候选配对覆盖范围已显示。');
  };

  const handleShowFloodVector = () => {
    if (!impactResult?.flood_vector_geojson) {
      showMessage('warn', '该影响评估结果没有可用洪涝范围矢量。');
      return;
    }
    floodPanel.onShowFloodVectorOnMap?.(impactResult);
    showMessage('success', '洪涝范围矢量已显示。');
  };

  const loadImpactResult = useCallback(async (detectionId, { silent = false } = {}) => {
    if (!detectionId) return;
    setImpactLoading(true);
    try {
      const data = await getFloodImpact(detectionId);
      setImpactResult(data);
      if (!silent && data?.warnings?.includes?.('overlay has not been run')) {
        showMessage('warn', '该洪涝检测尚未执行影响评估。');
      }
    } catch (error) {
      setImpactResult(null);
      if (!silent) showMessage('error', `影响评估结果加载失败：${getErrorText(error)}`);
    } finally {
      setImpactLoading(false);
    }
  }, [showMessage]);

  const handleRunOverlay = async () => {
    if (readOnly || !selectedImpactEvent) return;
    setOverlayRunning(true);
    try {
      await runFloodOverlay(selectedImpactEvent.id, { near_threshold_m: 500 });
      await loadImpactResult(selectedImpactEvent.id, { silent: true });
      await loadFloodEvents();
      showMessage('success', `洪涝检测 #${selectedImpactEvent.id} 影响评估已完成。`);
    } catch (error) {
      showMessage('error', `影响评估失败：${getErrorText(error)}`);
    } finally {
      setOverlayRunning(false);
    }
  };

  const handleCreateFloodProduct = async (event) => {
    if (readOnly || !event) return;
    setProductBusyId(event.id);
    try {
      await createFloodProduct(event.id);
      await loadFloodProducts();
      showMessage('success', `洪涝检测 #${event.id} 成果已发布。`);
      setResultMode('products');
    } catch (error) {
      showMessage('error', `成果发布失败：${getErrorText(error)}`);
    } finally {
      setProductBusyId(null);
    }
  };

  const handleOpenProductManifest = async (product) => {
    try {
      const manifest = await getFloodProductManifest(product.id || product.product_id);
      showMessage('success', `成果清单已读取：${manifest?.schema || product.product_id || product.id}`);
    } catch (error) {
      showMessage('error', `成果清单读取失败：${getErrorText(error)}`);
    }
  };

  const handleToggleFloodLayer = (eventId, key, visible) => {
    setMapLayerVis(prev => ({ ...prev, [eventId]: { ...prev[eventId], [key]: visible } }));
    floodPanel.onToggleFloodLayer?.(eventId, key, visible);
  };

  const handleSelectImpact = (event) => {
    setSelectedImpactEventId(String(event.id));
    setImpactResult(null);
    setActiveView('impact');
  };

  useEffect(() => {
    if (activeView !== 'impact' || !selectedImpactEvent) return;
    loadImpactResult(selectedImpactEvent.id, { silent: true });
  }, [activeView, loadImpactResult, selectedImpactEvent]);

  const radarTotalPages = Math.ceil(radarTotal / SEARCH_PAGE_SIZE);
  const scenesTotalPages = Math.ceil(scenesTotal / LIST_PAGE_SIZE);
  const waterTotalPages = Math.ceil(waterTotal / LIST_PAGE_SIZE);

  return (
    <div style={pageStyle}>
      <header style={panelStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 10 }}>
          <div>
            <h2 style={{ margin: 0, fontSize: 18, lineHeight: 1.25 }}>洪涝灾害分析</h2>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 8 }}>
              {SENSOR_ROWS.map(item => (
                <StatusBadge key={item.name} tone={item.tone}>{item.name} · {item.status}</StatusBadge>
              ))}
            </div>
          </div>
          <button type="button" style={buttonStyle('secondary', actionBusy === 'refresh')} disabled={actionBusy === 'refresh'} onClick={refreshAll}>刷新</button>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 8, marginTop: 12 }}>
          <KeyValue label="分析就绪场景" value={String(readyScenes.length)} strong />
          <KeyValue label="水体提取成果" value={String(waterTotal)} strong />
          <KeyValue label="洪涝检测成果" value={String(floodEvents.length)} strong />
          <KeyValue label="运行任务" value={String(runningCount)} strong />
        </div>
      </header>

      <nav style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
        gap: 6,
        marginBottom: 12,
      }}>
        {VIEWS.map(view => {
          const active = activeView === view.key;
          return (
            <button
              key={view.key}
              type="button"
              onClick={() => setActiveView(view.key)}
              style={{
                border: `1px solid ${active ? palette.primary : palette.border}`,
                background: active ? palette.primarySoft : palette.panel,
                color: active ? '#1d4ed8' : palette.text2,
                borderRadius: 6,
                padding: '8px 5px',
                fontSize: 12,
                fontWeight: 700,
                cursor: 'pointer',
              }}
            >
              {view.label}
            </button>
          );
        })}
      </nav>

      {message && (
        <div style={{
          marginBottom: 12,
          border: `1px solid ${getToneStyle(message.type === 'error' ? 'bad' : message.type === 'success' ? 'ok' : message.type === 'warn' ? 'warn' : 'info').border}`,
          background: getToneStyle(message.type === 'error' ? 'bad' : message.type === 'success' ? 'ok' : message.type === 'warn' ? 'warn' : 'info').background,
          color: getToneStyle(message.type === 'error' ? 'bad' : message.type === 'success' ? 'ok' : message.type === 'warn' ? 'warn' : 'info').color,
          borderRadius: 6,
          padding: '8px 10px',
          fontSize: 12,
        }}>
          {message.text}
        </div>
      )}

      {activeView === 'extract' && (
        <main>
          <section style={{ ...sectionStyle, borderTop: 'none' }}>
            <SectionHeader
              title="源影像筛选"
              actions={(
                <>
                  <button type="button" style={buttonStyle('primary', radarLoading)} disabled={radarLoading} onClick={() => runRadarSearch(0)}>
                    {radarLoading ? '查询中' : '查询'}
                  </button>
                  <button type="button" style={buttonStyle('quiet')} onClick={resetRadarSearch}>重置</button>
                </>
              )}
            />
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginBottom: 8 }}>
              <select value={radarDraft.satellite} onChange={e => updateRadarDraft('satellite', e.target.value)} style={inputStyle}>
                <option value="">卫星：全部</option>
                {searchOptions.satellite.map(item => <option key={item} value={item}>{item}</option>)}
              </select>
              <select value={radarDraft.imaging_mode} onChange={e => updateRadarDraft('imaging_mode', e.target.value)} style={inputStyle}>
                <option value="">模式：全部</option>
                {searchOptions.imaging_mode.map(item => <option key={item} value={item}>{item}</option>)}
              </select>
              <UnifiedDatePicker value={radarDraft.imaging_date_from} onChange={value => updateRadarDraft('imaging_date_from', value)} placeholder="成像时间起" language={language} />
              <UnifiedDatePicker value={radarDraft.imaging_date_to} onChange={value => updateRadarDraft('imaging_date_to', value)} placeholder="成像时间止" language={language} />
              <select value={radarDraft.product_level} onChange={e => updateRadarDraft('product_level', e.target.value)} style={inputStyle}>
                <option value="">级别：全部</option>
                {searchOptions.product_level.map(item => <option key={item} value={item}>{item}</option>)}
              </select>
              <select value={radarDraft.polarization} onChange={e => updateRadarDraft('polarization', e.target.value)} style={inputStyle}>
                <option value="">极化：全部</option>
                {searchOptions.polarization.map(item => <option key={item} value={item}>{item}</option>)}
              </select>
            </div>
            <div style={{ display: 'grid', gap: 6, marginBottom: 10 }}>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                <span style={{ color: palette.muted, fontSize: 12 }}>位置过滤</span>
                <button type="button" style={buttonStyle(sourceAoiMode === 'none' ? 'primary' : 'quiet')} onClick={() => setSourceAoiMode('none')}>不限</button>
                <button type="button" style={buttonStyle(sourceAoiMode === 'region' ? 'primary' : 'quiet', regionLoading)} disabled={regionLoading} onClick={() => setSourceAoiMode('region')}>行政区</button>
              </div>
              {sourceAoiMode === 'region' && (
                <RegionSelector
                  options={sourceRegionOptions}
                  selection={sourceRegionSelection}
                  onProvinceChange={value => updateRegionProvince('source', value)}
                  onCityChange={value => updateRegionCity('source', value)}
                  disabled={regionLoading}
                />
              )}
              {regionError && <div style={{ color: palette.red, fontSize: 11 }}>{regionError}</div>}
            </div>

            <div style={{ display: 'grid', gap: 6 }}>
              {!radarSearched && <EmptyState>输入条件后查询入库雷达数据。</EmptyState>}
              {radarSearched && radarResults.length === 0 && !radarLoading && <EmptyState>未找到匹配数据。</EmptyState>}
              {radarResults.map(item => {
                const selected = selectedRadars.some(row => row.id === item.id);
                const done = doneRadarIds.includes(item.id);
                const active = activeRadarIds.includes(item.id);
                const coverageVisible = sourceCoverageVisibleIds.has(String(item.id));
                const previewVisible = sourcePreviewVisibleIds.has(String(item.id));
                return (
                  <div key={item.id} style={{ ...rowStyle, background: selected ? '#eff6ff' : palette.panel }}>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 8, alignItems: 'center' }}>
                      <div style={{ minWidth: 0 }}>
                        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                          <strong>{item.satellite || '-'}</strong>
                          <span style={{ color: palette.muted }}>{formatYmd(item.imaging_date, language)}</span>
                          <span style={{ color: palette.subtle }}>ID {item.id}</span>
                          {done && <StatusBadge tone="ok">已标准化</StatusBadge>}
                          {active && <StatusBadge tone="info">处理中</StatusBadge>}
                        </div>
                        <div style={{ color: palette.subtle, fontSize: 11, marginTop: 4 }}>
                          {[item.imaging_mode, item.product_level, item.polarization].filter(Boolean).join(' · ') || '-'}
                        </div>
                      </div>
                      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                        <button
                          type="button"
                          style={buttonStyle(coverageVisible ? 'success' : 'quiet', !item.coverage_polygon)}
                          disabled={!item.coverage_polygon}
                          onClick={() => handleToggleSourceCoverage(item)}
                        >
                          {coverageVisible ? '清除范围' : '显示范围'}
                        </button>
                        <button
                          type="button"
                          style={buttonStyle(previewVisible ? 'success' : 'secondary', !(item.min_lat != null && item.max_lat != null && item.min_lon != null && item.max_lon != null))}
                          disabled={!(item.min_lat != null && item.max_lat != null && item.min_lon != null && item.max_lon != null)}
                          onClick={() => handleToggleSourcePreview(item)}
                        >
                          {previewVisible ? '清除源影像' : '显示源影像'}
                        </button>
                        <button
                          type="button"
                          style={buttonStyle(selected ? 'success' : 'primary', done || active)}
                          disabled={done || active}
                          onClick={() => toggleRadarSelection(item)}
                        >
                          {selected ? '已选' : '选择'}
                        </button>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
            {radarTotalPages > 1 && (
              <div style={{ display: 'flex', justifyContent: 'center', gap: 8, alignItems: 'center', marginTop: 8 }}>
                <button type="button" style={buttonStyle('quiet', radarPage <= 0)} disabled={radarPage <= 0} onClick={() => runRadarSearch(radarPage - 1)}>上一页</button>
                <span style={{ color: palette.muted, fontSize: 12 }}>{radarPage + 1} / {radarTotalPages}</span>
                <button type="button" style={buttonStyle('quiet', radarPage + 1 >= radarTotalPages)} disabled={radarPage + 1 >= radarTotalPages} onClick={() => runRadarSearch(radarPage + 1)}>下一页</button>
              </div>
            )}
            {selectedRadars.length > 0 && (
              <div style={{ marginTop: 10, display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <span style={{ color: palette.text2, fontSize: 12 }}>已选 {selectedRadars.length} 景</span>
                <button type="button" style={buttonStyle('primary', readOnly || actionBusy === 'geocode')} disabled={readOnly || actionBusy === 'geocode'} onClick={handleSubmitPreprocess}>
                  {actionBusy === 'geocode' ? '提交中' : '生成分析就绪影像'}
                </button>
              </div>
            )}
          </section>

          <section style={sectionStyle}>
            <SectionHeader title={`分析就绪场景 (${scenesTotal})`} actions={<button type="button" style={buttonStyle('quiet', scenesLoading)} disabled={scenesLoading} onClick={() => loadScenes(scenesPage)}>刷新场景</button>} />
            <div style={{ display: 'grid', gap: 8 }}>
              {scenesLoading && <EmptyState>场景加载中...</EmptyState>}
              {!scenesLoading && scenes.length === 0 && <EmptyState>暂无分析就绪场景。</EmptyState>}
              {!scenesLoading && scenes.map(scene => (
                <SceneRow
                  key={scene.id}
                  scene={scene}
                  readOnly={readOnly || actionBusy === `water_${scene.id}` || actionBusy === `reset_scene_${scene.id}`}
                  onShowMap={floodPanel.onShowSourceSceneOnMap || floodPanel.onShowOnMap || (() => {})}
                  onExtractWater={handleExtractWater}
                  onReset={handleResetScene}
                />
              ))}
            </div>
            {scenesTotalPages > 1 && (
              <div style={{ display: 'flex', justifyContent: 'center', gap: 8, alignItems: 'center', marginTop: 8 }}>
                <button type="button" style={buttonStyle('quiet', scenesPage <= 0)} disabled={scenesPage <= 0} onClick={() => loadScenes(scenesPage - 1)}>上一页</button>
                <span style={{ color: palette.muted, fontSize: 12 }}>{scenesPage + 1} / {scenesTotalPages}</span>
                <button type="button" style={buttonStyle('quiet', scenesPage + 1 >= scenesTotalPages)} disabled={scenesPage + 1 >= scenesTotalPages} onClick={() => loadScenes(scenesPage + 1)}>下一页</button>
              </div>
            )}
          </section>

          <section style={sectionStyle}>
            <SectionHeader title={`水体提取成果 (${waterTotal})`} actions={<button type="button" style={buttonStyle('quiet', waterLoading)} disabled={waterLoading} onClick={() => loadWaterResults(waterPage)}>刷新结果</button>} />
            <div style={{ display: 'grid', gap: 8 }}>
              {waterLoading && <EmptyState>水体提取成果加载中...</EmptyState>}
              {!waterLoading && waterResults.length === 0 && <EmptyState>暂无水体提取成果。</EmptyState>}
              {!waterLoading && waterResults.map(item => (
                <WaterResultRow key={item.id} item={item} onShowMap={handleShowWaterResult} />
              ))}
            </div>
            {waterTotalPages > 1 && (
              <div style={{ display: 'flex', justifyContent: 'center', gap: 8, alignItems: 'center', marginTop: 8 }}>
                <button type="button" style={buttonStyle('quiet', waterPage <= 0)} disabled={waterPage <= 0} onClick={() => loadWaterResults(waterPage - 1)}>上一页</button>
                <span style={{ color: palette.muted, fontSize: 12 }}>{waterPage + 1} / {waterTotalPages}</span>
                <button type="button" style={buttonStyle('quiet', waterPage + 1 >= waterTotalPages)} disabled={waterPage + 1 >= waterTotalPages} onClick={() => loadWaterResults(waterPage + 1)}>下一页</button>
              </div>
            )}
          </section>
        </main>
      )}

      {activeView === 'detect' && (
        <main>
          <section style={{ ...sectionStyle, borderTop: 'none' }}>
            <SectionHeader
              title="灾前/灾后配对"
              actions={(
                <>
                  <button type="button" style={buttonStyle('primary', pairSearching)} disabled={pairSearching} onClick={handleFindPairs}>{pairSearching ? '推荐中' : '推荐配对'}</button>
                  <button type="button" style={buttonStyle('success', readOnly || !selectedPair || actionBusy === 'flood_detect')} disabled={readOnly || !selectedPair || actionBusy === 'flood_detect'} onClick={handleSubmitFloodDetection}>
                    {actionBusy === 'flood_detect' ? '提交中' : '提交洪涝检测'}
                  </button>
                </>
              )}
            />
            <div style={{ display: 'grid', gap: 8, marginBottom: 10 }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
                <input value={disasterName} onChange={event => setDisasterName(event.target.value)} placeholder="灾害名称（可选）" style={inputStyle} />
                <UnifiedDatePicker value={disasterDate} onChange={setDisasterDate} placeholder="灾害发生日期" language={language} />
              </div>
              <RegionSelector
                options={pairRegionOptions}
                selection={pairRegionSelection}
                onProvinceChange={value => updateRegionProvince('pair', value)}
                onCityChange={value => updateRegionCity('pair', value)}
                disabled={regionLoading}
              />
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 6 }}>
                <label style={{ color: palette.text2, fontSize: 12 }}>
                  灾前窗口（天）
                  <input type="number" min="1" max="365" value={preWindowDays} onChange={event => setPreWindowDays(event.target.value)} style={inputStyle} />
                </label>
                <label style={{ color: palette.text2, fontSize: 12 }}>
                  灾后窗口（天）
                  <input type="number" min="1" max="365" value={postWindowDays} onChange={event => setPostWindowDays(event.target.value)} style={inputStyle} />
                </label>
                <label style={{ color: palette.text2, fontSize: 12 }}>
                  AOI覆盖
                  <input type="number" min="0" max="1" step="0.05" value={minAoiCoverage} onChange={event => setMinAoiCoverage(event.target.value)} style={inputStyle} />
                </label>
                <label style={{ color: palette.text2, fontSize: 12 }}>
                  配对重叠
                  <input type="number" min="0" max="1" step="0.05" value={overlapThreshold} onChange={event => setOverlapThreshold(event.target.value)} style={inputStyle} />
                </label>
              </div>
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, color: palette.text2, fontSize: 12 }}>
                <input type="checkbox" checked={refine} onChange={event => setRefine(event.target.checked)} style={{ accentColor: palette.primary }} />
                MRF 精化
              </label>
              {pairSearchSummary && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 8 }}>
                  <KeyValue label="灾前匹配池" value={String(pairSearchSummary.pre_pool_count || 0)} />
                  <KeyValue label="灾后匹配池" value={String(pairSearchSummary.post_pool_count || 0)} />
                  <KeyValue label="候选配对" value={String(pairSearchSummary.candidate_count || 0)} strong />
                </div>
              )}
            </div>
            <div style={{ display: 'grid', gap: 8 }}>
              {candidatePairs.length === 0 && <EmptyState>暂无候选配对。</EmptyState>}
              {candidatePairs.map((pair, index) => {
                const active = selectedPairIdx === index;
                return (
                  <button
                    key={`${pair.pre.id}-${pair.post.id}`}
                    type="button"
                    onClick={() => setSelectedPairIdx(index)}
                    style={{
                      ...rowStyle,
                      textAlign: 'left',
                      cursor: 'pointer',
                      borderColor: active ? palette.primary : palette.border,
                      background: active ? '#eff6ff' : palette.panel,
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                      <strong>#{pair.pre.id} {formatYmd(pair.pre.imaging_date, language)}</strong>
                      <span style={{ color: palette.muted }}>{'->'}</span>
                      <strong>#{pair.post.id} {formatYmd(pair.post.imaging_date, language)}</strong>
                      {pair.score != null && <StatusBadge tone="info">评分 {Number(pair.score).toFixed(2)}</StatusBadge>}
                      <StatusBadge tone={active ? 'info' : 'muted'}>{active ? '已选' : '候选'}</StatusBadge>
                    </div>
                    <div style={{ color: palette.muted, fontSize: 11, marginTop: 5 }}>
                      配对重叠 {formatPercent(pair.overlap_ratio)}
                      {pair.aoi_coverage_ratio != null ? ` · AOI覆盖 ${formatPercent(pair.aoi_coverage_ratio)}` : ''}
                      {pair.time_diff_days != null ? ` · 间隔 ${pair.time_diff_days} 天` : ''}
                      {pair.pre_delta_days != null ? ` · 灾前 ${pair.pre_delta_days} 天` : ''}
                      {pair.post_delta_days != null ? ` · 灾后 ${pair.post_delta_days} 天` : ''}
                      {pair.pre.satellite ? ` · ${pair.pre.satellite}` : ''}
                      {pair.pre.polarization ? ` · ${pair.pre.polarization}` : ''}
                    </div>
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 8 }}>
                      <span
                        role="button"
                        tabIndex={0}
                        style={buttonStyle('secondary', false, { display: 'inline-flex', padding: '3px 8px', fontSize: 11 })}
                        onClick={event => {
                          event.stopPropagation();
                          handleShowPairCoverage(pair);
                        }}
                        onKeyDown={event => {
                          if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault();
                            event.stopPropagation();
                            handleShowPairCoverage(pair);
                          }
                        }}
                      >
                        显示覆盖范围
                      </span>
                    </div>
                  </button>
                );
              })}
            </div>
          </section>

          <section style={sectionStyle}>
            <SectionHeader title="洪涝检测成果" actions={<button type="button" style={buttonStyle('quiet', floodLoading)} disabled={floodLoading} onClick={loadFloodEvents}>刷新结果</button>} />
            <div style={{ display: 'grid', gap: 8 }}>
              {floodLoading && <EmptyState>洪涝检测成果加载中...</EmptyState>}
              {!floodLoading && floodEvents.length === 0 && <EmptyState>暂无洪涝检测成果。</EmptyState>}
              {!floodLoading && floodEvents.slice(0, 6).map(event => (
                <FloodEventRow
                  key={event.id}
                  event={event}
                  mapLayerVis={mapLayerVis}
                  mapLoading={mapLoadingId === event.id}
                  onShowMap={handleShowFloodEvent}
                  onToggleLayer={handleToggleFloodLayer}
                  onSelectImpact={handleSelectImpact}
                  onCreateProduct={handleCreateFloodProduct}
                  productBusy={productBusyId === event.id}
                />
              ))}
            </div>
          </section>
        </main>
      )}

      {activeView === 'impact' && (
        <main>
          <section style={{ ...sectionStyle, borderTop: 'none' }}>
            <SectionHeader
              title="影响评估对象"
              actions={<button type="button" style={buttonStyle('quiet', impactLoading)} disabled={impactLoading || !selectedImpactEvent} onClick={() => selectedImpactEvent && loadImpactResult(selectedImpactEvent.id)}>刷新结果</button>}
            />
            <div style={{ display: 'grid', gap: 8 }}>
              {[
                ['分类栅格', selectedImpactEvent ? `检测 #${selectedImpactEvent.id}` : '请选择成果', selectedImpactEvent ? 'ok' : 'muted'],
                ['洪涝范围矢量', impactResult?.warnings?.includes?.('overlay has not been run') ? '未生成' : (impactResult ? '已生成' : '待评估'), impactResult && !impactResult?.warnings?.includes?.('overlay has not been run') ? 'ok' : 'warn'],
                ['灾害点命中', impactResult ? `${impactResult.hazard_points?.inside_flood?.length || 0} 个` : '待运行', impactResult ? 'ok' : 'muted'],
                ['近洪涝风险点', impactResult ? `${impactResult.hazard_points?.near_flood?.length || 0} 个` : '待运行', impactResult ? 'info' : 'muted'],
                ['D-InSAR 关联', impactResult ? `${impactResult.dinsar_products?.length || 0} 个` : '待运行', impactResult ? 'info' : 'muted'],
                ['行政区统计', impactResult?.affected_aois?.length ? `${impactResult.affected_aois.length} 个` : '暂无数据', impactResult ? 'muted' : 'muted'],
              ].map(([name, status, tone]) => (
                <div key={name} style={{ ...rowStyle, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                  <strong>{name}</strong>
                  <StatusBadge tone={tone}>{status}</StatusBadge>
                </div>
              ))}
            </div>
          </section>

          <section style={sectionStyle}>
            <SectionHeader title="选择洪涝检测成果" />
            <div style={{ display: 'grid', gap: 8 }}>
              {doneFloodEvents.length === 0 && <EmptyState>暂无已完成洪涝检测成果。</EmptyState>}
              {doneFloodEvents.map(event => {
                const active = String(selectedImpactEvent?.id) === String(event.id);
                return (
                  <button
                    key={event.id}
                    type="button"
                    onClick={() => setSelectedImpactEventId(String(event.id))}
                    style={{
                      ...rowStyle,
                      textAlign: 'left',
                      cursor: 'pointer',
                      borderColor: active ? palette.primary : palette.border,
                      background: active ? '#eff6ff' : palette.panel,
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
                      <strong>洪涝检测 #{event.id}</strong>
                      <StatusBadge tone={active ? 'info' : 'muted'}>{active ? '当前' : '可选'}</StatusBadge>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 8, marginTop: 8 }}>
                      <KeyValue label="灾前" value={formatYmd(event.pre_imaging_date, language)} />
                      <KeyValue label="灾后" value={formatYmd(event.post_imaging_date, language)} />
                      <KeyValue label="新增淹没面积" value={formatArea(event.flood_area_km2)} strong />
                    </div>
                  </button>
                );
              })}
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 10 }}>
              <button type="button" style={buttonStyle('secondary', !selectedImpactEvent)} disabled={!selectedImpactEvent} onClick={() => selectedImpactEvent && handleShowFloodEvent(selectedImpactEvent)}>显示分类图层</button>
              <button type="button" style={buttonStyle('secondary', !impactResult?.flood_vector_geojson)} disabled={!impactResult?.flood_vector_geojson} onClick={handleShowFloodVector}>显示洪涝范围</button>
              <button type="button" style={buttonStyle('primary', readOnly || !selectedImpactEvent || overlayRunning)} disabled={readOnly || !selectedImpactEvent || overlayRunning} onClick={handleRunOverlay}>
                {overlayRunning ? '评估中' : '执行影响评估'}
              </button>
              <button type="button" style={buttonStyle('quiet', true)} disabled>导出影响清单</button>
            </div>
          </section>

          <section style={sectionStyle}>
            <SectionHeader title="影响评估结果" />
            {impactLoading && <EmptyState>影响评估结果加载中...</EmptyState>}
            {!impactLoading && !impactResult && <EmptyState>选择一个已完成的洪涝检测成果后执行影响评估。</EmptyState>}
            {!impactLoading && impactResult && (
              <div style={{ display: 'grid', gap: 10 }}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, minmax(0, 1fr))', gap: 8 }}>
                  <KeyValue label="新增淹没面积" value={formatArea(impactResult.flood_area_km2)} strong />
                  <KeyValue label="命中灾害点" value={String(impactResult.hazard_points?.inside_flood?.length || 0)} strong />
                  <KeyValue label="近洪涝风险点" value={String(impactResult.hazard_points?.near_flood?.length || 0)} />
                  <KeyValue label="D-InSAR 产品" value={String(impactResult.dinsar_products?.length || 0)} />
                  <KeyValue label="影响行政区" value={String(impactResult.affected_aois?.length || 0)} />
                </div>
                {impactResult.warnings?.length > 0 && (
                  <div style={{ color: palette.amber, fontSize: 11 }}>
                    {impactResult.warnings.join('；')}
                  </div>
                )}
                <div style={{ display: 'grid', gap: 8 }}>
                  {(impactResult.hazard_points?.inside_flood || []).slice(0, 5).map(point => (
                    <div key={`inside_${point.id}`} style={rowStyle}>
                      <strong>{point.name || `灾害点 #${point.id}`}</strong>
                      <div style={{ color: palette.muted, fontSize: 11, marginTop: 4 }}>
                        洪涝范围内 · {[point.type, point.city, point.county].filter(Boolean).join(' / ') || '-'}
                      </div>
                    </div>
                  ))}
                  {(impactResult.hazard_points?.near_flood || []).slice(0, 5).map(point => (
                    <div key={`near_${point.id}`} style={rowStyle}>
                      <strong>{point.name || `灾害点 #${point.id}`}</strong>
                      <div style={{ color: palette.muted, fontSize: 11, marginTop: 4 }}>
                        距洪涝范围 {point.distance_m ?? '-'} m · {[point.type, point.city, point.county].filter(Boolean).join(' / ') || '-'}
                      </div>
                    </div>
                  ))}
                  {(impactResult.dinsar_products || []).slice(0, 5).map(product => (
                    <div key={`dinsar_${product.id || product.product_id}`} style={rowStyle}>
                      <strong>{product.display_name || product.product_id}</strong>
                      <div style={{ color: palette.muted, fontSize: 11, marginTop: 4 }}>
                        {product.engine || '-'} · 形变 {product.deformation_mm ?? '-'} mm · AI {product.ai_score ?? '-'}
                      </div>
                    </div>
                  ))}
                  {(impactResult.affected_aois || []).slice(0, 5).map(aoi => (
                    <div key={`aoi_${aoi.tree_id || aoi.name}`} style={rowStyle}>
                      <strong>{aoi.name || aoi.tree_id}</strong>
                      <div style={{ color: palette.muted, fontSize: 11, marginTop: 4 }}>
                        {aoi.level || '-'} · 受淹面积 {formatArea(aoi.flood_area_km2)}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </section>
        </main>
      )}

      {activeView === 'results' && (
        <main>
          <section style={{ ...sectionStyle, borderTop: 'none' }}>
            <SectionHeader
              title="成果列表"
              actions={(
                <>
                  <button type="button" style={buttonStyle(resultMode === 'flood' ? 'primary' : 'quiet')} onClick={() => setResultMode('flood')}>洪涝检测</button>
                  <button type="button" style={buttonStyle(resultMode === 'water' ? 'primary' : 'quiet')} onClick={() => setResultMode('water')}>水体提取</button>
                  <button type="button" style={buttonStyle(resultMode === 'products' ? 'primary' : 'quiet')} onClick={() => setResultMode('products')}>发布成果</button>
                  <button type="button" style={buttonStyle('secondary')} onClick={refreshAll}>刷新</button>
                </>
              )}
            />
            {resultMode === 'flood' && (
              <div style={{ display: 'grid', gap: 8 }}>
                {floodEvents.length === 0 && <EmptyState>暂无洪涝检测成果。</EmptyState>}
                {floodEvents.map(event => (
                  <FloodEventRow
                    key={event.id}
                    event={event}
                    mapLayerVis={mapLayerVis}
                    mapLoading={mapLoadingId === event.id}
                    onShowMap={handleShowFloodEvent}
                    onToggleLayer={handleToggleFloodLayer}
                    onSelectImpact={handleSelectImpact}
                    onCreateProduct={handleCreateFloodProduct}
                    productBusy={productBusyId === event.id}
                  />
                ))}
              </div>
            )}
            {resultMode === 'water' && (
              <div style={{ display: 'grid', gap: 8 }}>
                {waterResults.length === 0 && <EmptyState>暂无水体提取成果。</EmptyState>}
                {waterResults.map(item => (
                  <WaterResultRow key={item.id} item={item} onShowMap={handleShowWaterResult} />
                ))}
              </div>
            )}
            {resultMode === 'products' && (
              <div style={{ display: 'grid', gap: 8 }}>
                {productLoading && <EmptyState>发布成果加载中...</EmptyState>}
                {!productLoading && floodProducts.length === 0 && <EmptyState>暂无发布成果。可在洪涝检测成果中点击“发布成果”。</EmptyState>}
                {!productLoading && floodProducts.map(product => (
                  <FloodProductRow key={product.id || product.product_id} product={product} onOpenManifest={handleOpenProductManifest} />
                ))}
              </div>
            )}
          </section>

          <section style={sectionStyle}>
            <SectionHeader title="成果发布" />
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 8 }}>
              <button type="button" style={buttonStyle('secondary')} onClick={() => setResultMode('products')}>发布成果列表</button>
              <button type="button" style={buttonStyle('quiet', true)} disabled>GeoJSON 导出</button>
              <button type="button" style={buttonStyle('quiet', true)} disabled>报告</button>
            </div>
          </section>
        </main>
      )}
    </div>
  );
}
