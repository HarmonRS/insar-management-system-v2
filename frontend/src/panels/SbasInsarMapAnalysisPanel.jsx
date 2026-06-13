import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { LineChart } from 'echarts/charts';
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
} from 'echarts/components';
import * as echarts from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';

import {
  getSbasInsarProductAssetUrl,
  getSbasInsarProductDetail,
  listSbasInsarProducts,
  querySbasInsarPointTimeseries,
} from '../api/sbasInsarProducts';

echarts.use([
  LineChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  MarkLineComponent,
  CanvasRenderer,
]);

const panelStyle = { display: 'grid', gap: 12, padding: 16 };
const cardStyle = {
  border: '1px solid #d8dee8',
  borderRadius: 8,
  background: '#ffffff',
  overflow: 'hidden',
};
const cardBodyStyle = { display: 'grid', gap: 10, padding: 12 };
const mutedStyle = { color: '#64748b', fontSize: 12, lineHeight: 1.55 };
const labelStyle = { color: '#475569', fontSize: 12, fontWeight: 750 };
const inputStyle = {
  width: '100%',
  minWidth: 0,
  border: '1px solid #cbd5e1',
  borderRadius: 6,
  padding: '7px 9px',
  fontSize: 12,
  boxSizing: 'border-box',
};
const buttonStyle = {
  border: '1px solid #cbd5e1',
  borderRadius: 6,
  background: '#ffffff',
  color: '#0f172a',
  cursor: 'pointer',
  fontSize: 12,
  fontWeight: 700,
  padding: '7px 11px',
};
const primaryButtonStyle = {
  ...buttonStyle,
  borderColor: '#1d4ed8',
  background: '#1d4ed8',
  color: '#ffffff',
};
const chartColors = ['#1d4ed8', '#dc2626', '#059669', '#7c3aed', '#d97706', '#0f766e', '#111827'];

function formatNumber(value, digits = 2) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '-';
  return numeric.toFixed(digits);
}

function formatDate(value) {
  if (!value) return '-';
  return String(value).slice(0, 10);
}

function normalizeDate(value) {
  const text = String(value || '').trim();
  if (/^\d{8}$/.test(text)) {
    return `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`;
  }
  return text;
}

function parseDateMs(value) {
  const date = normalizeDate(value);
  if (!date) return NaN;
  const time = Date.parse(`${date}T00:00:00Z`);
  return Number.isFinite(time) ? time : NaN;
}

function normalizeDisplacements(rows) {
  return (Array.isArray(rows) ? rows : [])
    .map((item) => {
      const date = normalizeDate(item?.date);
      const time = parseDateMs(date);
      const displacement = Number(item?.displacement_mm ?? item?.displacement ?? item?.value);
      if (!date || !Number.isFinite(time) || !Number.isFinite(displacement)) return null;
      return { date, time, displacement };
    })
    .filter(Boolean)
    .sort((left, right) => left.time - right.time);
}

function buildLinearAxis(values, targetTicks = 6) {
  const finite = values.map(Number).filter(Number.isFinite);
  if (!finite.length) return { min: -1, max: 1, step: 0.5 };
  let min = Math.min(...finite, 0);
  let max = Math.max(...finite, 0);
  if (min === max) {
    const pad = Math.max(Math.abs(min) * 0.2, 1);
    min -= pad;
    max += pad;
  }
  const rawStep = (max - min) / Math.max(1, targetTicks);
  const magnitude = 10 ** Math.floor(Math.log10(rawStep));
  const normalized = rawStep / magnitude;
  const niceFactor = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
  const step = niceFactor * magnitude;
  return {
    min: Math.floor(min / step) * step,
    max: Math.ceil(max / step) * step,
    step,
  };
}

function formatAxisDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toISOString().slice(0, 10);
}

function Metric({ label, value, accent }) {
  return (
    <div style={{ border: '1px solid #e2e8f0', borderRadius: 8, padding: '8px 9px', background: '#f8fafc', minWidth: 0 }}>
      <div style={{ color: '#64748b', fontSize: 12 }}>{label}</div>
      <div style={{ color: accent || '#0f172a', fontSize: 14, fontWeight: 800, marginTop: 4, overflowWrap: 'anywhere' }}>{value}</div>
    </div>
  );
}

