import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  getLandsarLt1Capabilities,
  listLandsarLt1Products,
  previewLandsarLt1Production,
  submitLandsarLt1Production,
} from './api/landsarLt1Production';
import { searchRadarData } from './api/radar';
import { getRegionChildren } from './api/aoi';

const PAGE_SIZE_OPTIONS = [50, 100, 200, 500];
const DEFAULT_SEARCH = {
  imaging_date_from: '',
  imaging_date_to: '',
  imaging_mode: '',
  polarization: '',
  product_level: '',
  orbit_direction: '',
  relative_orbit: '',
  product_unique_id: '',
};

const shellStyle = { display: 'grid', gap: 12 };
const sectionStyle = {
  background: '#ffffff',
  border: '1px solid #d8dee8',
  borderRadius: 8,
  padding: 14,
};
const gridStyle = {
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
  gap: 12,
};
const labelStyle = {
  display: 'grid',
  gap: 5,
  color: '#475569',
  fontSize: 12,
  fontWeight: 650,
};
const inputStyle = {
  width: '100%',
  boxSizing: 'border-box',
  border: '1px solid #cbd5e1',
  borderRadius: 6,
  padding: '8px 9px',
  color: '#0f172a',
  background: '#ffffff',
  fontSize: 13,
  lineHeight: 1.35,
};
const mutedStyle = { color: '#64748b', fontSize: 12, lineHeight: 1.55 };
const buttonStyle = {
  border: '1px solid #2563eb',
  borderRadius: 6,
  background: '#2563eb',
  color: '#ffffff',
  padding: '8px 12px',
  fontSize: 13,
  fontWeight: 700,
  cursor: 'pointer',
};
const ghostButtonStyle = {
  ...buttonStyle,
  border: '1px solid #cbd5e1',
  background: '#ffffff',
  color: '#334155',
};
const disabledButtonStyle = {
  opacity: 0.5,
  cursor: 'not-allowed',
};
const tableHeaderStyle = {
  textAlign: 'left',
  padding: 8,
  borderBottom: '1px solid #e2e8f0',
};
const tableCellStyle = {
  padding: 8,
  borderBottom: '1px solid #e2e8f0',
};

function formatTime(value) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function formatYmd(value) {
  const text = String(value || '').trim();
  const compact = text.match(/^(\d{4})(\d{2})(\d{2})$/);
  if (compact) return `${compact[1]}-${compact[2]}-${compact[3]}`;
  return text || '-';
}

function StatusPill({ ok, text }) {
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        border: `1px solid ${ok ? '#86efac' : '#fecaca'}`,
        borderRadius: 999,
        padding: '3px 8px',
        color: ok ? '#166534' : '#991b1b',
        background: ok ? '#f0fdf4' : '#fef2f2',
        fontSize: 12,
        fontWeight: 700,
      }}
    >
      {text}
    </span>
  );
}

function sceneProduced(scene) {
  return Boolean(scene.lt1_image_produced || scene.lt1_landsar_produced);
}

function getSceneTitle(scene) {
  return scene.product_unique_id || scene.unique_id || scene.source_product_token || `radar:${scene.id}`;
}

function getErrorMessage(error, fallback) {
  const detail = error?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (detail) return JSON.stringify(detail);
  return error?.message || fallback;
}

function buildRadarSearchFormData(criteria, page, regionTreeId) {
  const formData = new FormData();
  formData.append('limit', String(page.limit));
  formData.append('offset', String(page.offset));
  formData.append('satellite_family', 'LT1');
  formData.append('source_format', 'LT1_ARCHIVE');
  Object.entries(criteria || {}).forEach(([key, rawValue]) => {
    const value = String(rawValue ?? '').trim();
    if (value) formData.append(key, value);
  });
  if (regionTreeId) {
    formData.append('region_tree_id', regionTreeId);
  }
  return formData;
}

