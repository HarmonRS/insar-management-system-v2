import { useCallback, useEffect, useMemo, useState } from 'react';

import UnifiedDatePicker from './components/UnifiedDatePicker';
import { getSearchOptions, searchRadarData } from './api/radar';
import {
  getFloodActiveRadarIds,
  getFloodDetectionPreview,
  getFloodDetections,
  getFloodDoneRadarIds,
  getFloodScenes,
  getFloodWaterExtractionPreview,
  getFloodWaterExtractions,
  resetFloodScene,
  searchFloodPairs,
  submitFloodDetection,
  submitFloodPreprocess,
  submitFloodWaterExtraction,
} from './api/flood';
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
  { key: 'impact', label: '套合分析' },
  { key: 'results', label: '结果与任务' },
];

const SENSOR_ROWS = [
  { name: 'LT-1', status: '精密链路可用', tone: 'ok', capability: 'ENVI/SARscape' },
  { name: 'GF3', status: '快速路线可用', tone: 'warn', capability: 'Python/GDAL + Otsu' },
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
  return (
    <div style={rowStyle}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 8, alignItems: 'start' }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <strong>场景 #{scene.id}</strong>
            <StatusBadge status={scene.status} />
            <span style={{ color: palette.muted }}>{scene.satellite || '-'}</span>
            <span style={{ color: palette.muted }}>{formatYmd(scene.imaging_date, 'zh')}</span>
          </div>
          <div style={{ color: palette.subtle, fontSize: 11, marginTop: 5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={scene.geo_path || scene.error_msg || ''}>
            {scene.geo_path ? scene.geo_path.split(/[\\/]/).pop() : scene.error_msg || `Radar ID ${scene.radar_data_id}`}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          <button type="button" style={buttonStyle('quiet', !scene.coverage_polygon)} disabled={!scene.coverage_polygon} onClick={() => onShowMap(scene)}>覆盖</button>
          <button type="button" style={buttonStyle('primary', readOnly || !canExtract)} disabled={readOnly || !canExtract} onClick={() => onExtractWater(scene)}>提取水体</button>
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
            <strong>水体 #{item.id}</strong>
            <StatusBadge status={item.status} />
            {item.scene_id && <span style={{ color: palette.muted }}>场景 #{item.scene_id}</span>}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 8, marginTop: 8 }}>
            <KeyValue label="面积" value={formatArea(item.water_area_km2)} strong />
            <KeyValue label="像素" value={item.water_pixel_count?.toLocaleString?.() || '-'} />
            <KeyValue label="阈值" value={item.otsu_threshold_db != null ? Number(item.otsu_threshold_db).toFixed(2) : '-'} />
          </div>
          {item.error_msg && <div style={{ color: palette.red, fontSize: 11, marginTop: 6 }}>{item.error_msg}</div>}
        </div>
        <button type="button" style={buttonStyle('secondary', asStatus(item.status) !== 'DONE')} disabled={asStatus(item.status) !== 'DONE'} onClick={() => onShowMap(item)}>上图</button>
      </div>
    </div>
  );
}

function FloodEventRow({ event, mapLayerVis, mapLoading, onShowMap, onToggleLayer, onSelectImpact }) {
  const done = asStatus(event.status) === 'DONE';
  const preDate = formatYmd(event.pre_imaging_date, 'zh');
  const postDate = formatYmd(event.post_imaging_date, 'zh');
  const layers = mapLayerVis[event.id];
  return (
    <div style={rowStyle}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', minWidth: 0 }}>
          <strong>洪涝 #{event.id}</strong>
          <StatusBadge status={event.status} />
          <span style={{ color: palette.muted }}>{event.pre_satellite || event.post_satellite || '-'}</span>
        </div>
        <button type="button" style={buttonStyle('secondary', !done || mapLoading)} disabled={!done || mapLoading} onClick={() => onShowMap(event)}>
          {mapLoading ? '加载中' : '上图'}
        </button>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 8, marginTop: 10 }}>
        <KeyValue label="灾前" value={preDate} />
        <KeyValue label="灾后" value={postDate} />
        <KeyValue label="洪涝面积" value={formatArea(event.flood_area_km2)} strong />
        <KeyValue label="稳定水体" value={formatArea(event.stable_water_area_km2)} />
      </div>
      {event.error_msg && <div style={{ color: palette.red, fontSize: 11, marginTop: 8 }}>{event.error_msg}</div>}
      {layers && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 9 }}>
          {[
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
              {layers[key] ? '已显示' : '已隐藏'} {label}
            </button>
          ))}
          {onSelectImpact && (
            <button type="button" style={buttonStyle('secondary', false, { padding: '3px 8px', fontSize: 11 })} onClick={() => onSelectImpact(event)}>
              套合分析
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

  const [pairPreStart, setPairPreStart] = useState('');
  const [pairPreEnd, setPairPreEnd] = useState('');
  const [pairPostStart, setPairPostStart] = useState('');
  const [pairPostEnd, setPairPostEnd] = useState('');
  const [overlapThreshold, setOverlapThreshold] = useState(0.3);
  const [refine, setRefine] = useState(false);
  const [pairSearching, setPairSearching] = useState(false);
  const [candidatePairs, setCandidatePairs] = useState([]);
  const [selectedPairIdx, setSelectedPairIdx] = useState(null);

  const [floodEvents, setFloodEvents] = useState([]);
  const [floodLoading, setFloodLoading] = useState(false);
  const [mapLoadingId, setMapLoadingId] = useState(null);
  const [mapLayerVis, setMapLayerVis] = useState({});
  const [selectedImpactEventId, setSelectedImpactEventId] = useState('');
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

  const showMessage = useCallback((type, text) => {
    setMessage({ type, text });
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
      showMessage('error', `水体场景加载失败：${getErrorText(error)}`);
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
      showMessage('error', `水体结果加载失败：${getErrorText(error)}`);
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
      showMessage('error', `洪涝结果加载失败：${getErrorText(error)}`);
    } finally {
      setFloodLoading(false);
    }
  }, [showMessage]);

  const refreshAll = useCallback(async () => {
    await Promise.all([
      loadStatusIds(),
      loadScenes(scenesPage),
      loadWaterResults(waterPage),
      loadFloodEvents(),
    ]);
  }, [loadFloodEvents, loadScenes, loadStatusIds, loadWaterResults, scenesPage, waterPage]);

  useEffect(() => {
    getSearchOptions()
      .then(data => setSearchOptions({
        satellite: data.satellite || [],
        imaging_mode: data.imaging_mode || [],
        product_level: data.product_level || [],
        polarization: data.polarization || [],
      }))
      .catch(() => {});
    refreshAll();
  }, [refreshAll]);

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
      const criteria = normalizeRadarSearchCriteria(radarDraft, RADAR_SEARCH_DEFAULTS);
      const formData = buildRadarSearchFormData({
        limit: SEARCH_PAGE_SIZE,
        offset: page * SEARCH_PAGE_SIZE,
        criteria,
        aoiMode: 'none',
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

  const handleSubmitPreprocess = async () => {
    if (readOnly || selectedRadars.length === 0) return;
    setActionBusy('geocode');
    showMessage('info', `正在提交 ${selectedRadars.length} 景水体预处理任务...`);
    onTaskStart?.(null, '正在提交水体预处理任务...');
    let ok = 0;
    let fail = 0;
    for (const radar of selectedRadars) {
      try {
        const res = await submitFloodPreprocess({ radar_data_id: radar.id });
        ok += 1;
        if (res.data?.task_id) onTaskStart?.(res.data.task_id, '水体预处理任务已启动');
      } catch {
        fail += 1;
      }
    }
    setActionBusy('');
    setSelectedRadars([]);
    await refreshAll();
    showMessage(fail ? 'warn' : 'success', fail ? `提交完成：${ok} 成功，${fail} 失败。` : `已提交 ${ok} 个水体预处理任务。`);
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
      showMessage('success', `水体提取 #${item.id} 已加载到地图。`);
    } catch (error) {
      showMessage('error', `水体图层加载失败：${getErrorText(error)}`);
    } finally {
      setMapLoadingId(null);
    }
  };

  const handleFindPairs = async () => {
    setPairSearching(true);
    setCandidatePairs([]);
    setSelectedPairIdx(null);
    try {
      const data = await searchFloodPairs({
        pre_start: compactDate(pairPreStart),
        pre_end: compactDate(pairPreEnd),
        post_start: compactDate(pairPostStart),
        post_end: compactDate(pairPostEnd),
        overlap_threshold: Number(overlapThreshold) || 0,
      });
      setCandidatePairs(data.pairs || []);
      setSelectedPairIdx(data.pairs?.length ? 0 : null);
      showMessage(data.pairs?.length ? 'success' : 'warn', data.pairs?.length ? `找到 ${data.pairs.length} 组候选配对。` : '没有找到满足条件的配对。');
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
      showMessage('success', `洪涝结果 #${event.id} 已加载到地图。`);
    } catch (error) {
      showMessage('error', `洪涝图层加载失败：${getErrorText(error)}`);
    } finally {
      setMapLoadingId(null);
    }
  };

  const handleToggleFloodLayer = (eventId, key, visible) => {
    setMapLayerVis(prev => ({ ...prev, [eventId]: { ...prev[eventId], [key]: visible } }));
    floodPanel.onToggleFloodLayer?.(eventId, key, visible);
  };

  const handleSelectImpact = (event) => {
    setSelectedImpactEventId(String(event.id));
    setActiveView('impact');
  };

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
          <KeyValue label="可提取场景" value={String(readyScenes.length)} strong />
          <KeyValue label="水体结果" value={String(waterTotal)} strong />
          <KeyValue label="洪涝结果" value={String(floodEvents.length)} strong />
          <KeyValue label="运行中" value={String(runningCount)} strong />
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
              title="入库影像"
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

            <div style={{ display: 'grid', gap: 6 }}>
              {!radarSearched && <EmptyState>输入条件后查询入库雷达数据。</EmptyState>}
              {radarSearched && radarResults.length === 0 && !radarLoading && <EmptyState>未找到匹配数据。</EmptyState>}
              {radarResults.map(item => {
                const selected = selectedRadars.some(row => row.id === item.id);
                const done = doneRadarIds.includes(item.id);
                const active = activeRadarIds.includes(item.id);
                return (
                  <div key={item.id} style={{ ...rowStyle, background: selected ? '#eff6ff' : palette.panel }}>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 8, alignItems: 'center' }}>
                      <div style={{ minWidth: 0 }}>
                        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                          <strong>{item.satellite || '-'}</strong>
                          <span style={{ color: palette.muted }}>{formatYmd(item.imaging_date, language)}</span>
                          <span style={{ color: palette.subtle }}>ID {item.id}</span>
                          {done && <StatusBadge tone="ok">已预处理</StatusBadge>}
                          {active && <StatusBadge tone="info">处理中</StatusBadge>}
                        </div>
                        <div style={{ color: palette.subtle, fontSize: 11, marginTop: 4 }}>
                          {[item.imaging_mode, item.product_level, item.polarization].filter(Boolean).join(' · ') || '-'}
                        </div>
                      </div>
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
                  {actionBusy === 'geocode' ? '提交中' : '提交水体预处理'}
                </button>
              </div>
            )}
          </section>

          <section style={sectionStyle}>
            <SectionHeader title={`可提取场景 (${scenesTotal})`} actions={<button type="button" style={buttonStyle('quiet', scenesLoading)} disabled={scenesLoading} onClick={() => loadScenes(scenesPage)}>刷新场景</button>} />
            <div style={{ display: 'grid', gap: 8 }}>
              {scenesLoading && <EmptyState>场景加载中...</EmptyState>}
              {!scenesLoading && scenes.length === 0 && <EmptyState>暂无水体预处理场景。</EmptyState>}
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
            <SectionHeader title={`水体提取结果 (${waterTotal})`} actions={<button type="button" style={buttonStyle('quiet', waterLoading)} disabled={waterLoading} onClick={() => loadWaterResults(waterPage)}>刷新结果</button>} />
            <div style={{ display: 'grid', gap: 8 }}>
              {waterLoading && <EmptyState>水体结果加载中...</EmptyState>}
              {!waterLoading && waterResults.length === 0 && <EmptyState>暂无水体提取结果。</EmptyState>}
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
                    {actionBusy === 'flood_detect' ? '提交中' : '提交检测'}
                  </button>
                </>
              )}
            />
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginBottom: 8 }}>
              <UnifiedDatePicker value={pairPreStart} onChange={setPairPreStart} placeholder="灾前开始" language={language} />
              <UnifiedDatePicker value={pairPreEnd} onChange={setPairPreEnd} placeholder="灾前结束" language={language} />
              <UnifiedDatePicker value={pairPostStart} onChange={setPairPostStart} placeholder="灾后开始" language={language} />
              <UnifiedDatePicker value={pairPostEnd} onChange={setPairPostEnd} placeholder="灾后结束" language={language} />
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, color: palette.text2, fontSize: 12 }}>
                重叠阈值
                <input type="number" min="0" max="1" step="0.05" value={overlapThreshold} onChange={event => setOverlapThreshold(event.target.value)} style={{ ...inputStyle, width: 80 }} />
              </label>
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, color: palette.text2, fontSize: 12 }}>
                <input type="checkbox" checked={refine} onChange={event => setRefine(event.target.checked)} style={{ accentColor: palette.primary }} />
                MRF 精化
              </label>
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
                      <StatusBadge tone={active ? 'info' : 'muted'}>{active ? '已选' : '候选'}</StatusBadge>
                    </div>
                    <div style={{ color: palette.muted, fontSize: 11, marginTop: 5 }}>
                      重叠 {(Number(pair.overlap_ratio || 0) * 100).toFixed(1)}%
                      {pair.time_diff_days != null ? ` · 间隔 ${pair.time_diff_days} 天` : ''}
                      {pair.pre.satellite ? ` · ${pair.pre.satellite}` : ''}
                    </div>
                  </button>
                );
              })}
            </div>
          </section>

          <section style={sectionStyle}>
            <SectionHeader title="近期洪涝结果" actions={<button type="button" style={buttonStyle('quiet', floodLoading)} disabled={floodLoading} onClick={loadFloodEvents}>刷新结果</button>} />
            <div style={{ display: 'grid', gap: 8 }}>
              {floodLoading && <EmptyState>洪涝结果加载中...</EmptyState>}
              {!floodLoading && floodEvents.length === 0 && <EmptyState>暂无洪涝检测结果。</EmptyState>}
              {!floodLoading && floodEvents.slice(0, 6).map(event => (
                <FloodEventRow
                  key={event.id}
                  event={event}
                  mapLayerVis={mapLayerVis}
                  mapLoading={mapLoadingId === event.id}
                  onShowMap={handleShowFloodEvent}
                  onToggleLayer={handleToggleFloodLayer}
                  onSelectImpact={handleSelectImpact}
                />
              ))}
            </div>
          </section>
        </main>
      )}

      {activeView === 'impact' && (
        <main>
          <section style={{ ...sectionStyle, borderTop: 'none' }}>
            <SectionHeader title="套合对象" />
            <div style={{ display: 'grid', gap: 8 }}>
              {[
                ['洪涝分类栅格', selectedImpactEvent ? `结果 #${selectedImpactEvent.id}` : '请选择结果', selectedImpactEvent ? 'ok' : 'muted'],
                ['灾害点', '已有底库', 'ok'],
                ['洪涝矢量化', '待接入', 'warn'],
                ['行政区统计', '待接入', 'muted'],
                ['当前 AOI', '待接入', 'muted'],
                ['自定义矢量', '待接入', 'muted'],
              ].map(([name, status, tone]) => (
                <div key={name} style={{ ...rowStyle, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                  <strong>{name}</strong>
                  <StatusBadge tone={tone}>{status}</StatusBadge>
                </div>
              ))}
            </div>
          </section>

          <section style={sectionStyle}>
            <SectionHeader title="选择洪涝结果" />
            <div style={{ display: 'grid', gap: 8 }}>
              {doneFloodEvents.length === 0 && <EmptyState>暂无已完成洪涝结果。</EmptyState>}
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
                      <strong>洪涝 #{event.id}</strong>
                      <StatusBadge tone={active ? 'info' : 'muted'}>{active ? '当前' : '可选'}</StatusBadge>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 8, marginTop: 8 }}>
                      <KeyValue label="灾前" value={formatYmd(event.pre_imaging_date, language)} />
                      <KeyValue label="灾后" value={formatYmd(event.post_imaging_date, language)} />
                      <KeyValue label="洪涝面积" value={formatArea(event.flood_area_km2)} strong />
                    </div>
                  </button>
                );
              })}
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 10 }}>
              <button type="button" style={buttonStyle('secondary', !selectedImpactEvent)} disabled={!selectedImpactEvent} onClick={() => selectedImpactEvent && handleShowFloodEvent(selectedImpactEvent)}>加载洪涝图层</button>
              <button type="button" style={buttonStyle('quiet', true)} disabled>运行套合分析</button>
              <button type="button" style={buttonStyle('quiet', true)} disabled>导出影响清单</button>
            </div>
          </section>
        </main>
      )}

      {activeView === 'results' && (
        <main>
          <section style={{ ...sectionStyle, borderTop: 'none' }}>
            <SectionHeader
              title="结果列表"
              actions={(
                <>
                  <button type="button" style={buttonStyle(resultMode === 'flood' ? 'primary' : 'quiet')} onClick={() => setResultMode('flood')}>洪涝</button>
                  <button type="button" style={buttonStyle(resultMode === 'water' ? 'primary' : 'quiet')} onClick={() => setResultMode('water')}>水体</button>
                  <button type="button" style={buttonStyle('secondary')} onClick={refreshAll}>刷新</button>
                </>
              )}
            />
            {resultMode === 'flood' && (
              <div style={{ display: 'grid', gap: 8 }}>
                {floodEvents.length === 0 && <EmptyState>暂无洪涝检测结果。</EmptyState>}
                {floodEvents.map(event => (
                  <FloodEventRow
                    key={event.id}
                    event={event}
                    mapLayerVis={mapLayerVis}
                    mapLoading={mapLoadingId === event.id}
                    onShowMap={handleShowFloodEvent}
                    onToggleLayer={handleToggleFloodLayer}
                    onSelectImpact={handleSelectImpact}
                  />
                ))}
              </div>
            )}
            {resultMode === 'water' && (
              <div style={{ display: 'grid', gap: 8 }}>
                {waterResults.length === 0 && <EmptyState>暂无水体提取结果。</EmptyState>}
                {waterResults.map(item => (
                  <WaterResultRow key={item.id} item={item} onShowMap={handleShowWaterResult} />
                ))}
              </div>
            )}
          </section>

          <section style={sectionStyle}>
            <SectionHeader title="产品出口" />
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 8 }}>
              <button type="button" style={buttonStyle('quiet', true)} disabled>GeoTIFF</button>
              <button type="button" style={buttonStyle('quiet', true)} disabled>GeoJSON</button>
              <button type="button" style={buttonStyle('quiet', true)} disabled>报告</button>
            </div>
          </section>
        </main>
      )}
    </div>
  );
}