function StatusBadge({ value }) {
  const color = value === 'READY' ? '#15803d' : value === 'INCOMPLETE' ? '#b45309' : value === 'ERROR' ? '#dc2626' : '#64748b';
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color, fontSize: 12, fontWeight: 800 }}>
      <span style={{ width: 7, height: 7, borderRadius: 999, background: color }} />
      {value || 'UNKNOWN'}
    </span>
  );
}

function findAsset(assets, roles) {
  const roleSet = new Set(roles);
  return (Array.isArray(assets) ? assets : []).find((asset) => roleSet.has(asset.asset_role) && asset.exists_flag);
}

function assetCacheKey(asset) {
  return [asset?.id, asset?.file_size, asset?.updated_at || asset?.created_at || asset?.relative_path]
    .filter(Boolean)
    .join(':');
}

function pointCardsFromDetail(detail, queryResult) {
  const points = detail?.monitor_points?.monitor_points || [];
  const cards = points.map((point, index) => ({
    id: point.point_id || `point_${index + 1}`,
    name: point.selection_label || point.point_id || `监测点 ${index + 1}`,
    subName: point.selection_key || '',
    rate: Number(point.deformation_rate_mm_per_year),
    values: normalizeDisplacements(point.displacements),
    color: chartColors[index % chartColors.length],
    point,
  }));
  if (queryResult) {
    const matched = queryResult.matched || {};
    cards.push({
      id: matched.used_nearest ? 'query_nearest' : 'query_exact',
      name: matched.used_nearest ? '查询点最近邻' : '查询点',
      subName: `${formatNumber(matched.lon, 6)}, ${formatNumber(matched.lat, 6)}`,
      rate: Number(matched.los_rate_mm_per_year),
      values: normalizeDisplacements(queryResult.displacements),
      color: '#111827',
      point: {
        point_id: matched.used_nearest ? 'query_nearest' : 'query_exact',
        selection_label: matched.used_nearest ? '查询点最近邻' : '查询点',
        selection_key: matched.used_nearest ? `最近邻 ${formatNumber(matched.distance_m, 1)} m` : '输入点有效像元',
        deformation_rate_mm_per_year: matched.los_rate_mm_per_year,
        displacements: queryResult.displacements || [],
        matched,
        lon: matched.lon,
        lat: matched.lat,
      },
    });
  }
  return cards.filter((card) => card.values.length > 0);
}

function EchartsCanvas({ option }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current) return undefined;
    const chart = echarts.init(containerRef.current, null, { renderer: 'canvas' });
    chartRef.current = chart;
    let resizeObserver = null;
    if (typeof ResizeObserver !== 'undefined') {
      resizeObserver = new ResizeObserver(() => chart.resize());
      resizeObserver.observe(containerRef.current);
    }
    const onResize = () => chart.resize();
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      resizeObserver?.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!chartRef.current || !option) return;
    chartRef.current.setOption(option, true);
  }, [option]);

  return <div ref={containerRef} style={{ width: '100%', minWidth: 640, height: 360 }} />;
}