export default function LandsarLt1ProductionPanel({ readOnly, onJobQueued }) {
  const [capabilities, setCapabilities] = useState(null);
  const [products, setProducts] = useState([]);
  const [scenes, setScenes] = useState([]);
  const [scenePage, setScenePage] = useState({
    limit: 100,
    offset: 0,
    total: 0,
    hasMore: false,
  });
  const [searchDraft, setSearchDraft] = useState(DEFAULT_SEARCH);
  const [searchApplied, setSearchApplied] = useState(DEFAULT_SEARCH);
  const [regionMode, setRegionMode] = useState('none');
  const [regionSelection, setRegionSelection] = useState({ province: '', city: '' });
  const [regionOptions, setRegionOptions] = useState({ provinces: [], cities: [] });
  const [selectedRadarIds, setSelectedRadarIds] = useState(() => new Set());
  const [preview, setPreview] = useState(null);
  const [message, setMessage] = useState('');
  const [actionLoading, setActionLoading] = useState(false);
  const [searchLoading, setSearchLoading] = useState(false);
  const [regionLoading, setRegionLoading] = useState(false);
  const [form, setForm] = useState({ mode: 'scene', taskName: '' });

  const selectedRegionTreeId = regionSelection.city || regionSelection.province || '';
  const producedRadarIds = useMemo(
    () => new Set(scenes.filter(sceneProduced).map(scene => Number(scene.id))),
    [scenes],
  );
  const selectedRadarIdList = useMemo(
    () => [...selectedRadarIds].filter(id => !producedRadarIds.has(Number(id))),
    [selectedRadarIds, producedRadarIds],
  );
  const selectableCurrentScenes = useMemo(
    () => scenes.filter(scene => !sceneProduced(scene) && scene.source_product_ref_id),
    [scenes],
  );
  const allCurrentSelectableSelected = selectableCurrentScenes.length > 0
    && selectableCurrentScenes.every(scene => selectedRadarIds.has(scene.id));
  const sceneStart = scenePage.total === 0 ? 0 : scenePage.offset + 1;
  const sceneEnd = Math.min(scenePage.offset + scenes.length, scenePage.total || scenePage.offset + scenes.length);
  const payload = useMemo(() => ({
    radar_data_ids: selectedRadarIdList,
    mode: form.mode,
    task_name: form.taskName.trim() || undefined,
  }), [form, selectedRadarIdList]);

  const refreshProducts = useCallback(async () => {
    const result = await listLandsarLt1Products({ limit: 20, offset: 0 });
    setProducts(Array.isArray(result?.items) ? result.items : []);
  }, []);

  const refreshScenes = useCallback(async () => {
    setSearchLoading(true);
    try {
      const result = await searchRadarData(
        buildRadarSearchFormData(
          searchApplied,
          scenePage,
          regionMode === 'region' ? selectedRegionTreeId : '',
        ),
      );
      const items = Array.isArray(result?.items) ? result.items : [];
      const total = Number(result?.total ?? items.length);
      const offset = Number(result?.offset ?? scenePage.offset);
      const limit = Number(result?.limit ?? scenePage.limit);
      setScenes(items);
      setScenePage(current => ({
        ...current,
        limit,
        offset,
        total,
        hasMore: Boolean(result?.has_more ?? (offset + items.length < total)),
      }));
    } finally {
      setSearchLoading(false);
    }
  }, [regionMode, scenePage.limit, scenePage.offset, searchApplied, selectedRegionTreeId]);

  const refreshCapabilities = useCallback(async () => {
    const result = await getLandsarLt1Capabilities();
    setCapabilities(result);
  }, []);

  useEffect(() => {
    refreshCapabilities().catch(error => setMessage(getErrorMessage(error, '读取 LT-1 生产能力失败')));
    refreshProducts().catch(() => {});
  }, [refreshCapabilities, refreshProducts]);

  useEffect(() => {
    refreshScenes().catch(error => setMessage(getErrorMessage(error, '检索 LT-1 影像失败')));
  }, [refreshScenes]);

  useEffect(() => {
    if (!producedRadarIds.size) return;
    setSelectedRadarIds(current => {
      let changed = false;
      const next = new Set();
      current.forEach(id => {
        if (producedRadarIds.has(Number(id))) changed = true;
        else next.add(id);
      });
      return changed ? next : current;
    });
  }, [producedRadarIds]);

  const loadProvinces = useCallback(async () => {
    setRegionLoading(true);
    setMessage('');
    try {
      const result = await getRegionChildren('1');
      setRegionOptions({ provinces: Array.isArray(result?.children) ? result.children : [], cities: [] });
    } catch (error) {
      setMessage(getErrorMessage(error, '加载行政区失败'));
    } finally {
      setRegionLoading(false);
    }
  }, []);

  const loadCities = useCallback(async provinceId => {
    if (!provinceId) {
      setRegionOptions(current => ({ ...current, cities: [] }));
      return;
    }
    setRegionLoading(true);
    setMessage('');
    try {
      const result = await getRegionChildren(provinceId);
      setRegionOptions(current => ({ ...current, cities: Array.isArray(result?.children) ? result.children : [] }));
    } catch (error) {
      setMessage(getErrorMessage(error, '加载地市失败'));
    } finally {
      setRegionLoading(false);
    }
  }, []);

  const updateField = (field, value) => {
    setForm(current => ({ ...current, [field]: value }));
    setPreview(null);
    setMessage('');
  };

  const updateSearchDraft = (field, value) => {
    setSearchDraft(current => ({ ...current, [field]: value }));
    setPreview(null);
    setMessage('');
  };

  const updateRegionMode = async value => {
    setRegionMode(value);
    setRegionSelection({ province: '', city: '' });
    setPreview(null);
    setMessage('');
    if (value === 'region' && regionOptions.provinces.length === 0) {
      await loadProvinces();
    }
  };

  const updateProvince = async value => {
    setRegionSelection({ province: value, city: '' });
    setPreview(null);
    if (value) await loadCities(value);
    else setRegionOptions(current => ({ ...current, cities: [] }));
  };

  const updateCity = value => {
    setRegionSelection(current => ({ ...current, city: value }));
    setPreview(null);
  };

  const toggleScene = scene => {
    if (sceneProduced(scene) || !scene.source_product_ref_id) return;
    setPreview(null);
    setMessage('');
    setSelectedRadarIds(current => {
      const next = new Set(current);
      if (next.has(scene.id)) next.delete(scene.id);
      else next.add(scene.id);
      return next;
    });
  };

  const toggleCurrentPageSelection = () => {
    setPreview(null);
    setMessage('');
    setSelectedRadarIds(current => {
      const next = new Set(current);
      if (allCurrentSelectableSelected) {
        selectableCurrentScenes.forEach(scene => next.delete(scene.id));
      } else {
        selectableCurrentScenes.forEach(scene => next.add(scene.id));
      }
      return next;
    });
  };

  const updatePageSize = value => {
    const limit = Number(value);
    setScenePage(current => ({ ...current, limit, offset: 0 }));
  };

  const goToScenePage = offset => {
    setScenePage(current => ({ ...current, offset: Math.max(0, offset) }));
  };

  const applySearch = () => {
    if (regionMode === 'region' && !selectedRegionTreeId) {
      setMessage('请选择行政区。');
      return;
    }
    setSearchApplied(searchDraft);
    setScenePage(current => ({ ...current, offset: 0 }));
    setPreview(null);
    setMessage('');
  };

  const resetSearch = () => {
    setSearchDraft(DEFAULT_SEARCH);
    setSearchApplied(DEFAULT_SEARCH);
    setRegionMode('none');
    setRegionSelection({ province: '', city: '' });
    setScenePage(current => ({ ...current, offset: 0 }));
    setPreview(null);
    setMessage('');
  };

  const handleRefresh = async () => {
    setMessage('');
    try {
      await Promise.all([refreshProducts(), refreshScenes()]);
    } catch (error) {
      setMessage(getErrorMessage(error, '刷新失败'));
    }
  };

  const handlePreview = async () => {
    setActionLoading(true);
    setMessage('');
    try {
      const result = await previewLandsarLt1Production(payload);
      setPreview(result);
      setMessage(result.allow_submit ? '预览通过' : '预览未通过');
    } catch (error) {
      setPreview(null);
      setMessage(getErrorMessage(error, '预览失败'));
    } finally {
      setActionLoading(false);
    }
  };

  const handleSubmit = async () => {
    setActionLoading(true);
    setMessage('');
    try {
      const result = await submitLandsarLt1Production(payload);
      const queued = Array.isArray(result.queued) ? result.queued : [];
      setMessage(`已提交 ${queued.length || 1} 个地理编码 GeoTIFF 生产任务`);
      if (result.task_id) onJobQueued?.(result.task_id);
      else queued.forEach(item => item.task_id && onJobQueued?.(item.task_id));
      setPreview(null);
      setSelectedRadarIds(new Set());
      await Promise.all([refreshProducts(), refreshScenes()]);
    } catch (error) {
      setMessage(getErrorMessage(error, '提交失败'));
    } finally {
      setActionLoading(false);
    }
  };

  const busy = actionLoading || searchLoading || regionLoading;
  const canSubmitSelection = !readOnly && selectedRadarIdList.length > 0 && !actionLoading;

  return (
    <div style={shellStyle}>
      <section style={sectionStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <div>
            <h3 style={{ margin: 0, fontSize: 16 }}>LT-1 地理编码影像生产</h3>
            <div style={{ ...mutedStyle, marginTop: 5 }}>
              复用影像检索筛选 LT-1 场景，再提交 Gamma 单景流水线生成 analysis_ready.tif。
            </div>
          </div>
          <StatusPill
            ok={capabilities?.engine === 'lt_gamma'}
            text={capabilities?.engine === 'lt_gamma' ? 'lt_gamma 已配置' : '未配置'}
          />
        </div>
        {capabilities?.message && <div style={{ ...mutedStyle, marginTop: 8 }}>{capabilities.message}</div>}
      </section>

      <section style={sectionStyle}>
        <div style={gridStyle}>
          <label style={labelStyle}>
            生产模式
            <select
              style={inputStyle}
              value={form.mode}
              onChange={event => updateField('mode', event.target.value)}
              disabled={actionLoading || readOnly}
            >
              <option value="scene">单景</option>
              <option value="batch">批量单景</option>
            </select>
          </label>
          <label style={labelStyle}>
            任务名
            <input
              style={inputStyle}
              value={form.taskName}
              onChange={event => updateField('taskName', event.target.value)}
              disabled={actionLoading || readOnly}
              placeholder="可选"
            />
          </label>
        </div>

        <div style={{ display: 'flex', gap: 8, marginTop: 14, flexWrap: 'wrap' }}>
          <button
            type="button"
            style={{ ...ghostButtonStyle, ...((!canSubmitSelection || actionLoading) ? disabledButtonStyle : {}) }}
            onClick={handlePreview}
            disabled={!canSubmitSelection}
          >
            预览
          </button>
          <button
            type="button"
            style={{ ...buttonStyle, ...((!canSubmitSelection || preview?.allow_submit === false) ? disabledButtonStyle : {}) }}
            onClick={handleSubmit}
            disabled={!canSubmitSelection || preview?.allow_submit === false}
          >
            提交生产
          </button>
          <button
            type="button"
            style={{ ...ghostButtonStyle, ...(busy ? disabledButtonStyle : {}) }}
            onClick={handleRefresh}
            disabled={busy}
          >
            刷新
          </button>
        </div>
        {message && <div style={{ ...mutedStyle, marginTop: 10 }}>{message}</div>}
      </section>

      <section style={sectionStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <div>
            <h3 style={{ margin: 0, fontSize: 16 }}>生产候选检索</h3>
            <div style={{ ...mutedStyle, marginTop: 4 }}>
              调用影像检索能力，按时间、行政区、轨道、极化等条件规划要生产的 LT-1 场景。
            </div>
          </div>
          <div style={mutedStyle}>已选 {selectedRadarIdList.length} 景</div>
        </div>

        <div style={{ ...gridStyle, marginTop: 12 }}>
          <label style={labelStyle}>
            成像时间起
            <input
              type="date"
              style={inputStyle}
              value={searchDraft.imaging_date_from}
              onChange={event => updateSearchDraft('imaging_date_from', event.target.value)}
            />
          </label>
          <label style={labelStyle}>
            成像时间止
            <input
              type="date"
              style={inputStyle}
              value={searchDraft.imaging_date_to}
              onChange={event => updateSearchDraft('imaging_date_to', event.target.value)}
            />
          </label>
          <label style={labelStyle}>
            成像模式
            <input
              style={inputStyle}
              value={searchDraft.imaging_mode}
              onChange={event => updateSearchDraft('imaging_mode', event.target.value)}
              placeholder="如 MONO / KSC"
            />
          </label>
          <label style={labelStyle}>
            极化
            <input
              style={inputStyle}
              value={searchDraft.polarization}
              onChange={event => updateSearchDraft('polarization', event.target.value)}
              placeholder="如 HH"
            />
          </label>
          <label style={labelStyle}>
            相对轨道
            <input
              style={inputStyle}
              value={searchDraft.relative_orbit}
              onChange={event => updateSearchDraft('relative_orbit', event.target.value)}
              placeholder="可选"
            />
          </label>
          <label style={labelStyle}>
            产品名
            <input
              style={inputStyle}
              value={searchDraft.product_unique_id}
              onChange={event => updateSearchDraft('product_unique_id', event.target.value)}
              placeholder="模糊匹配"
            />
          </label>
        </div>

        <div style={{ ...gridStyle, marginTop: 12 }}>
          <label style={labelStyle}>
            空间范围
            <select
              style={inputStyle}
              value={regionMode}
              onChange={event => updateRegionMode(event.target.value)}
              disabled={regionLoading}
            >
              <option value="none">不限</option>
              <option value="region">行政区</option>
            </select>
          </label>
          {regionMode === 'region' && (
            <>
              <label style={labelStyle}>
                省份
                <select
                  style={inputStyle}
                  value={regionSelection.province}
                  onChange={event => updateProvince(event.target.value)}
                  disabled={regionLoading}
                >
                  <option value="">选择省份</option>
                  {regionOptions.provinces.map(item => (
                    <option key={item.tree_id} value={item.tree_id}>{item.name}</option>
                  ))}
                </select>
              </label>
              <label style={labelStyle}>
                地市
                <select
                  style={inputStyle}
                  value={regionSelection.city}
                  onChange={event => updateCity(event.target.value)}
                  disabled={regionLoading || !regionSelection.province}
                >
                  <option value="">不限地市</option>
                  {regionOptions.cities.map(item => (
                    <option key={item.tree_id} value={item.tree_id}>{item.name}</option>
                  ))}
                </select>
              </label>
            </>
          )}
          <label style={labelStyle}>
            每页数量
            <select
              style={inputStyle}
              value={scenePage.limit}
              onChange={event => updatePageSize(event.target.value)}
              disabled={searchLoading}
            >
              {PAGE_SIZE_OPTIONS.map(size => (
                <option key={size} value={size}>{size}</option>
              ))}
            </select>
          </label>
        </div>

        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'center', marginTop: 12, flexWrap: 'wrap' }}>
          <div style={mutedStyle}>
            {searchLoading ? '正在检索影像...' : `第 ${sceneStart}-${sceneEnd} 景 / 共 ${scenePage.total} 景`}
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button
              type="button"
              style={{ ...buttonStyle, ...(busy ? disabledButtonStyle : {}) }}
              onClick={applySearch}
              disabled={busy}
            >
              检索
            </button>
            <button
              type="button"
              style={{ ...ghostButtonStyle, ...(busy ? disabledButtonStyle : {}) }}
              onClick={resetSearch}
              disabled={busy}
            >
              重置
            </button>
            <button
              type="button"
              style={{ ...ghostButtonStyle, ...((readOnly || searchLoading || selectableCurrentScenes.length === 0) ? disabledButtonStyle : {}) }}
              onClick={toggleCurrentPageSelection}
              disabled={readOnly || searchLoading || selectableCurrentScenes.length === 0}
            >
              {allCurrentSelectableSelected ? '取消本页选择' : '选择本页可生产'}
            </button>
            <button
              type="button"
              style={{ ...ghostButtonStyle, ...((searchLoading || scenePage.offset <= 0) ? disabledButtonStyle : {}) }}
              onClick={() => goToScenePage(scenePage.offset - scenePage.limit)}
              disabled={searchLoading || scenePage.offset <= 0}
            >
              上一页
            </button>
            <button
              type="button"
              style={{ ...ghostButtonStyle, ...((searchLoading || !scenePage.hasMore) ? disabledButtonStyle : {}) }}
              onClick={() => goToScenePage(scenePage.offset + scenePage.limit)}
              disabled={searchLoading || !scenePage.hasMore}
            >
              下一页
            </button>
          </div>
        </div>

        <div style={{ overflowX: 'auto', marginTop: 10 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr style={{ color: '#475569', background: '#f8fafc' }}>
                <th style={tableHeaderStyle}>选择</th>
                <th style={tableHeaderStyle}>产品</th>
                <th style={tableHeaderStyle}>日期</th>
                <th style={tableHeaderStyle}>模式</th>
                <th style={tableHeaderStyle}>轨道</th>
                <th style={tableHeaderStyle}>极化</th>
                <th style={tableHeaderStyle}>状态</th>
                <th style={tableHeaderStyle}>路径</th>
              </tr>
            </thead>
            <tbody>
              {scenes.map(scene => {
                const produced = sceneProduced(scene);
                const selectable = !produced && Boolean(scene.source_product_ref_id);
                const selected = selectedRadarIds.has(scene.id);
                return (
                  <tr key={scene.id} style={{ background: selected ? '#eff6ff' : '#ffffff', opacity: produced ? 0.62 : 1 }}>
                    <td style={tableCellStyle}>
                      <input
                        type="checkbox"
                        checked={selected}
                        disabled={readOnly || actionLoading || !selectable}
                        onChange={() => toggleScene(scene)}
                      />
                    </td>
                    <td style={{ ...tableCellStyle, color: '#0f172a', fontWeight: 650 }}>
                      {getSceneTitle(scene)}
                    </td>
                    <td style={{ ...tableCellStyle, color: '#475569' }}>{formatYmd(scene.imaging_date)}</td>
                    <td style={{ ...tableCellStyle, color: '#475569' }}>{scene.imaging_mode || '-'}</td>
                    <td style={{ ...tableCellStyle, color: '#475569' }}>{scene.relative_orbit || scene.orbit_circle || '-'}</td>
                    <td style={{ ...tableCellStyle, color: '#475569' }}>{scene.polarization || '-'}</td>
                    <td style={{ ...tableCellStyle, color: produced ? '#166534' : '#475569', fontWeight: produced ? 700 : 500 }}>
                      {produced ? '已生产 GeoTIFF' : (scene.source_product_ref_id ? '可生产' : '未关联源资产')}
                    </td>
                    <td
                      title={scene.file_path}
                      style={{
                        ...tableCellStyle,
                        color: '#475569',
                        maxWidth: 360,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {scene.file_path}
                    </td>
                  </tr>
                );
              })}
              {scenes.length === 0 && (
                <tr>
                  <td colSpan="8" style={{ ...tableCellStyle, color: '#64748b' }}>
                    暂无符合条件的 LT-1 影像
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {preview && (
        <section style={sectionStyle}>
          <h3 style={{ margin: 0, fontSize: 16 }}>预览结果</h3>
          <div style={{ ...gridStyle, marginTop: 10 }}>
            <div style={mutedStyle}>场景数: {preview.scene_count}</div>
            <div style={mutedStyle}>engine: {preview.engine}</div>
            <div style={mutedStyle}>profile: {preview.profile_code}</div>
          </div>
          {Array.isArray(preview.blockers) && preview.blockers.length > 0 && (
            <div style={{ marginTop: 10, display: 'grid', gap: 6 }}>
              {preview.blockers.map(item => (
                <div key={item} style={{ color: '#991b1b', fontSize: 12 }}>{item}</div>
              ))}
            </div>
          )}
          {Array.isArray(preview.warnings) && preview.warnings.length > 0 && (
            <div style={{ marginTop: 10, display: 'grid', gap: 6 }}>
              {preview.warnings.map(item => (
                <div key={item} style={{ color: '#92400e', fontSize: 12 }}>{item}</div>
              ))}
            </div>
          )}
        </section>
      )}

      <section style={sectionStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center' }}>
          <h3 style={{ margin: 0, fontSize: 16 }}>最近 LT-1 GeoTIFF 产品</h3>
          <div style={mutedStyle}>{products.length} 项</div>
        </div>
        <div style={{ overflowX: 'auto', marginTop: 10 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr style={{ color: '#475569', background: '#f8fafc' }}>
                <th style={tableHeaderStyle}>产品</th>
                <th style={tableHeaderStyle}>状态</th>
                <th style={tableHeaderStyle}>日期</th>
                <th style={tableHeaderStyle}>单位</th>
                <th style={tableHeaderStyle}>时间</th>
                <th style={tableHeaderStyle}>GeoTIFF</th>
              </tr>
            </thead>
            <tbody>
              {products.map(product => (
                <tr key={product.id}>
                  <td style={{ ...tableCellStyle, color: '#0f172a', fontWeight: 650 }}>
                    {product.display_name || product.product_id}
                  </td>
                  <td style={{ ...tableCellStyle, color: '#475569' }}>{product.status}</td>
                  <td style={{ ...tableCellStyle, color: '#475569' }}>{formatYmd(product.summary?.imaging_date)}</td>
                  <td style={{ ...tableCellStyle, color: '#475569' }}>{product.summary?.backscatter_unit || '-'}</td>
                  <td style={{ ...tableCellStyle, color: '#475569' }}>{formatTime(product.published_at)}</td>
                  <td
                    title={product.primary_asset_path}
                    style={{
                      ...tableCellStyle,
                      color: '#475569',
                      maxWidth: 360,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {product.primary_asset_path || product.publish_dir || '-'}
                  </td>
                </tr>
              ))}
              {products.length === 0 && (
                <tr>
                  <td colSpan="6" style={{ ...tableCellStyle, color: '#64748b' }}>
                    暂无产品
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
