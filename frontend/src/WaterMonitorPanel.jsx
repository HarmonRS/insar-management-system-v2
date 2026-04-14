import React, { useState, useEffect, useCallback, useRef } from 'react';
import UnifiedDatePicker from './components/UnifiedDatePicker';
import { getSearchOptions, searchRadarData } from './api/radar';
import { submitGeocode, getWaterScenes, getWaterDoneIds, getWaterActiveIds, submitFloodDetect, getFloodEvents, findWaterPairs, resetSceneStatus, cleanupFailedScenes, getFloodEventPreview } from './api/water';
import { submitWaterDetect, getWaterDetections, getWaterDetectionPreview } from './api/gf3';
import { buildRadarSearchFormData, normalizeRadarSearchCriteria } from './utils/appUiHelpers';
import { RADAR_SEARCH_DEFAULTS } from './config/appConstants';

const getStatusLabel = (lang) => lang === 'en'
  ? { PENDING: 'Pending', RUNNING: 'Processing', DONE: 'Done', FAILED: 'Failed' }
  : { PENDING: '等待中', RUNNING: '处理中', DONE: '完成', FAILED: '失败' };
const STATUS_COLOR = { PENDING: '#64748b', RUNNING: '#2563eb', DONE: '#16a34a', FAILED: '#dc2626' };

const UI_COLORS = {
  pageText: '#0f172a',
  textSecondary: '#334155',
  textMuted: '#64748b',
  textSubtle: '#94a3b8',
  border: '#d5dfeb',
  borderStrong: '#c3cfdf',
  panel: '#ffffff',
  panelSoft: '#f8fafc',
  primary: '#2563eb',
  primarySoft: '#bfdbfe',
  primaryPanel: '#eff6ff',
  success: '#16a34a',
  successSoft: '#bbf7d0',
  successPanel: '#f0fdf4',
  danger: '#dc2626',
  dangerSoft: '#fecaca',
  dangerPanel: '#fef2f2',
  warning: '#d97706',
  warningSoft: '#fed7aa',
  warningPanel: '#fff7ed',
  info: '#0891b2',
  infoSoft: '#a5f3fc',
  infoPanel: '#ecfeff',
};

const PANEL_SHADOW = '0 10px 24px rgba(15, 23, 42, 0.05)';

const PANEL_CARD_STYLE = {
  background: UI_COLORS.panel,
  border: `1px solid ${UI_COLORS.border}`,
  borderRadius: 10,
  padding: 14,
  boxShadow: PANEL_SHADOW,
};

const NESTED_CARD_STYLE = {
  background: UI_COLORS.panelSoft,
  border: `1px solid ${UI_COLORS.border}`,
  borderRadius: 10,
  padding: 12,
};

const TABLE_WRAP_STYLE = {
  background: UI_COLORS.panel,
  border: `1px solid ${UI_COLORS.border}`,
  borderRadius: 10,
  overflow: 'hidden',
  boxShadow: PANEL_SHADOW,
};

const INPUT_STYLE = {
  padding: '6px 10px',
  borderRadius: 6,
  border: `1px solid ${UI_COLORS.border}`,
  background: UI_COLORS.panel,
  color: UI_COLORS.pageText,
  fontSize: 12,
  boxSizing: 'border-box',
};

const SECTION_DESC_STYLE = {
  color: UI_COLORS.textSecondary,
  fontSize: 12,
  marginBottom: 12,
  lineHeight: 1.6,
};

const EMPTY_STATE_STYLE = {
  color: UI_COLORS.textMuted,
  fontSize: 12,
  textAlign: 'center',
  padding: 20,
};

const TABLE_HEAD_CELL_STYLE = {
  textAlign: 'left',
  padding: '8px 10px',
  fontWeight: 500,
  color: UI_COLORS.textMuted,
  background: UI_COLORS.panelSoft,
  borderBottom: `1px solid ${UI_COLORS.border}`,
};

const TABLE_CELL_STYLE = {
  padding: '8px 10px',
  color: UI_COLORS.textSecondary,
  borderBottom: `1px solid ${UI_COLORS.border}`,
};

function getButtonStyle(variant = 'secondary', disabled = false, overrides = {}) {
  const base = {
    padding: '6px 12px',
    borderRadius: 6,
    fontSize: 12,
    cursor: disabled ? 'not-allowed' : 'pointer',
    transition: 'all 0.2s ease',
  };
  const variantStyle = {
    primary: {
      background: UI_COLORS.primary,
      color: '#fff',
      border: `1px solid ${UI_COLORS.primary}`,
    },
    secondary: {
      background: UI_COLORS.panelSoft,
      color: UI_COLORS.textSecondary,
      border: `1px solid ${UI_COLORS.border}`,
    },
    ghostPrimary: {
      background: UI_COLORS.primaryPanel,
      color: UI_COLORS.primary,
      border: `1px solid ${UI_COLORS.primarySoft}`,
    },
    success: {
      background: UI_COLORS.successPanel,
      color: UI_COLORS.success,
      border: `1px solid ${UI_COLORS.successSoft}`,
    },
    danger: {
      background: UI_COLORS.dangerPanel,
      color: UI_COLORS.danger,
      border: `1px solid ${UI_COLORS.dangerSoft}`,
    },
    info: {
      background: UI_COLORS.infoPanel,
      color: UI_COLORS.info,
      border: `1px solid ${UI_COLORS.infoSoft}`,
    },
  }[variant] || {};
  return {
    ...base,
    ...variantStyle,
    ...(disabled ? { opacity: 0.55 } : null),
    ...overrides,
  };
}

function getStatusBadgeStyle(status, compact = false) {
  const style = {
    PENDING: {
      color: UI_COLORS.textMuted,
      background: UI_COLORS.panelSoft,
      border: UI_COLORS.borderStrong,
    },
    RUNNING: {
      color: UI_COLORS.primary,
      background: UI_COLORS.primaryPanel,
      border: UI_COLORS.primarySoft,
    },
    DONE: {
      color: UI_COLORS.success,
      background: UI_COLORS.successPanel,
      border: UI_COLORS.successSoft,
    },
    FAILED: {
      color: UI_COLORS.danger,
      background: UI_COLORS.dangerPanel,
      border: UI_COLORS.dangerSoft,
    },
  }[status] || {
    color: UI_COLORS.textMuted,
    background: UI_COLORS.panelSoft,
    border: UI_COLORS.border,
  };
  return {
    fontSize: compact ? 10 : 11,
    padding: compact ? '1px 6px' : '2px 8px',
    borderRadius: 999,
    background: style.background,
    color: style.color,
    border: `1px solid ${style.border}`,
  };
}

function getTabButtonStyle(active) {
  return {
    padding: '8px 14px',
    cursor: 'pointer',
    background: 'none',
    border: 'none',
    borderBottom: active ? `2px solid ${UI_COLORS.primary}` : '2px solid transparent',
    color: active ? UI_COLORS.primary : UI_COLORS.textSecondary,
    fontWeight: active ? 600 : 500,
    fontSize: 13,
  };
}

const SEARCH_DEFAULTS = { ...RADAR_SEARCH_DEFAULTS };

// ---------------------------------------------------------------------------
// 雷达数据搜索框（复用数据管理逻辑）
// ---------------------------------------------------------------------------
const SEARCH_PAGE_SIZE = 50;