function TimeseriesChart({ cards }) {
  const chart = useMemo(() => {
    const validCards = (Array.isArray(cards) ? cards : []).filter((card) => card.values.length > 0);
    const allValues = validCards.flatMap((card) => card.values.map((value) => value.displacement));
    const allTimes = validCards.flatMap((card) => card.values.map((value) => value.time));
    if (!validCards.length || !allTimes.length) return null;
    return {
      cards: validCards,
      yAxis: buildLinearAxis(allValues, 6),
      minTime: Math.min(...allTimes),
      maxTime: Math.max(...allTimes),
    };
  }, [cards]);

  const option = useMemo(() => {
    if (!chart) return null;
    const oneDay = 24 * 60 * 60 * 1000;
    const xMin = chart.minTime === chart.maxTime ? chart.minTime - oneDay : chart.minTime;
    const xMax = chart.minTime === chart.maxTime ? chart.maxTime + oneDay : chart.maxTime;
    return {
      animation: false,
      backgroundColor: '#ffffff',
      color: chart.cards.map((card) => card.color),
      tooltip: {
        trigger: 'axis',
        confine: true,
        axisPointer: { type: 'line', snap: true },
        formatter: (params) => {
          const rows = (Array.isArray(params) ? params : [params]).filter((item) => Array.isArray(item?.value));
          if (!rows.length) return '';
          const date = rows[0]?.data?.date || formatAxisDate(rows[0].value[0]);
          const body = rows.map((item) => `${item.marker}<b>${item.seriesName}</b>: ${formatNumber(item.value[1], 2)} mm`).join('<br/>');
          return `<div style="font-weight:750;margin-bottom:4px">${date}</div>${body}`;
        },
      },
      legend: {
        type: 'scroll',
        top: 6,
        left: 8,
        right: 8,
        itemWidth: 14,
        itemHeight: 8,
        textStyle: { color: '#334155', fontSize: 11 },
      },
      grid: { left: 72, right: 28, top: 58, bottom: 62 },
      xAxis: {
        type: 'time',
        min: xMin,
        max: xMax,
        name: 'SAR Date',
        nameLocation: 'middle',
        nameGap: 38,
        axisLabel: { color: '#64748b', hideOverlap: true, formatter: formatAxisDate },
        splitLine: { show: true, lineStyle: { color: '#f1f5f9' } },
      },
      yAxis: {
        type: 'value',
        min: chart.yAxis.min,
        max: chart.yAxis.max,
        interval: chart.yAxis.step,
        name: '累计形变 (mm)',
        nameLocation: 'middle',
        nameGap: 50,
        axisLabel: { color: '#64748b' },
        splitLine: { show: true, lineStyle: { color: '#e2e8f0' } },
      },
      dataZoom: [
        { type: 'inside', xAxisIndex: 0, filterMode: 'none' },
        { type: 'slider', xAxisIndex: 0, filterMode: 'none', height: 22, bottom: 18 },
      ],
      series: chart.cards.map((card, index) => ({
        name: card.name,
        type: 'line',
        data: card.values.map((value) => ({ value: [value.time, Number(value.displacement.toFixed(6))], date: value.date })),
        showSymbol: true,
        symbolSize: card.id.startsWith('query') ? 8 : 6,
        smooth: false,
        connectNulls: false,
        lineStyle: { width: card.id.startsWith('query') ? 3 : 2.2, color: card.color },
        itemStyle: { color: card.color, borderColor: '#ffffff', borderWidth: 1 },
        markLine: index === 0 ? {
          symbol: 'none',
          silent: true,
          data: [{ yAxis: 0, name: '0 mm' }],
          label: { formatter: '0 mm', color: '#475569' },
          lineStyle: { color: '#0f172a', opacity: 0.32, type: 'dashed', width: 1 },
        } : undefined,
      })),
    };
  }, [chart]);

  if (!option) {
    return (
      <div style={{ border: '1px dashed #cbd5e1', borderRadius: 8, padding: 12, background: '#f8fafc', ...mutedStyle }}>
        暂无可绘制的监测点或查询点时序。
      </div>
    );
  }

  return (
    <div style={{ border: '1px solid #e2e8f0', borderRadius: 8, overflow: 'hidden', background: '#ffffff' }}>
      <div style={{ padding: '9px 12px', background: '#f8fafc', borderBottom: '1px solid #e2e8f0' }}>
        <div style={{ fontSize: 13, fontWeight: 850, color: '#0f172a' }}>监测点和查询点形变曲线</div>
        <div style={{ ...mutedStyle, marginTop: 3 }}>横坐标按真实 SAR 日期间隔缩放，纵坐标为累计形变 mm。</div>
      </div>
      <div style={{ overflowX: 'auto', padding: '8px 10px 0' }}>
        <EchartsCanvas option={option} />
      </div>
    </div>
  );
}

export default function SbasInsarMapAnalysisPanel({
  readOnly,
  onToggleRateLayer,
  onRateOpacityChange,
  onToggleMonitorPoints,
  onToggleProductOverview,
  onFlyToProduct,
  onShowQueryPoint,
  onClearLayers,
}) {
  const [products, setProducts] = useState([]);
  const [selectedId, setSelectedId] = useState('');
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [overviewVisible, setOverviewVisible] = useState(false);
  const [rateVisible, setRateVisible] = useState(false);
  const [monitorVisible, setMonitorVisible] = useState(false);
  const [opacity, setOpacity] = useState(0.78);
  const [lon, setLon] = useState('');
  const [lat, setLat] = useState('');
  const [queryLoading, setQueryLoading] = useState(false);
  const [queryResult, setQueryResult] = useState(null);

  const loadProducts = useCallback(async () => {
    setLoading(true);
    setMessage('');
    try {
      const payload = await listSbasInsarProducts({ limit: 30, offset: 0, status: 'READY' });
      const nextItems = (payload?.items || []).filter((item) => (
        String(item.engine_code || '').toLowerCase() === 'gamma'
        || String(item.processor_code || '').toLowerCase().includes('gamma')
      ));
      setProducts(nextItems);
      setSelectedId((prev) => (prev && nextItems.some((item) => String(item.id) === String(prev)) ? prev : String(nextItems[0]?.id || '')));
      if (!nextItems.length) {
        setMessage('暂无 READY 的 Gamma SBAS 产品；请先完成结果注册。');
      }
    } catch (error) {
      setMessage(`加载 SBAS 产品失败：${error?.response?.data?.detail || error.message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadProducts();
  }, [loadProducts]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return undefined;
    }
    let disposed = false;
    setDetailLoading(true);
    setMessage('');
    setOverviewVisible(false);
    setRateVisible(false);
    setMonitorVisible(false);
    setQueryResult(null);
    onClearLayers?.();
    getSbasInsarProductDetail(selectedId)
      .then((payload) => {
        if (!disposed) setDetail(payload);
      })
      .catch((error) => {
        if (!disposed) {
          setDetail(null);
          setMessage(`加载产品详情失败：${error?.response?.data?.detail || error.message}`);
        }
      })
      .finally(() => {
        if (!disposed) setDetailLoading(false);
      });
    return () => {
      disposed = true;
    };
  }, [selectedId, onClearLayers]);

  const selectedProduct = useMemo(
    () => products.find((item) => String(item.id) === String(selectedId)) || detail,
    [detail, products, selectedId],
  );

  const assets = detail?.assets || [];
  const rateAsset = useMemo(() => findAsset(assets, ['primary_geocoded_preview', 'primary_rate_color_preview']), [assets]);
  const colorbarAsset = useMemo(() => findAsset(assets, ['primary_colorbar']), [assets]);
  const monitorPoints = detail?.monitor_points?.monitor_points || [];
  const geocodedPointCount = monitorPoints.filter((point) => Number.isFinite(Number(point.lon)) && Number.isFinite(Number(point.lat))).length;
  const chartCards = useMemo(() => pointCardsFromDetail(detail, queryResult), [detail, queryResult]);
  const colorPolicy = detail?.color_policy || {};
  const range = colorPolicy?.display_range_mm_per_year || [];
  const rateRangeText = Array.isArray(range) && range.length >= 2
    ? `${formatNumber(range[0], 0)} 到 ${formatNumber(range[1], 0)} mm/yr`
    : '-80 到 80 mm/yr';

  const toggleRate = () => {
    if (!detail) return;
    const nextVisible = !rateVisible;
    const ok = onToggleRateLayer?.(detail, nextVisible, opacity);
    if (ok !== false) setRateVisible(nextVisible);
  };

  const toggleMonitor = () => {
    if (!detail) return;
    const nextVisible = !monitorVisible;
    const ok = onToggleMonitorPoints?.(detail, nextVisible);
    if (ok !== false) setMonitorVisible(nextVisible);
  };

  const toggleOverview = () => {
    const nextVisible = !overviewVisible;
    const ok = onToggleProductOverview?.(products, nextVisible);
    if (ok !== false) {
      setOverviewVisible(nextVisible);
      setMessage(nextVisible ? `已在地图显示 ${products.length} 个 SBAS 产品范围和时间。` : '已隐藏 SBAS 产品范围总览。');
    }
  };

  const changeOpacity = (event) => {
    const next = Number(event.target.value);
    setOpacity(next);
    onRateOpacityChange?.(next);
  };

  const queryPoint = async () => {
    if (!detail?.id) return;
    const numericLon = Number(lon);
    const numericLat = Number(lat);
    if (!Number.isFinite(numericLon) || !Number.isFinite(numericLat)) {
      setMessage('请输入有效 WGS84 经度和纬度。');
      return;
    }
    if (numericLon < -180 || numericLon > 180 || numericLat < -90 || numericLat > 90) {
      setMessage('经纬度超出 WGS84 范围。');
      return;
    }
    setQueryLoading(true);
    setMessage('');
    try {
      const result = await querySbasInsarPointTimeseries(detail.id, { lon: numericLon, lat: numericLat });
      setQueryResult(result);
      onShowQueryPoint?.(result, detail);
      const matched = result?.matched || {};
      setMessage(matched.used_nearest ? `已使用最近有效像元，距离 ${formatNumber(matched.distance_m, 1)} m。` : '查询点位于有效像元，曲线已生成。');
    } catch (error) {
      setQueryResult(null);
      setMessage(`查询失败：${error?.response?.data?.detail || error.message}`);
    } finally {
      setQueryLoading(false);
    }
  };

  return (
    <div style={panelStyle}>
      <div style={{ display: 'grid', gap: 4 }}>
        <div style={{ color: '#0f172a', fontSize: 16, fontWeight: 900 }}>时序InSAR地图分析</div>
        <div style={mutedStyle}>只读检视 Gamma SBAS 成果：速率图叠加、自动监测点、WGS84 点查询和形变曲线。</div>
      </div>

      <div style={cardStyle}>
        <div style={cardBodyStyle}>
          <div style={{ display: 'grid', gap: 6 }}>
            <label style={labelStyle} htmlFor="sbas-map-product">SBAS 产品</label>
            <select
              id="sbas-map-product"
              value={selectedId}
              onChange={(event) => setSelectedId(event.target.value)}
              disabled={loading || detailLoading || !products.length}
              style={inputStyle}
            >
              {products.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.display_name || item.run_key || item.id} / {formatDate(item.date_start)} → {formatDate(item.date_end)}
                </option>
              ))}
            </select>
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button type="button" onClick={loadProducts} disabled={loading} style={buttonStyle}>{loading ? '刷新中...' : '刷新产品'}</button>
            <button
              type="button"
              onClick={toggleOverview}
              disabled={!products.length}
              style={overviewVisible ? primaryButtonStyle : buttonStyle}
            >
              {overviewVisible ? '隐藏全部范围' : `查看全部范围/时间 (${products.length})`}
            </button>
            <button type="button" onClick={() => onFlyToProduct?.(detail || selectedProduct)} disabled={!selectedProduct} style={buttonStyle}>定位成果范围</button>
            <button
              type="button"
              onClick={() => {
                setOverviewVisible(false);
                setRateVisible(false);
                setMonitorVisible(false);
                setQueryResult(null);
                onClearLayers?.();
              }}
              style={buttonStyle}
            >
              清除地图图层
            </button>
          </div>
          {message && (
            <div style={{ color: message.includes('失败') || message.includes('超出') || message.includes('暂无') ? '#dc2626' : '#166534', fontSize: 12 }}>
              {message}
            </div>
          )}
        </div>
      </div>

      {detailLoading && <div className="empty-state">正在加载 SBAS 产品详情...</div>}

      {detail && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 8 }}>
            <Metric label="状态" value={<StatusBadge value={detail.status} />} accent={detail.status === 'READY' ? '#15803d' : '#b45309'} />
            <Metric label="时间范围" value={`${formatDate(detail.date_start)} → ${formatDate(detail.date_end)}`} />
            <Metric label="栈期数" value={detail.stack_size ?? detail.stack_dates?.length ?? '-'} />
            <Metric label="监测点" value={`${geocodedPointCount}/${monitorPoints.length || 0} 有经纬度`} accent={geocodedPointCount ? '#15803d' : '#b45309'} />
            <Metric label="色表范围" value={rateRangeText} />
          </div>

          <div style={cardStyle}>
            <div style={{ padding: '10px 12px', background: '#f8fafc', borderBottom: '1px solid #e2e8f0' }}>
              <div style={{ fontSize: 13, fontWeight: 850, color: '#0f172a' }}>地图图层</div>
              <div style={{ ...mutedStyle, marginTop: 3 }}>速率图使用专家链路生成的 Gamma hls.cm 浏览图；监测点使用 `disp_prt_2d` 自动选点结果。</div>
            </div>
            <div style={cardBodyStyle}>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                <button type="button" onClick={toggleRate} disabled={!rateAsset} style={rateVisible ? primaryButtonStyle : buttonStyle}>
                  {rateVisible ? '隐藏 LOS 速率图' : '显示 LOS 速率图'}
                </button>
                <button type="button" onClick={toggleMonitor} disabled={!monitorPoints.length || !geocodedPointCount} style={monitorVisible ? primaryButtonStyle : buttonStyle}>
                  {monitorVisible ? '隐藏监测点' : '显示监测点'}
                </button>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '80px 1fr 44px', gap: 8, alignItems: 'center' }}>
                <div style={labelStyle}>透明度</div>
                <input type="range" min="0.2" max="1" step="0.02" value={opacity} onChange={changeOpacity} disabled={!rateAsset} />
                <div style={{ color: '#334155', fontSize: 12, fontWeight: 800 }}>{Math.round(opacity * 100)}%</div>
              </div>
              {colorbarAsset && (
                <div style={{ display: 'grid', gap: 5 }}>
                  <div style={labelStyle}>LOS 速率色卡</div>
                  <img
                    src={getSbasInsarProductAssetUrl(detail.id, colorbarAsset.id, assetCacheKey(colorbarAsset))}
                    alt="Gamma hls.cm LOS velocity colorbar"
                    style={{ width: '100%', maxHeight: 76, objectFit: 'contain', border: '1px solid #e2e8f0', borderRadius: 6, background: '#ffffff' }}
                  />
                </div>
              )}
              {!rateAsset && <div style={mutedStyle}>未找到可叠加的 LOS 速率预览资产。</div>}
              {monitorPoints.length > 0 && geocodedPointCount === 0 && (
                <div style={{ color: '#b45309', fontSize: 12 }}>
                  当前监测点摘要没有 WGS84 经纬度；重新注册资产后可在主地图落点。
                </div>
              )}
            </div>
          </div>

          <div style={cardStyle}>
            <div style={{ padding: '10px 12px', background: '#f8fafc', borderBottom: '1px solid #e2e8f0' }}>
              <div style={{ fontSize: 13, fontWeight: 850, color: '#0f172a' }}>WGS84 点查询</div>
              <div style={{ ...mutedStyle, marginTop: 3 }}>输入覆盖区内经纬度；若不是有效像元，系统会取最近有效像元并标注距离。</div>
            </div>
            <div style={cardBodyStyle}>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr)) minmax(110px, auto)', gap: 8, alignItems: 'center' }}>
                <input
                  value={lon}
                  onChange={(event) => setLon(event.target.value)}
                  onKeyDown={(event) => { if (event.key === 'Enter') queryPoint(); }}
                  placeholder="经度 lon"
                  style={inputStyle}
                />
                <input
                  value={lat}
                  onChange={(event) => setLat(event.target.value)}
                  onKeyDown={(event) => { if (event.key === 'Enter') queryPoint(); }}
                  placeholder="纬度 lat"
                  style={inputStyle}
                />
                <button type="button" onClick={queryPoint} disabled={queryLoading || !detail?.id} style={primaryButtonStyle}>
                  {queryLoading ? '查询中...' : '查询曲线'}
                </button>
              </div>
              {queryResult && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 8 }}>
                  <Metric label="匹配方式" value={queryResult.matched?.used_nearest ? '最近有效像元' : '输入点有效像元'} accent={queryResult.matched?.used_nearest ? '#b45309' : '#15803d'} />
                  <Metric label="匹配经纬度" value={`${formatNumber(queryResult.matched?.lon, 6)}, ${formatNumber(queryResult.matched?.lat, 6)}`} />
                  <Metric label="距离" value={`${formatNumber(queryResult.matched?.distance_m, 1)} m`} />
                  <Metric label="LOS 速率" value={`${formatNumber(queryResult.matched?.los_rate_mm_per_year, 2)} mm/yr`} />
                </div>
              )}
            </div>
          </div>

          <TimeseriesChart cards={chartCards} />
        </>
      )}

      {!loading && !detailLoading && !detail && (
        <div className="empty-state">
          {readOnly ? '暂无可检视的 Gamma SBAS 产品。' : '暂无可检视的 Gamma SBAS 产品。'}
        </div>
      )}
    </div>
  );
}