function RadarSearchBox({ onSelect, selectedIds, doneRadarIds = [], activeRadarIds = [], language = 'zh', fixedSatellite = '' }) {
  const en = language === 'en';
  const [options, setOptions] = useState({ satellite: [], imaging_mode: [], polarization: [], product_level: [] });
  const [draft, setDraft] = useState({ ...SEARCH_DEFAULTS, satellite: fixedSatellite });
  const [results, setResults] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const lastCriteriaRef = useRef(null);

  useEffect(() => {
    getSearchOptions().then(data => {
      setOptions({
        satellite: data.satellite || [],
        imaging_mode: data.imaging_mode || [],
        polarization: data.polarization || [],
        product_level: data.product_level || [],
      });
    }).catch(() => {});
  }, []);

  const update = (key, val) => setDraft(d => ({ ...d, [key]: val }));

  const doSearch = async (criteria, pageNum) => {
    setLoading(true);
    try {
      const effectiveCriteria = fixedSatellite ? { ...criteria, satellite: fixedSatellite } : criteria;
      const formData = buildRadarSearchFormData({ limit: SEARCH_PAGE_SIZE, offset: pageNum * SEARCH_PAGE_SIZE, criteria: effectiveCriteria, aoiMode: 'none' });
      const data = await searchRadarData(formData);
      setResults(data.items || []);
      setTotal(data.total || 0);
    } catch {
      setResults([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  };

  const handleSearch = async () => {
    const criteria = normalizeRadarSearchCriteria(draft, SEARCH_DEFAULTS);
    lastCriteriaRef.current = criteria;
    setPage(0);
    setSearched(true);
    await doSearch(criteria, 0);
  };

  const handlePageChange = async (newPage) => {
    setPage(newPage);
    await doSearch(lastCriteriaRef.current, newPage);
  };

  const handleReset = () => { setDraft({ ...SEARCH_DEFAULTS, satellite: fixedSatellite }); setResults([]); setTotal(0); setPage(0); setSearched(false); lastCriteriaRef.current = null; };

  const sel = () => ({ ...INPUT_STYLE, padding: '5px 8px', width: '100%' });
  const totalPages = Math.ceil(total / SEARCH_PAGE_SIZE);

  return (
    <div style={{ ...NESTED_CARD_STYLE, marginBottom: 10 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginBottom: 6 }}>
        {fixedSatellite ? (
          <div style={{ ...sel(), display: 'flex', alignItems: 'center', color: UI_COLORS.primary, background: UI_COLORS.primaryPanel, borderColor: UI_COLORS.primarySoft }}>{fixedSatellite}</div>
        ) : (
          <select value={draft.satellite} onChange={e => update('satellite', e.target.value)} style={sel()}>
            <option value="">{en ? 'Satellite: All' : '卫星：全部'}</option>
            {options.satellite.map(v => <option key={v} value={v}>{v}</option>)}
          </select>
        )}
        <select value={draft.imaging_mode} onChange={e => update('imaging_mode', e.target.value)} style={sel()}>
          <option value="">{en ? 'Mode: All' : '成像模式：全部'}</option>
          {options.imaging_mode.map(v => <option key={v} value={v}>{v}</option>)}
        </select>
        <UnifiedDatePicker value={draft.imaging_date_from} onChange={v => update('imaging_date_from', v)} title={en ? 'Date from: Any' : '成像时间起：不限'} placeholder={en ? 'Start date' : '选择起始日期'} />
        <UnifiedDatePicker value={draft.imaging_date_to} onChange={v => update('imaging_date_to', v)} title={en ? 'Date to: Any' : '成像时间止：不限'} placeholder={en ? 'End date' : '选择结束日期'} />
        <select value={draft.polarization} onChange={e => update('polarization', e.target.value)} style={sel()}>
          <option value="">{en ? 'Polarization: All' : '极化方式：全部'}</option>
          {options.polarization.map(v => <option key={v} value={v}>{v}</option>)}
        </select>
        <select value={draft.product_level} onChange={e => update('product_level', e.target.value)} style={sel()}>
          <option value="">{en ? 'Level: All' : '产品级别：全部'}</option>
          {options.product_level.map(v => <option key={v} value={v}>{v}</option>)}
        </select>
      </div>
      <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
        <button onClick={handleSearch} disabled={loading} style={getButtonStyle('primary', loading, { flex: 1, padding: '6px 0' })}>
          {loading ? (en ? 'Searching...' : '搜索中...') : (en ? 'Search' : '搜索')}
        </button>
        <button onClick={handleReset} style={getButtonStyle('secondary', false, { flex: 1, padding: '6px 0' })}>
          {en ? 'Reset' : '重置'}
        </button>
      </div>

      {searched && (
        <>
          <div style={{ maxHeight: 260, overflowY: 'auto', border: `1px solid ${UI_COLORS.border}`, borderRadius: 8, background: UI_COLORS.panel }}>
            {results.length === 0 ? (
              <div style={{ ...EMPTY_STATE_STYLE, padding: 12 }}>{en ? 'No results' : '无匹配数据'}</div>
            ) : results.map(item => {
              const isSelected = selectedIds.includes(item.id);
              const isDone = doneRadarIds.includes(item.id);
              const isActive = activeRadarIds.includes(item.id);
              return (
                <div key={item.id} style={{ display: 'flex', alignItems: 'center', padding: '8px 10px', borderBottom: `1px solid ${UI_COLORS.border}`, background: isSelected ? UI_COLORS.primaryPanel : UI_COLORS.panel, opacity: isActive ? 0.7 : 1 }}>
                  <div style={{ flex: 1, fontSize: 12 }}>
                    <span style={{ color: UI_COLORS.pageText }}>{item.satellite}</span>
                    <span style={{ color: UI_COLORS.textMuted, marginLeft: 8 }}>{item.imaging_date}</span>
                    <span style={{ color: UI_COLORS.textSubtle, marginLeft: 8, fontSize: 11 }}>ID={item.id}</span>
                    {isDone && <span style={{ marginLeft: 8, ...getStatusBadgeStyle('DONE', true) }}>{en ? 'Done' : '已完成'}</span>}
                    {isActive && <span style={{ marginLeft: 8, ...getStatusBadgeStyle('RUNNING', true) }}>{en ? 'Processing' : '处理中'}</span>}
                  </div>
                  <button
                    onClick={() => !isActive && onSelect(item)}
                    disabled={isActive}
                    style={isActive
                      ? getButtonStyle('secondary', true, { padding: '3px 10px', fontSize: 11 })
                      : isSelected
                        ? getButtonStyle('success', false, { padding: '3px 10px', fontSize: 11 })
                        : getButtonStyle('primary', false, { padding: '3px 10px', fontSize: 11 })}
                  >
                    {isActive ? (en ? 'In Progress' : '进行中') : isSelected ? (en ? 'Selected' : '已选') : (en ? 'Select' : '选择')}
                  </button>
                </div>
              );
            })}
          </div>
          {total > 0 && (
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 8, fontSize: 11, color: UI_COLORS.textMuted }}>
              <span>共 {total} 条</span>
              {totalPages > 1 && (
                <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                  <button onClick={() => handlePageChange(page - 1)} disabled={page === 0 || loading} style={getButtonStyle('secondary', page === 0 || loading, { padding: '2px 8px', fontSize: 11 })}>{en ? 'Prev' : '上一页'}</button>
                  <span style={{ color: UI_COLORS.textSubtle }}>{page + 1} / {totalPages}</span>
                  <button onClick={() => handlePageChange(page + 1)} disabled={page >= totalPages - 1 || loading} style={getButtonStyle('secondary', page >= totalPages - 1 || loading, { padding: '2px 8px', fontSize: 11 })}>{en ? 'Next' : '下一页'}</button>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 预览图 Modal
// ---------------------------------------------------------------------------
function PreviewModal({ scene, onClose, language = 'zh' }) {
  if (!scene) return null;
  const en = language === 'en';
  const imgUrl = `/api/radar-data/${scene.radar_data_id}/thumb`;
  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(15, 23, 42, 0.48)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div onClick={e => e.stopPropagation()} style={{ background: UI_COLORS.panel, border: `1px solid ${UI_COLORS.border}`, borderRadius: 12, padding: 16, maxWidth: 700, width: '90%', boxShadow: '0 24px 48px rgba(15, 23, 42, 0.18)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
          <span style={{ color: UI_COLORS.pageText, fontSize: 13 }}>
            {scene.satellite} · {scene.imaging_date}
            <span style={{ color: UI_COLORS.textMuted, marginLeft: 8, fontSize: 11 }}>{en ? 'Scene ID=' : '场景 ID='}{scene.id}</span>
          </span>
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: UI_COLORS.textMuted, cursor: 'pointer', fontSize: 18, lineHeight: 1 }}>×</button>
        </div>
        <img
          src={imgUrl}
          alt={en ? 'Preview' : '预览图'}
          style={{ width: '100%', borderRadius: 4, display: 'block' }}
          onError={e => { e.target.style.display = 'none'; e.target.nextSibling.style.display = 'block'; }}
        />
        <div style={{ display: 'none', color: UI_COLORS.textMuted, fontSize: 12, textAlign: 'center', padding: 20 }}>{en ? 'Preview unavailable' : '预览图暂不可用'}</div>
        {scene.geo_path && (
          <div style={{ marginTop: 8, color: UI_COLORS.textMuted, fontSize: 11, wordBreak: 'break-all' }}>
            {en ? 'Output: ' : '输出路径：'}{scene.geo_path}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 主面板
// ---------------------------------------------------------------------------
export default function WaterMonitorPanel({ readOnly, onShowOnMap, onShowFloodOnMap, onToggleFloodLayer, onTaskStart, language = 'zh' }) {
  const en = language === 'en';
  const STATUS_LABEL = getStatusLabel(language);
  const [tab, setTab] = useState('geocode');

  // Tab: 单景预处理
  const [selectedRadars, setSelectedRadars] = useState([]);
  const [geocodeLoading, setGeocodeLoading] = useState(false);
  const [geocodeMsg, setGeocodeMsg] = useState('');
  const [scenes, setScenes] = useState([]);
  const [scenesLoading, setScenesLoading] = useState(false);
  const [previewScene, setPreviewScene] = useState(null);
  const [scenesTotal, setScenesTotal] = useState(0);
  const [scenesPage, setScenesPage] = useState(0);
  const [doneRadarIds, setDoneRadarIds] = useState([]);
  const [activeRadarIds, setActiveRadarIds] = useState([]);
  const PAGE_SIZE = 20;

  // Tab 2: 洪涝检测 — 配对查找
  const [pairPreStart, setPairPreStart] = useState('');
  const [pairPreEnd, setPairPreEnd] = useState('');
  const [pairPostStart, setPairPostStart] = useState('');
  const [pairPostEnd, setPairPostEnd] = useState('');
  const [overlapThreshold, setOverlapThreshold] = useState(0.3);
  const [candidatePairs, setCandidatePairs] = useState([]);
  const [selectedPairIdx, setSelectedPairIdx] = useState(null);
  const [pairSearching, setPairSearching] = useState(false);
  const [pairSearched, setPairSearched] = useState(false);
  const [refine, setRefine] = useState(false);
  const [floodLoading, setFloodLoading] = useState(false);
  const [floodMsg, setFloodMsg] = useState('');

  // Tab 3: 洪涝事件
  const [events, setEvents] = useState([]);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [mapLoadingId, setMapLoadingId] = useState(null); // 正在加载地图的事件ID
  const [mapLayerVis, setMapLayerVis] = useState({});     // { [evId]: { pre, post, classified } }

  // Tab: 水体检测
  const [detectGf3Selected, setDetectGf3Selected] = useState(null); // 选中的 GF3 radar_data
  const [detectInputPath, setDetectInputPath] = useState('');
  const [detectSceneId, setDetectSceneId] = useState('');
  const [detectSource, setDetectSource] = useState('gf3'); // 'gf3' | 'scene' | 'path'
  const [detectLoading, setDetectLoading] = useState(false);
  const [detectMsg, setDetectMsg] = useState('');

  // Tab: 检测结果
  const [detections, setDetections] = useState([]);
  const [detectionsTotal, setDetectionsTotal] = useState(0);
  const [detectionsPage, setDetectionsPage] = useState(0);
  const [detectionsLoading, setDetectionsLoading] = useState(false);

  const scenesPageRef = useRef(0);

  const loadStatusIds = useCallback(async () => {
    try {
      const [done, active] = await Promise.all([getWaterDoneIds(), getWaterActiveIds()]);
      setDoneRadarIds(done);
      setActiveRadarIds(active);
    } catch { /* 静默失败，不影响主列表 */ }
  }, []);

  const loadScenes = useCallback(async (page = 0) => {
    setScenesLoading(true);
    try {
      const res = (await getWaterScenes(PAGE_SIZE, page * PAGE_SIZE)).data;
      setScenes(res.items || []);
      setScenesTotal(res.total || 0);
    }
    catch { setScenes([]); setScenesTotal(0); }
    finally { setScenesLoading(false); }
  }, []);

  // 当有 PENDING/RUNNING 场景时，每 5s 自动刷新列表和状态 ID
  useEffect(() => {
    if (tab !== 'geocode') return;
    const hasActive = activeRadarIds.length > 0;
    if (!hasActive) return;
    const timer = setInterval(() => {
      loadScenes(scenesPageRef.current);
      loadStatusIds();
    }, 5000);
    return () => clearInterval(timer);
  }, [activeRadarIds, tab, loadScenes, loadStatusIds]);

  const loadEvents = useCallback(async () => {
    setEventsLoading(true);
    try { setEvents((await getFloodEvents()).data.items || []); }
    catch { setEvents([]); }
    finally { setEventsLoading(false); }
  }, []);

  const loadDetections = useCallback(async (page = 0) => {
    setDetectionsLoading(true);
    try {
      const res = (await getWaterDetections(PAGE_SIZE, page * PAGE_SIZE)).data;
      setDetections(res.items || []);
      setDetectionsTotal(res.total || 0);
    } catch { setDetections([]); setDetectionsTotal(0); }
    finally { setDetectionsLoading(false); }
  }, []);

  useEffect(() => {
    if (tab === 'geocode') { setScenesPage(0); scenesPageRef.current = 0; loadScenes(0); loadStatusIds(); }
    if (tab === 'events') loadEvents();
    if (tab === 'detect_results') loadDetections(0);
  }, [tab, loadScenes, loadStatusIds, loadEvents, loadDetections]);

  const handleGeocode = async () => {
    if (selectedRadars.length === 0) { setGeocodeMsg(en ? 'Please search and select radar data first' : '请先搜索并选择雷达数据'); return; }

    // 立即锁定前端
    if (onTaskStart) {
      onTaskStart(null, en ? 'Submitting geocoding tasks...' : '正在提交地理编码任务...');
    }

    setGeocodeLoading(true); setGeocodeMsg('');
    let ok = 0, fail = 0;
    for (const radar of selectedRadars) {
      try {
        await submitGeocode(radar.id);
        ok++;
      } catch (e) {
        fail++;
        console.error(`radar_id=${radar.id} submit failed:`, e.response?.data?.detail || e.message);
      }
    }
    setGeocodeMsg(fail === 0
      ? (en ? `Submitted ${ok} tasks` : `已提交 ${ok} 个任务`)
      : (en ? `Submitted: ${ok} succeeded, ${fail} failed` : `提交完成：${ok} 成功，${fail} 失败`));
    setSelectedRadars([]);
    setTimeout(() => { loadScenes(scenesPage); loadStatusIds(); }, 1000);
    setGeocodeLoading(false);
  };

  const handleFindPairs = async () => {
    setPairSearching(true);
    setPairSearched(false);
    setCandidatePairs([]);
    setSelectedPairIdx(null);
    setFloodMsg('');
    try {
      const r = await findWaterPairs({
        pre_start: pairPreStart,
        pre_end: pairPreEnd,
        post_start: pairPostStart,
        post_end: pairPostEnd,
        overlap_threshold: overlapThreshold,
      });
      setCandidatePairs(r.pairs || []);
      setSelectedPairIdx(r.pairs && r.pairs.length > 0 ? 0 : null);
    } catch (e) {
      setFloodMsg(`${en ? 'Search failed' : '查找失败'}: ${e.response?.data?.detail || e.message}`);
    } finally {
      setPairSearching(false);
      setPairSearched(true);
    }
  };

  const handleFloodDetect = async () => {
    if (selectedPairIdx === null) { setFloodMsg(en ? 'Please find and select a pair first' : '请先查找并选择一组配对'); return; }
    const pair = candidatePairs[selectedPairIdx];

    // 立即锁定前端
    if (onTaskStart) {
      onTaskStart(null, en ? 'Submitting precise detection task...' : '正在提交精密检测任务...');
    }

    setFloodLoading(true); setFloodMsg('');
    try {
      const res = await submitFloodDetect(pair.pre.id, pair.post.id, refine);
      const taskId = res.data.task_id;
      setFloodMsg(`${en ? 'Task submitted' : '任务已提交'}: task_id=${taskId}`);
      // 更新任务 ID
      if (onTaskStart && taskId) {
        onTaskStart(taskId, en ? 'Precise detection task started' : '精密检测任务已启动');
      }
      setTimeout(loadEvents, 1000);
    } catch (e) {
      setFloodMsg(`${en ? 'Submit failed' : '提交失败'}: ${e.response?.data?.detail || e.message}`);
    } finally { setFloodLoading(false); }
  };

  // 水体检测提交
  const handleWaterDetect = async () => {
    const params = {};
    if (detectSource === 'gf3' && detectGf3Selected) {
      params.input_path = detectGf3Selected.file_path;
    } else if (detectSource === 'scene' && detectSceneId) {
      params.scene_id = parseInt(detectSceneId, 10);
    } else if (detectSource === 'path' && detectInputPath.trim()) {
      params.input_path = detectInputPath.trim();
    } else {
      setDetectMsg(en ? 'Please provide input' : '请提供输入路径、场景 ID 或选择 GF3 数据');
      return;
    }
    setDetectLoading(true);
    setDetectMsg('');
    if (onTaskStart) onTaskStart(null, en ? 'Submitting quick detection...' : '正在提交快速检测任务...');
    try {
      await submitWaterDetect(params);
      setDetectMsg(en ? 'Task submitted!' : '任务已提交！');
      setTimeout(() => loadDetections(0), 1000);
    } catch (e) {
      setDetectMsg(`${en ? 'Failed' : '提交失败'}: ${e.response?.data?.detail || e.message}`);
    } finally { setDetectLoading(false); }
  };

  // 在地图上展示水体掩膜
  const handleShowWaterOnMap = async (det) => {
    if (!det.output_path || det.status !== 'DONE') return;
    try {
      const preview = await getWaterDetectionPreview(det.id);
      if (onShowFloodOnMap && preview.png_base64) {
        onShowFloodOnMap({
          id: `water_${det.id}`,
          png_base64: preview.png_base64,
          bounds: preview.bounds,
          label: `快速检测 #${det.id}`,
        });
      }
    } catch (e) {
      console.error('Failed to load water detection preview:', e);
    }
  };

  const tabBtn = (t, label) => (
    <button onClick={() => setTab(t)} style={getTabButtonStyle(tab === t)}>
      {label}
    </button>
  );

  return (
    <div style={{ padding: 16, color: UI_COLORS.pageText, fontSize: 13, height: '100%', overflowY: 'auto' }}>
      <PreviewModal scene={previewScene} onClose={() => setPreviewScene(null)} language={language} />

      <div style={{ display: 'flex', borderBottom: `1px solid ${UI_COLORS.border}`, marginBottom: 16, flexWrap: 'wrap' }}>
        {tabBtn('geocode', en ? 'Scene Preprocessing' : '单景预处理')}
        {tabBtn('water_detect', en ? 'Quick Detection (Otsu)' : '快速检测（Otsu）')}
        {tabBtn('detect_results', en ? 'Quick Results' : '快速检测结果')}
        {tabBtn('flood', en ? 'Precise Detection (ENVI)' : '精密检测（ENVI）')}
        {tabBtn('events', en ? 'Precise Results' : '精密检测结果')}
      </div>

      {/* ---- Tab: 单景预处理 ---- */}
      {tab === 'geocode' && (
        <div>
          <div style={SECTION_DESC_STYLE}>
            {en ? 'Search radar data and submit geocoding tasks (Import + Multilooking + Geocoding + Radiometric Calibration).' : '搜索雷达数据，可多选后批量提交地理编码任务（导入 + 多视 + 地理编码 + 辐射定标）。'}
          </div>
          {!readOnly && (
            <>
              <RadarSearchBox
                onSelect={item => setSelectedRadars(prev =>
                  prev.find(r => r.id === item.id)
                    ? prev.filter(r => r.id !== item.id)
                    : [...prev, item]
                )}
                selectedIds={selectedRadars.map(r => r.id)}
                doneRadarIds={doneRadarIds}
                activeRadarIds={activeRadarIds}
                language={language}
              />
              {selectedRadars.length > 0 && (
                <div style={{ margin: '10px 0', padding: '8px 12px', background: UI_COLORS.successPanel, border: `1px solid ${UI_COLORS.successSoft}`, borderRadius: 8, fontSize: 12 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                    <span style={{ color: UI_COLORS.success }}>{en ? `Selected ${selectedRadars.length}` : `已选 ${selectedRadars.length} 景`}</span>
                    <button onClick={() => setSelectedRadars([])} style={{ background: 'none', border: 'none', color: UI_COLORS.textMuted, cursor: 'pointer', fontSize: 11 }}>{en ? 'Clear all' : '全部取消'}</button>
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                    {selectedRadars.map(r => (
                      <span key={r.id} style={{ background: '#dcfce7', border: `1px solid ${UI_COLORS.successSoft}`, borderRadius: 999, padding: '3px 8px', fontSize: 11, color: UI_COLORS.success, display: 'flex', alignItems: 'center', gap: 4 }}>
                        {r.satellite} · {r.imaging_date}
                        <button onClick={() => setSelectedRadars(prev => prev.filter(x => x.id !== r.id))} style={{ background: 'none', border: 'none', color: UI_COLORS.textMuted, cursor: 'pointer', fontSize: 11, padding: 0, lineHeight: 1 }}>✕</button>
                      </span>
                    ))}
                  </div>
                </div>
              )}
              <div style={{ display: 'flex', gap: 8, marginBottom: 10 }}>
                <button onClick={handleGeocode} disabled={geocodeLoading || selectedRadars.length === 0} style={getButtonStyle('primary', geocodeLoading || selectedRadars.length === 0, { padding: '6px 16px' })}>
                  {geocodeLoading ? (en ? 'Submitting...' : '提交中...') : en ? `Submit Geocoding${selectedRadars.length > 1 ? ` (${selectedRadars.length})` : ''}` : `提交地理编码${selectedRadars.length > 1 ? `（${selectedRadars.length} 景）` : ''}`}
                </button>
                <button onClick={() => loadScenes(scenesPage)} style={getButtonStyle('secondary', false, { padding: '6px 10px' })}>{en ? 'Refresh' : '刷新列表'}</button>
              </div>
              {geocodeMsg && <div style={{ marginBottom: 10, color: geocodeMsg.includes('失败') ? UI_COLORS.danger : UI_COLORS.success, fontSize: 12 }}>{geocodeMsg}</div>}
            </>
          )}

          <div style={{ marginTop: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
            <span style={{ color: UI_COLORS.textMuted, fontSize: 12 }}>{en ? `Processed scenes (${scenesTotal} total)` : `已处理场景（共 ${scenesTotal} 条）`}</span>
            {!readOnly && (
              <button
                onClick={async () => {
                  if (!window.confirm(en ? 'Delete all failed records?' : '确认删除所有失败记录？')) return;
                  try {
                    const res = await cleanupFailedScenes();
                    setScenesPage(0);
                    loadScenes(0);
                    setGeocodeMsg(en ? `Cleaned ${res.data.deleted} failed records` : `已清理 ${res.data.deleted} 条失败记录`);
                  } catch (e) {
                    setGeocodeMsg(en ? `Cleanup failed: ${e.response?.data?.detail || e.message}` : `清理失败: ${e.response?.data?.detail || e.message}`);
                  }
                }}
                style={getButtonStyle('danger', false, { padding: '3px 10px', fontSize: 11 })}
              >{en ? 'Clear Failed' : '清理失败记录'}</button>            )}
          </div>
          {scenesLoading ? <div style={{ color: UI_COLORS.textMuted, fontSize: 12 }}>{en ? 'Loading...' : '加载中...'}</div> : (
            <>
              <div style={TABLE_WRAP_STYLE}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                  <thead>
                    <tr>
                      {(en ? ['Scene ID', 'Radar ID', 'Satellite', 'Date', 'Pixel Size', 'Status', 'Output', 'Actions'] : ['场景ID', '雷达ID', '卫星', '成像日期', '像素大小', '状态', '输出路径', '操作']).map(h => (
                        <th key={h} style={TABLE_HEAD_CELL_STYLE}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {scenes.map(s => (
                      <tr key={s.id}>
                        <td style={{ ...TABLE_CELL_STYLE, color: UI_COLORS.textMuted }}>{s.id}</td>
                        <td style={{ ...TABLE_CELL_STYLE, color: UI_COLORS.textSecondary }}>{s.radar_data_id}</td>
                        <td style={TABLE_CELL_STYLE}>{s.satellite || '-'}</td>
                        <td style={TABLE_CELL_STYLE}>{s.imaging_date || '-'}</td>
                        <td style={TABLE_CELL_STYLE}>{s.pixel_size_m != null ? `${s.pixel_size_m}m` : '-'}</td>
                        <td style={{ ...TABLE_CELL_STYLE, color: STATUS_COLOR[s.status] || UI_COLORS.textMuted }}>{STATUS_LABEL[s.status] || s.status}</td>
                        <td style={{ ...TABLE_CELL_STYLE, color: UI_COLORS.textMuted, maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {s.geo_path
                            ? <span title={s.geo_path}>{s.geo_path.split(/[\\/]/).pop()}</span>
                            : s.error_msg
                              ? <span style={{ color: UI_COLORS.danger }} title={s.error_msg}>{s.error_msg.slice(0, 50)}{s.error_msg.length > 50 ? '...' : ''}</span>
                              : '-'}
                        </td>
                        <td style={{ ...TABLE_CELL_STYLE, whiteSpace: 'nowrap' }}>
                          {s.status === 'DONE' && (
                            <>
                              <button
                                onClick={() => setPreviewScene(s)}
                                style={getButtonStyle('ghostPrimary', false, { padding: '2px 8px', fontSize: 11, marginRight: 4 })}
                              >{en ? 'Preview' : '预览'}</button>
                              {onShowOnMap && (
                                <button
                                  onClick={() => onShowOnMap(s)}
                                  disabled={!s.coverage_polygon}
                                  style={getButtonStyle('info', !s.coverage_polygon, { padding: '2px 8px', fontSize: 11 })}
                                >{en ? 'Map' : '地图'}</button>
                              )}
                            </>
                          )}
                          {(s.status === 'PENDING' || s.status === 'RUNNING') && (
                            <button
                              onClick={async () => {
                                try {
                                  await resetSceneStatus(s.id);
                                  loadScenes();
                                } catch (e) {
                                  alert(e.response?.data?.detail || e.message);
                                }
                              }}
                              style={getButtonStyle('danger', false, { padding: '2px 8px', fontSize: 11 })}
                            >{en ? 'Reset' : '重置'}</button>
                          )}
                        </td>
                      </tr>
                    ))}
                    {scenes.length === 0 && <tr><td colSpan={8} style={{ ...EMPTY_STATE_STYLE, padding: 12 }}> {en ? 'No records' : '暂无记录'} </td></tr>}
                  </tbody>
                </table>
                {scenesTotal > PAGE_SIZE && (
                  <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 8, padding: 10, fontSize: 12 }}>
                    <button
                      onClick={() => { const p = scenesPage - 1; setScenesPage(p); scenesPageRef.current = p; loadScenes(p); }}
                      disabled={scenesPage === 0}
                      style={getButtonStyle('secondary', scenesPage === 0, { padding: '3px 10px', fontSize: 11 })}
                    >{en ? 'Prev' : '上一页'}</button>
                    <span style={{ color: UI_COLORS.textMuted }}>{scenesPage + 1} / {Math.ceil(scenesTotal / PAGE_SIZE)}</span>
                    <button
                      onClick={() => { const p = scenesPage + 1; setScenesPage(p); scenesPageRef.current = p; loadScenes(p); }}
                      disabled={(scenesPage + 1) * PAGE_SIZE >= scenesTotal}
                      style={getButtonStyle('secondary', (scenesPage + 1) * PAGE_SIZE >= scenesTotal, { padding: '3px 10px', fontSize: 11 })}
                    >{en ? 'Next' : '下一页'}</button>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      )}

      {/* ---- Tab 2: 洪涝检测 ---- */}
      {/* ---- Tab: 水体检测 ---- */}
      {tab === 'water_detect' && (
        <div>
          <div style={SECTION_DESC_STYLE}>
            {en ? 'Quick flood detection using Otsu adaptive threshold + DEM/slope constraints + morphological filtering.' : '快速洪涝检测：Otsu 自适应阈值 + DEM/坡度约束 + 形态学处理 + 连通分量过滤。'}
          </div>
          {!readOnly && (
            <div style={{ ...PANEL_CARD_STYLE, marginBottom: 16 }}>
              {/* 数据源选择 */}
              <div style={{ marginBottom: 10 }}>
                <label style={{ color: UI_COLORS.textMuted, fontSize: 11, display: 'block', marginBottom: 6 }}>{en ? 'Data Source' : '数据来源'}</label>
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                  {[
                    { key: 'gf3', label: en ? 'GF3 Data' : 'GF3 数据' },
                    { key: 'scene', label: en ? 'Scene ID' : '场景 ID' },
                    { key: 'path', label: en ? 'GeoTIFF Path' : 'GeoTIFF 路径' },
                  ].map(({ key, label }) => (
                    <label key={key} style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer', color: UI_COLORS.textSecondary, fontSize: 12 }}>
                      <input type="radio" name="detectSource" value={key} checked={detectSource === key} onChange={() => setDetectSource(key)} style={{ accentColor: UI_COLORS.primary }} />
                      {label}
                    </label>
                  ))}
                </div>
              </div>

              {detectSource === 'gf3' && (
                <div style={{ marginBottom: 10 }}>
                  <div style={{ color: UI_COLORS.textMuted, fontSize: 11, marginBottom: 6 }}>{en ? 'Search GF3 data from database' : '从已入库的 GF3 数据中搜索'}</div>
                  <RadarSearchBox
                    onSelect={item => setDetectGf3Selected(prev => prev?.id === item.id ? null : item)}
                    selectedIds={detectGf3Selected ? [detectGf3Selected.id] : []}
                    language={language}
                    fixedSatellite="GF3"
                  />
                  {detectGf3Selected && (
                    <div style={{ marginTop: 6, padding: '6px 10px', background: UI_COLORS.successPanel, border: `1px solid ${UI_COLORS.successSoft}`, borderRadius: 8, fontSize: 11, color: UI_COLORS.success }}>
                      {en ? 'Selected: ' : '已选择：'}{detectGf3Selected.satellite} · {detectGf3Selected.imaging_date} · {detectGf3Selected.file_path}
                    </div>
                  )}
                </div>
              )}

              {detectSource === 'path' && (
                <div style={{ marginBottom: 10 }}>
                  <label style={{ color: UI_COLORS.textMuted, fontSize: 11, display: 'block', marginBottom: 4 }}>{en ? 'GeoTIFF Path' : 'GeoTIFF 文件路径'}</label>
                  <input type="text" value={detectInputPath} onChange={e => setDetectInputPath(e.target.value)} placeholder={en ? 'Path to geocoded GeoTIFF' : '地理编码后的 GeoTIFF 文件路径'} style={{ ...INPUT_STYLE, width: '100%' }} />
                </div>
              )}

              {detectSource === 'scene' && (
                <div style={{ marginBottom: 10 }}>
                  <label style={{ color: UI_COLORS.textMuted, fontSize: 11, display: 'block', marginBottom: 4 }}>{en ? 'Scene ID (from preprocessing)' : '场景 ID（来自单景预处理）'}</label>
                  <input type="number" value={detectSceneId} onChange={e => setDetectSceneId(e.target.value)} placeholder="ID" style={{ ...INPUT_STYLE, width: 140 }} />
                </div>
              )}

              <button onClick={handleWaterDetect} disabled={detectLoading} style={getButtonStyle('primary', detectLoading, { padding: '6px 20px' })}>
                {detectLoading ? (en ? 'Submitting...' : '提交中...') : (en ? 'Submit Detection' : '提交检测')}
              </button>
              {detectMsg && <div style={{ marginTop: 8, fontSize: 12, color: detectMsg.includes('失败') || detectMsg.includes('Failed') ? UI_COLORS.danger : UI_COLORS.success }}>{detectMsg}</div>}
            </div>
          )}
        </div>
      )}

      {/* ---- Tab: 检测结果 ---- */}
      {tab === 'detect_results' && (
        <div>
          <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8, color: UI_COLORS.pageText }}>{en ? 'Quick Detection Results' : '快速检测结果'}</div>
          {detectionsLoading && <div style={{ color: UI_COLORS.textMuted, fontSize: 12 }}>{en ? 'Loading...' : '加载中...'}</div>}
          {!detectionsLoading && detections.length === 0 ? (
            <div style={EMPTY_STATE_STYLE}>{en ? 'No detection results' : '暂无检测结果'}</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {detections.map(det => (
                <div key={det.id} style={PANEL_CARD_STYLE}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ color: UI_COLORS.pageText, fontWeight: 600, fontSize: 12 }}>#{det.id} {det.scene_id ? `(scene=${det.scene_id})` : ''}</span>
                    <span style={getStatusBadgeStyle(det.status)}>{STATUS_LABEL[det.status] || det.status}</span>
                  </div>
                  {det.water_area_km2 != null && (
                    <div style={{ display: 'flex', gap: 16, marginTop: 8 }}>
                      <div style={{ background: UI_COLORS.panelSoft, border: `1px solid ${UI_COLORS.border}`, borderRadius: 8, padding: '6px 10px', flex: 1, textAlign: 'center' }}>
                        <div style={{ color: UI_COLORS.primary, fontWeight: 700, fontSize: 16 }}>{det.water_area_km2.toFixed(2)}</div>
                        <div style={{ color: UI_COLORS.textMuted, fontSize: 10 }}>{en ? 'Area (km²)' : '面积 (km²)'}</div>
                      </div>
                      <div style={{ background: UI_COLORS.panelSoft, border: `1px solid ${UI_COLORS.border}`, borderRadius: 8, padding: '6px 10px', flex: 1, textAlign: 'center' }}>
                        <div style={{ color: UI_COLORS.success, fontWeight: 700, fontSize: 16 }}>{det.water_pixel_count?.toLocaleString()}</div>
                        <div style={{ color: UI_COLORS.textMuted, fontSize: 10 }}>{en ? 'Pixels' : '水体像素'}</div>
                      </div>
                      <div style={{ background: UI_COLORS.warningPanel, border: `1px solid ${UI_COLORS.warningSoft}`, borderRadius: 8, padding: '6px 10px', flex: 1, textAlign: 'center' }}>
                        <div style={{ color: UI_COLORS.warning, fontWeight: 700, fontSize: 14 }}>{det.otsu_threshold_db?.toFixed(2)}</div>
                        <div style={{ color: UI_COLORS.textMuted, fontSize: 10 }}>{en ? 'Otsu Thresh' : 'Otsu 阈值'}</div>
                      </div>
                    </div>
                  )}
                  {det.input_path && <div style={{ color: UI_COLORS.textMuted, fontSize: 11, marginTop: 6, wordBreak: 'break-all' }}>{en ? 'Input: ' : '输入：'}{det.input_path}</div>}
                  {det.output_path && <div style={{ color: UI_COLORS.textMuted, fontSize: 11, marginTop: 2, wordBreak: 'break-all' }}>{en ? 'Output: ' : '输出：'}{det.output_path}</div>}
                  {det.error_msg && <div style={{ color: UI_COLORS.danger, fontSize: 11, marginTop: 4 }}>{det.error_msg}</div>}
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 6 }}>
                    <span style={{ color: UI_COLORS.textMuted, fontSize: 10 }}>{det.created_at}</span>
                    {det.status === 'DONE' && det.output_path && (
                      <button onClick={() => handleShowWaterOnMap(det)} style={getButtonStyle('ghostPrimary', false, { padding: '3px 10px', fontSize: 11 })}>
                        {en ? 'Show on Map' : '地图查看'}
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
          {detectionsTotal > PAGE_SIZE && (
            <div style={{ display: 'flex', justifyContent: 'center', gap: 8, marginTop: 10 }}>
              <button disabled={detectionsPage <= 0} onClick={() => { setDetectionsPage(p => p - 1); loadDetections(detectionsPage - 1); }} style={getButtonStyle('secondary', detectionsPage <= 0, { padding: '4px 12px', fontSize: 11 })}>{en ? 'Prev' : '上一页'}</button>
              <span style={{ color: UI_COLORS.textMuted, fontSize: 11, lineHeight: '28px' }}>{detectionsPage + 1}/{Math.ceil(detectionsTotal / PAGE_SIZE)}</span>
              <button disabled={(detectionsPage + 1) * PAGE_SIZE >= detectionsTotal} onClick={() => { setDetectionsPage(p => p + 1); loadDetections(detectionsPage + 1); }} style={getButtonStyle('secondary', (detectionsPage + 1) * PAGE_SIZE >= detectionsTotal, { padding: '4px 12px', fontSize: 11 })}>{en ? 'Next' : '下一页'}</button>
            </div>
          )}
        </div>
      )}

      {tab === 'flood' && (
        <div>
          <div style={SECTION_DESC_STYLE}>
            {en ? 'Precise flood detection via ENVI/SARscape. Set pre/post-disaster time ranges, find matching scene pairs and submit detection tasks.' : '精密洪涝检测（ENVI/SARscape）：设置灾前/灾后时间范围，自动查找满足重叠条件的场景配对，选择后提交检测任务。'}
          </div>
          {!readOnly && (
            <>
              {/* Step 1: 时间范围 */}
              <div style={{ ...PANEL_CARD_STYLE, marginBottom: 10 }}>
                <div style={{ color: UI_COLORS.textMuted, fontSize: 11, marginBottom: 8 }}>{en ? 'Step 1 · Set Time Range' : 'Step 1 · 设置时间范围'}</div>
                <div style={{ display: 'grid', gridTemplateColumns: '50px 1fr 1fr', gap: 6, alignItems: 'center', marginBottom: 6 }}>
                  <span style={{ color: UI_COLORS.textSecondary, fontSize: 12 }}>{en ? 'Pre' : '灾前'}</span>
                  <UnifiedDatePicker value={pairPreStart} onChange={setPairPreStart} placeholder={en ? 'Start date' : '开始日期'} />
                  <UnifiedDatePicker value={pairPreEnd} onChange={setPairPreEnd} placeholder={en ? 'End date' : '结束日期'} />
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '50px 1fr 1fr', gap: 6, alignItems: 'center', marginBottom: 10 }}>
                  <span style={{ color: UI_COLORS.textSecondary, fontSize: 12 }}>{en ? 'Post' : '灾后'}</span>
                  <UnifiedDatePicker value={pairPostStart} onChange={setPairPostStart} placeholder={en ? 'Start date' : '开始日期'} />
                  <UnifiedDatePicker value={pairPostEnd} onChange={setPairPostEnd} placeholder={en ? 'End date' : '结束日期'} />
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <span style={{ color: UI_COLORS.textSecondary, fontSize: 12 }}>{en ? 'Overlap Threshold' : '重叠阈值'}</span>
                  <input
                    type="number" min="0" max="1" step="0.05"
                    value={overlapThreshold}
                    onChange={e => setOverlapThreshold(parseFloat(e.target.value) || 0)}
                    style={{ ...INPUT_STYLE, width: 72, padding: '4px 6px' }}
                  />
                  <span style={{ color: UI_COLORS.textMuted, fontSize: 11 }}>（0~1）</span>
                  <button
                    onClick={handleFindPairs}
                    disabled={pairSearching}
                    style={getButtonStyle('primary', pairSearching, { marginLeft: 'auto', padding: '5px 14px' })}
                  >
                    {pairSearching ? (en ? 'Searching...' : '查找中...') : (en ? 'Find Pairs' : '查找候选配对')}
                  </button>
                </div>
              </div>

              {/* Step 2: 候选配对列表 */}
              {(pairSearched || candidatePairs.length > 0) && (
                <div style={{ ...PANEL_CARD_STYLE, marginBottom: 10 }}>
                  <div style={{ color: UI_COLORS.textMuted, fontSize: 11, marginBottom: 8 }}>
                    {en ? 'Step 2 · Select Pair' : 'Step 2 · 选择配对'}
                    {candidatePairs.length > 0 && <span style={{ color: UI_COLORS.primary, marginLeft: 6 }}>{en ? `Found ${candidatePairs.length}` : `找到 ${candidatePairs.length} 组`}</span>}
                  </div>
                  {candidatePairs.length === 0 ? (
                    <div style={{ ...EMPTY_STATE_STYLE, padding: '12px 0' }}>{en ? 'No pairs found. Adjust time range or lower overlap threshold.' : '未找到满足条件的配对，请调整时间范围或降低重叠阈值'}</div>
                  ) : (
                    <div style={{ maxHeight: 220, overflowY: 'auto' }}>
                      {candidatePairs.map((pair, idx) => (
                        <div
                          key={idx}
                          onClick={() => setSelectedPairIdx(idx)}
                          style={{
                            display: 'flex', alignItems: 'center', gap: 10,
                            padding: '8px 10px', marginBottom: 4,
                            background: selectedPairIdx === idx ? UI_COLORS.primaryPanel : UI_COLORS.panelSoft,
                            border: `1px solid ${selectedPairIdx === idx ? UI_COLORS.primarySoft : UI_COLORS.border}`,
                            borderRadius: 8, cursor: 'pointer',
                          }}
                        >
                          <input type="radio" readOnly checked={selectedPairIdx === idx} style={{ cursor: 'pointer', accentColor: UI_COLORS.primary }} />
                          <div style={{ flex: 1, fontSize: 12 }}>
                            <span style={{ color: UI_COLORS.success }}>{en ? 'Pre' : '灾前'} {pair.pre.imaging_date}</span>
                            <span style={{ color: UI_COLORS.textMuted, margin: '0 6px' }}>→</span>
                            <span style={{ color: UI_COLORS.danger }}>{en ? 'Post' : '灾后'} {pair.post.imaging_date}</span>
                            <span style={{ color: UI_COLORS.textMuted, marginLeft: 8, fontSize: 11 }}>
                              {en ? 'Overlap' : '重叠'} {(pair.overlap_ratio * 100).toFixed(1)}%
                              {pair.time_diff_days != null && ` · ${en ? 'Diff' : '时间差'} ${pair.time_diff_days} ${en ? 'd' : '天'}`}
                            </span>
                          </div>
                          <span style={{ fontSize: 11, color: UI_COLORS.textMuted }}>{pair.pre.satellite}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Step 3: 提交 */}
              <div style={{ ...PANEL_CARD_STYLE, marginBottom: 10 }}>
                <div style={{ color: UI_COLORS.textMuted, fontSize: 11, marginBottom: 8 }}>{en ? 'Step 3 · Submit' : 'Step 3 · 提交'}</div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10 }}>
                  <input type="checkbox" id="refine-cb" checked={refine} onChange={e => setRefine(e.target.checked)} style={{ accentColor: UI_COLORS.primary }} />
                  <label htmlFor="refine-cb" style={{ color: UI_COLORS.textSecondary, fontSize: 12, cursor: 'pointer' }}>{en ? 'MRF Refinement' : 'MRF 精化'}</label>
                  <span style={{ color: UI_COLORS.textMuted, fontSize: 11 }}>{en ? 'Higher accuracy, longer processing' : '精度更高，耗时更长'}</span>
                </div>
                <button
                  onClick={handleFloodDetect}
                  disabled={floodLoading || selectedPairIdx === null}
                  style={getButtonStyle('primary', floodLoading || selectedPairIdx === null, { padding: '5px 16px' })}
                >
                  {floodLoading ? (en ? 'Submitting...' : '提交中...') : (en ? 'Submit Detection' : '提交检测')}
                </button>
                {floodMsg && <div style={{ marginTop: 8, color: floodMsg.includes('失败') ? UI_COLORS.danger : UI_COLORS.success, fontSize: 12 }}>{floodMsg}</div>}
              </div>
            </>
          )}
        </div>
      )}

      {/* ---- Tab 3: 洪涝事件 ---- */}
      {tab === 'events' && (
        <div>
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 10 }}>
            <button onClick={loadEvents} style={getButtonStyle('secondary', false, { padding: '4px 10px' })}>{en ? 'Refresh' : '刷新'}</button>
          </div>
          {eventsLoading ? <div style={{ color: UI_COLORS.textMuted, fontSize: 12 }}>{en ? 'Loading...' : '加载中...'}</div> : (
            events.length === 0
              ? <div style={{ ...EMPTY_STATE_STYLE, padding: 24 }}>{en ? 'No detection records' : '暂无检测记录'}</div>
              : <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  {events.map(ev => {
                    const totalArea = (ev.flood_area_km2 || 0) + (ev.stable_water_area_km2 || 0);
                    const floodRatio = totalArea > 0 ? (ev.flood_area_km2 || 0) / totalArea : 0;
                    const stableRatio = totalArea > 0 ? (ev.stable_water_area_km2 || 0) / totalArea : 0;
                    const isDone = ev.status === 'DONE';
                    const isFailed = ev.status === 'FAILED';
                    const finishTime = ev.updated_at && isDone
                      ? ev.updated_at.slice(0, 16).replace('T', ' ')
                      : null;
                    const preDate = ev.pre_imaging_date || `场景${ev.pre_scene_id}`;
                    const postDate = ev.post_imaging_date || `场景${ev.post_scene_id}`;
                    const satellite = ev.pre_satellite || ev.post_satellite || '-';
                    let timeDiff = null;
                    if (ev.pre_imaging_date && ev.post_imaging_date) {
                      try {
                        const d1 = new Date(ev.pre_imaging_date.replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3'));
                        const d2 = new Date(ev.post_imaging_date.replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3'));
                        timeDiff = Math.abs(Math.round((d2 - d1) / 86400000));
                      } catch {
                        timeDiff = null;
                      }
                    }
                    return (
                      <div key={ev.id} style={{ background: UI_COLORS.panel, border: `1px solid ${isFailed ? UI_COLORS.dangerSoft : isDone ? UI_COLORS.successSoft : UI_COLORS.border}`, borderRadius: 10, padding: 14, position: 'relative', boxShadow: PANEL_SHADOW }}>
                        {/* 顶部：ID + 卫星 + 状态 */}
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                          <span style={{ color: UI_COLORS.textMuted, fontSize: 11 }}>#{ev.id} · {satellite}</span>
                          <span style={getStatusBadgeStyle(ev.status)}>{STATUS_LABEL[ev.status] || ev.status}</span>
                        </div>

                        {/* 时间轴：灾前 → 灾后 */}
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
                          <div style={{ flex: 1, background: UI_COLORS.successPanel, border: `1px solid ${UI_COLORS.successSoft}`, borderRadius: 8, padding: '6px 10px', textAlign: 'center' }}>
                            <div style={{ color: UI_COLORS.textMuted, fontSize: 10, marginBottom: 2 }}>{en ? 'Pre' : '灾前'}</div>
                            <div style={{ color: UI_COLORS.success, fontSize: 13, fontWeight: 600 }}>{preDate}</div>
                          </div>
                          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', color: UI_COLORS.textMuted, fontSize: 11 }}>
                            <span>→</span>
                            {timeDiff != null && <span style={{ fontSize: 10, marginTop: 2 }}>{timeDiff}{en ? 'd' : '天'}</span>}
                          </div>
                          <div style={{ flex: 1, background: UI_COLORS.dangerPanel, border: `1px solid ${UI_COLORS.dangerSoft}`, borderRadius: 8, padding: '6px 10px', textAlign: 'center' }}>
                            <div style={{ color: UI_COLORS.textMuted, fontSize: 10, marginBottom: 2 }}>{en ? 'Post' : '灾后'}</div>
                            <div style={{ color: UI_COLORS.danger, fontSize: 13, fontWeight: 600 }}>{postDate}</div>
                          </div>
                        </div>

                        {/* 面积统计 */}
                        {isDone && (
                          <div style={{ marginBottom: 10 }}>
                            <div style={{ display: 'flex', gap: 8, marginBottom: 6 }}>
                              <div style={{ flex: 1, background: UI_COLORS.dangerPanel, border: `1px solid ${UI_COLORS.dangerSoft}`, borderRadius: 8, padding: '6px 10px' }}>
                                <div style={{ color: UI_COLORS.textMuted, fontSize: 10, marginBottom: 2 }}>{en ? 'Flood Area' : '洪涝面积'}</div>
                                <div style={{ color: ev.flood_area_km2 > 0 ? UI_COLORS.danger : UI_COLORS.textMuted, fontSize: 14, fontWeight: 600 }}>
                                  {ev.flood_area_km2 != null ? `${ev.flood_area_km2} km²` : '-'}
                                </div>
                              </div>
                              <div style={{ flex: 1, background: UI_COLORS.infoPanel, border: `1px solid ${UI_COLORS.infoSoft}`, borderRadius: 8, padding: '6px 10px' }}>
                                <div style={{ color: UI_COLORS.textMuted, fontSize: 10, marginBottom: 2 }}>{en ? 'Stable Water' : '稳定水体'}</div>
                                <div style={{ color: UI_COLORS.info, fontSize: 14, fontWeight: 600 }}>
                                  {ev.stable_water_area_km2 != null ? `${ev.stable_water_area_km2} km²` : '-'}
                                </div>
                              </div>
                            </div>
                            {totalArea > 0 && (
                              <div style={{ height: 6, borderRadius: 3, overflow: 'hidden', background: UI_COLORS.panelSoft, display: 'flex' }}>
                                <div style={{ width: `${floodRatio * 100}%`, background: UI_COLORS.danger, transition: 'width 0.3s' }} title={`洪涝 ${(floodRatio * 100).toFixed(1)}%`} />
                                <div style={{ width: `${stableRatio * 100}%`, background: UI_COLORS.primary, transition: 'width 0.3s' }} title={`稳定水体 ${(stableRatio * 100).toFixed(1)}%`} />
                              </div>
                            )}
                          </div>
                        )}

                        {/* 错误信息 */}
                        {isFailed && ev.error_msg && (
                          <div style={{ color: UI_COLORS.danger, fontSize: 11, background: UI_COLORS.dangerPanel, border: `1px solid ${UI_COLORS.dangerSoft}`, borderRadius: 8, padding: '6px 8px', marginBottom: 8, wordBreak: 'break-all' }}>
                            {ev.error_msg.slice(0, 120)}{ev.error_msg.length > 120 ? '...' : ''}
                          </div>
                        )}

                        {/* 底部：完成时间 / 输出路径 / 地图按钮 */}
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 10, color: UI_COLORS.textMuted }}>
                          {ev.classified_path
                            ? <span title={ev.classified_path} style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '50%' }}>
                                {en ? 'Output: ' : '输出: '}{ev.classified_path.split('/').pop()}
                              </span>
                            : <span />}
                          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                            {finishTime && <span style={{ color: UI_COLORS.textMuted }}>{en ? 'Done at' : '完成于'} {finishTime}</span>}
                            {!finishTime && ev.created_at && <span>{en ? 'Submitted at' : '提交于'} {ev.created_at.slice(0, 16).replace('T', ' ')}</span>}
                            {isDone && onShowFloodOnMap && (
                              <button
                                onClick={async () => {
                                  setMapLoadingId(ev.id);
                                  try {
                                    const [pre, post, cls] = await Promise.all([
                                      getFloodEventPreview(ev.id, 'pre').catch(() => null),
                                      getFloodEventPreview(ev.id, 'post').catch(() => null),
                                      getFloodEventPreview(ev.id, 'classified').catch(() => null),
                                    ]);
                                    onShowFloodOnMap(ev, { pre, post, classified: cls });
                                    setMapLayerVis(v => ({
                                      ...v,
                                      [ev.id]: { pre: !!pre, post: !!post, classified: !!cls },
                                    }));
                                  } finally {
                                    setMapLoadingId(null);
                                  }
                                }}
                                disabled={mapLoadingId === ev.id}
                                style={getButtonStyle('ghostPrimary', mapLoadingId === ev.id, { padding: '2px 8px', fontSize: 11, whiteSpace: 'nowrap' })}
                              >
                                {mapLoadingId === ev.id ? (en ? 'Loading...' : '加载中...') : (en ? 'Show on Map' : '在地图显示')}
                              </button>
                            )}
                          </div>
                        </div>

                        {/* 图层开关（加载后显示） */}
                        {mapLayerVis[ev.id] && (
                          <div style={{ marginTop: 8, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                            {[
                              { key: 'pre', label: en ? 'Pre-disaster' : '灾前影像', color: UI_COLORS.success },
                              { key: 'post', label: en ? 'Post-disaster' : '灾后影像', color: UI_COLORS.danger },
                              { key: 'classified', label: en ? 'Classification' : '分类结果', color: UI_COLORS.primary },
                            ].map(({ key, label, color }) => {
                              const vis = mapLayerVis[ev.id]?.[key];
                              return (
                                <button
                                  key={key}
                                  onClick={() => {
                                    const next = !vis;
                                    setMapLayerVis(v => ({ ...v, [ev.id]: { ...v[ev.id], [key]: next } }));
                                    onToggleFloodLayer && onToggleFloodLayer(ev.id, key, next);
                                  }}
                                  style={{
                                    padding: '2px 8px', fontSize: 11, borderRadius: 3, cursor: 'pointer',
                                    background: vis ? `${color}22` : UI_COLORS.panelSoft,
                                    border: `1px solid ${vis ? color : UI_COLORS.border}`,
                                    color: vis ? color : UI_COLORS.textMuted,
                                  }}
                                >
                                  {vis ? '●' : '○'} {label}
                                </button>
                              );
                            })}
                          </div>
                        )}

                        {/* 分类结果色表图例（加载后显示） */}
                        {mapLayerVis[ev.id]?.classified && (
                          <div style={{ marginTop: 6, padding: '6px 10px', background: UI_COLORS.panelSoft, border: `1px solid ${UI_COLORS.border}`, borderRadius: 8 }}>
                            <div style={{ color: UI_COLORS.textMuted, fontSize: 10, marginBottom: 4 }}>{en ? 'Legend' : '分类图例'}</div>
                            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                              {[
                                { color: UI_COLORS.primary, label: en ? 'Stable Water' : '稳定水体' },
                                { color: UI_COLORS.danger, label: en ? 'Flood' : '洪涝' },
                                { color: '#f59e0b', label: en ? 'High Scatter' : '高散射' },
                                { color: '#505050', label: en ? 'Non-water' : '非水体' },
                              ].map(({ color, label }) => (
                                <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                                  <div style={{ width: 12, height: 12, borderRadius: 2, background: color, flexShrink: 0 }} />
                                  <span style={{ color: UI_COLORS.textMuted, fontSize: 10 }}>{label}</span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
          )}
        </div>
      )}
    </div>
  );
}
