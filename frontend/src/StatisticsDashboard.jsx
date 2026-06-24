import { useEffect, useMemo, useRef, useState } from 'react';
import {
  BarChart,
  HeatmapChart,
  LineChart,
  MapChart,
  ScatterChart,
} from 'echarts/charts';
import {
  GeoComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  VisualMapComponent,
} from 'echarts/components';
import * as echarts from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';

import { getStatisticsDashboard } from './api/stats';

echarts.use([
  BarChart,
  HeatmapChart,
  LineChart,
  MapChart,
  ScatterChart,
  GeoComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  VisualMapComponent,
  CanvasRenderer,
]);

const CITY_COVERAGE_MAP_NAME = 'insar-city-coverage';

const FAMILY_COLORS = {
  'LT-1': '#2563eb',
  'Sentinel-1': '#0f766e',
  GF3: '#d97706',
  未分类: '#64748b',
};

const STATUS_COLORS = {
  READY: '#16a34a',
  OK: '#16a34a',
  COMPLETED: '#16a34a',
  SUCCESS: '#16a34a',
  RUNNING: '#0ea5e9',
  PENDING: '#f59e0b',
  FAILED: '#dc2626',
  ERROR: '#dc2626',
  WARNING: '#f59e0b',
  WARN: '#f59e0b',
  UNKNOWN: '#64748b',
};

function formatNumber(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '-';
  return new Intl.NumberFormat('zh-CN').format(number);
}

function formatPercent(rate) {
  const number = Number(rate);
  if (!Number.isFinite(number)) return '-';
  return `${(number * 100).toFixed(1)}%`;
}

function formatDuration(seconds) {
  const number = Number(seconds);
  if (!Number.isFinite(number) || number <= 0) return '-';
  if (number < 60) return `${Math.round(number)} 秒`;
  if (number < 3600) return `${Math.round(number / 60)} 分钟`;
  return `${(number / 3600).toFixed(1)} 小时`;
}

function formatDateTime(value) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function colorForStatus(status) {
  return STATUS_COLORS[String(status || '').toUpperCase()] || '#64748b';
}

function colorForFamily(family) {
  return FAMILY_COLORS[family] || '#64748b';
}

function colorForCoverageCount(count, maxCount) {
  const value = Number(count) || 0;
  const max = Math.max(Number(maxCount) || 1, 1);
  const rate = value / max;
  if (rate >= 0.82) return '#08306b';
  if (rate >= 0.62) return '#1261b4';
  if (rate >= 0.42) return '#2f7ed8';
  if (rate >= 0.24) return '#5aa9f4';
  if (rate >= 0.1) return '#9bd3ff';
  return '#dbeeff';
}

function isFeatureCollection(features) {
  return features?.type === 'FeatureCollection'
    && Array.isArray(features.features)
    && features.features.length > 0;
}

function registerCityCoverageMap(features) {
  if (!isFeatureCollection(features)) return false;
  echarts.registerMap(CITY_COVERAGE_MAP_NAME, features);
  return true;
}

function buildCityCoverageGrid(cityCoverage, mode = 'source') {
  const payload = cityCoverage || {};
  const scope = mode === 'results' ? payload.results : payload.source;
  const features = Array.isArray(payload.features?.features) ? payload.features.features : [];
  const regions = Array.isArray(scope?.regions) ? scope.regions : [];
  if (!features.length || !regions.length) return null;
  const countByTree = new Map(regions.map((item) => [String(item.tree_id), item]));
  return {
    source_type: 'city_regions',
    total: scope.total,
    covered_count: scope.matched_count,
    cell_count: regions.length,
    max_count: scope.max_count,
    extent: {},
    features: payload.features,
    cells: regions.map((region) => ({
      tree_id: region.tree_id,
      name: region.name,
      count: region.count,
      lon: region.lon,
      lat: region.lat,
      breakdown: region.breakdown || [],
      meta: countByTree.get(String(region.tree_id)),
    })),
  };
}

function Chart({ option, className = '', emptyText = '暂无数据' }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const [renderError, setRenderError] = useState('');

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
    if (!chartRef.current) return;
    if (option) {
      try {
        chartRef.current.setOption(option, true);
        setRenderError('');
      } catch (err) {
        console.error('Statistics chart render failed', err);
        chartRef.current.clear();
        setRenderError(err?.message || 'chart render failed');
      }
    } else {
      setRenderError('');
      chartRef.current.clear();
    }
  }, [option]);

  return (
    <div className={`statistics-echart ${className}`}>
      <div ref={containerRef} className="statistics-echart-canvas" />
      {!option && <div className="statistics-chart-empty">{emptyText}</div>}
      {renderError && <div className="statistics-chart-empty statistics-chart-empty--error">图表渲染失败，请刷新或检查统计数据</div>}
    </div>
  );
}

function buildSourceOption(rows) {
  const data = Array.isArray(rows) ? rows : [];
  if (!data.length) return null;
  return {
    animation: false,
    color: data.map((item) => colorForFamily(item.family)),
    grid: { left: 36, right: 12, top: 26, bottom: 28 },
    tooltip: {
      trigger: 'axis',
      confine: true,
      axisPointer: { type: 'shadow' },
      formatter: (params) => {
        const item = params?.[0]?.data || {};
        return `${item.family}<br/>资产：${formatNumber(item.count)} 景<br/>解析可用：${formatPercent(item.ready_rate)}`;
      },
    },
    xAxis: {
      type: 'category',
      data: data.map((item) => item.family),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#cbd5e1' } },
      axisLabel: { color: '#475569', fontWeight: 700 },
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: '#edf2f7' } },
      axisLabel: { color: '#64748b' },
    },
    series: [
      {
        type: 'bar',
        barMaxWidth: 42,
        data: data.map((item) => ({
          ...item,
          value: item.count,
          itemStyle: { color: colorForFamily(item.family), borderRadius: [4, 4, 0, 0] },
        })),
      },
    ],
  };
}

function buildTrendOption(rows, familyNames) {
  const data = Array.isArray(rows) ? rows : [];
  if (!data.length) return null;
  const families = familyNames?.length
    ? familyNames
    : Array.from(new Set(data.flatMap((item) => Object.keys(item.by_family || {}))));
  return {
    animation: false,
    color: families.map(colorForFamily),
    legend: {
      top: 0,
      right: 0,
      itemWidth: 12,
      itemHeight: 8,
      textStyle: { color: '#475569', fontSize: 11 },
    },
    grid: { left: 42, right: 12, top: 34, bottom: 28 },
    tooltip: { trigger: 'axis', confine: true },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: data.map((item) => item.month),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#cbd5e1' } },
      axisLabel: { color: '#64748b' },
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: '#edf2f7' } },
      axisLabel: { color: '#64748b' },
    },
    series: families.map((family) => ({
      name: family,
      type: 'line',
      smooth: true,
      symbolSize: 5,
      lineStyle: { width: 2 },
      areaStyle: { opacity: 0.08 },
      data: data.map((item) => item.by_family?.[family] || 0),
    })),
  };
}

function buildPipelineOption(rows) {
  const data = Array.isArray(rows) ? rows : [];
  if (!data.length) return null;
  return {
    animation: false,
    grid: { left: 82, right: 38, top: 8, bottom: 10 },
    tooltip: {
      trigger: 'axis',
      confine: true,
      axisPointer: { type: 'shadow' },
      formatter: (params) => {
        const item = params?.[0]?.data || {};
        return `${item.label}<br/>数量：${formatNumber(item.value)}<br/>比例：${formatPercent(item.rate)}`;
      },
    },
    xAxis: {
      type: 'value',
      max: Math.max(...data.map((item) => Number(item.value) || 0), 1),
      splitLine: { lineStyle: { color: '#edf2f7' } },
      axisLabel: { color: '#64748b' },
    },
    yAxis: {
      type: 'category',
      inverse: true,
      data: data.map((item) => item.label),
      axisTick: { show: false },
      axisLine: { show: false },
      axisLabel: { color: '#334155', fontWeight: 700 },
    },
    series: [
      {
        type: 'bar',
        barWidth: 14,
        data: data.map((item, index) => ({
          ...item,
          itemStyle: {
            color: ['#2563eb', '#0f766e', '#0891b2', '#16a34a', '#d97706'][index % 5],
            borderRadius: [0, 5, 5, 0],
          },
        })),
        label: {
          show: true,
          position: 'right',
          color: '#475569',
          formatter: (params) => `${formatNumber(params.data.value)} / ${formatPercent(params.data.rate)}`,
        },
      },
    ],
  };
}

function buildLegacyPointCoverageGrid(points, extent) {
  const source = Array.isArray(points)
    ? points
        .map((item) => ({
          ...item,
          lon: Number(item.lon),
          lat: Number(item.lat),
        }))
        .filter((item) => Number.isFinite(item.lon) && Number.isFinite(item.lat))
    : [];
  if (!source.length) return null;

  const extentMinLon = Number(extent?.min_lon);
  const extentMaxLon = Number(extent?.max_lon);
  const extentMinLat = Number(extent?.min_lat);
  const extentMaxLat = Number(extent?.max_lat);
  const minLon = Number.isFinite(extentMinLon) ? extentMinLon : Math.min(...source.map((item) => item.lon));
  const maxLon = Number.isFinite(extentMaxLon) ? extentMaxLon : Math.max(...source.map((item) => item.lon));
  const minLat = Number.isFinite(extentMinLat) ? extentMinLat : Math.min(...source.map((item) => item.lat));
  const maxLat = Number.isFinite(extentMaxLat) ? extentMaxLat : Math.max(...source.map((item) => item.lat));
  const lonSpan = Math.max(maxLon - minLon, 0.01);
  const latSpan = Math.max(maxLat - minLat, 0.01);
  const columns = 42;
  const rows = Math.max(14, Math.min(34, Math.round((columns * latSpan) / lonSpan)));
  const cellLon = lonSpan / columns;
  const cellLat = latSpan / rows;
  const buckets = new Map();

  source.forEach((item) => {
    const col = Math.min(columns - 1, Math.max(0, Math.floor(((item.lon - minLon) / lonSpan) * columns)));
    const row = Math.min(rows - 1, Math.max(0, Math.floor(((item.lat - minLat) / latSpan) * rows)));
    const key = `${col}:${row}`;
    const family = item.family || '未分类';
    const bucket = buckets.get(key) || { col, row, count: 0, families: {}, examples: [] };
    bucket.count += 1;
    bucket.families[family] = (bucket.families[family] || 0) + 1;
    if (bucket.examples.length < 4) {
      bucket.examples.push({
        family,
        label: item.satellite || item.source_format,
        date: item.date,
      });
    }
    buckets.set(key, bucket);
  });

  const cells = Array.from(buckets.values()).map((bucket) => {
    const dominantFamily = Object.entries(bucket.families).sort((a, b) => b[1] - a[1])[0]?.[0] || '未分类';
    return {
      col: bucket.col,
      row: bucket.row,
      count: bucket.count,
      lon_min: minLon + bucket.col * cellLon,
      lon_max: minLon + (bucket.col + 1) * cellLon,
      lat_min: minLat + bucket.row * cellLat,
      lat_max: minLat + (bucket.row + 1) * cellLat,
      lon: minLon + (bucket.col + 0.5) * cellLon,
      lat: minLat + (bucket.row + 0.5) * cellLat,
      dominant_family: dominantFamily,
      families: Object.entries(bucket.families).map(([name, count]) => ({ name, count })),
      examples: bucket.examples,
    };
  });

  return {
    source_type: 'legacy_points',
    total: source.length,
    covered_count: source.length,
    cell_count: cells.length,
    max_count: Math.max(...cells.map((cell) => cell.count), 1),
    columns,
    rows,
    extent: { min_lon: minLon, min_lat: minLat, max_lon: maxLon, max_lat: maxLat },
    cells,
  };
}

function buildCoverageOption(grid, mode = 'source') {
  if (grid?.source_type === 'city_regions') {
    if (!registerCityCoverageMap(grid.features)) return null;
    const unit = mode === 'results' ? '项成果' : '景源数据';
    const ownerLabel = mode === 'results' ? '成果类型' : '数据源';
    const maxCount = Math.max(Number(grid?.max_count) || 0, ...grid.cells.map((item) => Number(item.count) || 0), 1);
    const heatData = grid.cells
      .map((region) => {
        const lon = Number(region.lon);
        const lat = Number(region.lat);
        const count = Number(region.count) || 0;
        if (!Number.isFinite(lon) || !Number.isFinite(lat) || count <= 0) return null;
        return {
          name: region.name,
          value: [lon, lat, count],
          tree_id: region.tree_id,
          breakdown: region.breakdown || [],
        };
      })
      .filter(Boolean);
    return {
      animation: false,
      tooltip: {
        trigger: 'item',
        confine: true,
        formatter: ({ data }) => {
          const value = Array.isArray(data?.value) ? Number(data.value[2]) : Number(data?.value);
          if (!data || !Number.isFinite(value) || value <= 0) {
            return `${data?.name || '未命中行政区'}<br/>暂无${unit}`;
          }
          const breakdown = Array.isArray(data.breakdown) && data.breakdown.length
            ? `<br/>${ownerLabel}：${data.breakdown.map((item) => `${item.name} ${formatNumber(item.count)}`).join('，')}`
            : '';
          return `<b>${data.name}</b><br/>${formatNumber(value)} ${unit}${breakdown}`;
        },
      },
      visualMap: {
        show: true,
        type: 'continuous',
        min: 0,
        max: maxCount,
        orient: 'vertical',
        right: 8,
        bottom: 10,
        calculable: false,
        itemWidth: 12,
        itemHeight: 78,
        textStyle: { color: '#475569', fontSize: 11 },
        inRange: {
          color: ['#dbeafe', '#93c5fd', '#38bdf8', '#22c55e', '#facc15', '#f97316', '#dc2626'],
        },
      },
      geo: {
        map: CITY_COVERAGE_MAP_NAME,
        roam: false,
        silent: true,
        layoutCenter: ['50%', '50%'],
        layoutSize: '96%',
        itemStyle: {
          areaColor: '#f8fafc',
          borderColor: '#cbd5e1',
          borderWidth: 0.8,
        },
        emphasis: {
          disabled: true,
        },
      },
      series: [
        {
          name: mode === 'results' ? '成果热度' : '源数据热度',
          type: 'heatmap',
          coordinateSystem: 'geo',
          pointSize: mode === 'results' ? 34 : 28,
          blurSize: mode === 'results' ? 42 : 36,
          minOpacity: 0.18,
          maxOpacity: 0.92,
          data: heatData,
        },
        {
          name: mode === 'results' ? '成果命中市' : '源数据命中市',
          type: 'scatter',
          coordinateSystem: 'geo',
          symbolSize: (value) => {
            const count = Number(value?.[2]) || 0;
            return Math.max(5, Math.min(15, 5 + (count / maxCount) * 10));
          },
          itemStyle: {
            color: 'rgba(15, 23, 42, 0.72)',
            borderColor: '#ffffff',
            borderWidth: 1,
          },
          label: {
            show: true,
            position: 'right',
            color: '#0f172a',
            fontSize: 11,
            fontWeight: 800,
            formatter: ({ data }) => (Number(data?.value?.[2]) >= maxCount * 0.28 ? data.name : ''),
          },
          emphasis: {
            label: { show: true },
            itemStyle: { color: '#0f172a' },
          },
          data: heatData,
        },
      ],
    };
  }

  const cells = Array.isArray(grid?.cells) ? grid.cells : [];
  const extent = grid?.extent || {};
  if (!cells.length) return null;
  const minLon = Number(extent.min_lon);
  const maxLon = Number(extent.max_lon);
  const minLat = Number(extent.min_lat);
  const maxLat = Number(extent.max_lat);
  if (![minLon, maxLon, minLat, maxLat].every(Number.isFinite)) return null;
  const lonSpan = Math.max(maxLon - minLon, 0.01);
  const latSpan = Math.max(maxLat - minLat, 0.01);
  const lonPadding = lonSpan * 0.04;
  const latPadding = latSpan * 0.04;
  const maxCount = Math.max(Number(grid?.max_count) || 0, ...cells.map((item) => Number(item.count) || 0), 1);
  const unit = mode === 'results' ? '项成果' : '景源数据';
  const ownerLabel = mode === 'results' ? '主成果类型' : '主数据源';
  const data = cells.map((cell) => ({
    value: [
      Number(cell.lon_min),
      Number(cell.lat_min),
      Number(cell.lon_max),
      Number(cell.lat_max),
      Number(cell.count) || 0,
    ],
    meta: cell,
  }));

  return {
    animation: false,
    grid: { left: 8, right: 8, top: 8, bottom: 8 },
    tooltip: {
      trigger: 'item',
      confine: true,
      formatter: ({ data: item }) => {
        const meta = item?.meta;
        if (!meta) return '';
        const owner = mode === 'results' ? meta.dominant_catalog : meta.dominant_family;
        return [
          `<b>${formatNumber(meta.count)} ${unit}</b>`,
          owner ? `${ownerLabel}：${owner}` : '',
          `经度：${Number(meta.lon_min).toFixed(3)} - ${Number(meta.lon_max).toFixed(3)}`,
          `纬度：${Number(meta.lat_min).toFixed(3)} - ${Number(meta.lat_max).toFixed(3)}`,
          meta.examples.length
            ? `样例：${meta.examples.map((example) => `${example.family || example.catalog || ''} ${example.date || ''}`.trim()).filter(Boolean).join('，')}`
            : '',
        ].filter(Boolean).join('<br/>');
      },
    },
    xAxis: {
      type: 'value',
      min: minLon - lonPadding,
      max: maxLon + lonPadding,
      show: false,
    },
    yAxis: {
      type: 'value',
      min: minLat - latPadding,
      max: maxLat + latPadding,
      show: false,
      scale: true,
    },
    series: [
      {
        name: '覆盖密度',
        type: 'custom',
        renderItem: (params, api) => {
          const lonMin = api.value(0);
          const latMin = api.value(1);
          const lonMax = api.value(2);
          const latMax = api.value(3);
          const count = api.value(4);
          const topLeft = api.coord([lonMin, latMax]);
          const bottomRight = api.coord([lonMax, latMin]);
          const rect = echarts.graphic.clipRectByRect(
            {
              x: topLeft[0],
              y: topLeft[1],
              width: Math.max(bottomRight[0] - topLeft[0], 1.5),
              height: Math.max(bottomRight[1] - topLeft[1], 1.5),
            },
            {
              x: params.coordSys.x,
              y: params.coordSys.y,
              width: params.coordSys.width,
              height: params.coordSys.height,
            },
          );
          if (!rect) return null;
          return {
            type: 'rect',
            shape: rect,
            style: {
              fill: colorForCoverageCount(count, maxCount),
              stroke: 'rgba(255,255,255,0.72)',
              lineWidth: 0.6,
            },
            emphasis: {
              style: {
                stroke: '#0f172a',
                lineWidth: 1.2,
              },
            },
          };
        },
        data,
      },
    ],
  };
}

function buildStatusOption(rows, key = 'status') {
  const data = Array.isArray(rows) ? rows : [];
  if (!data.length) return null;
  return {
    animation: false,
    grid: { left: 80, right: 26, top: 8, bottom: 12 },
    tooltip: { trigger: 'axis', confine: true, axisPointer: { type: 'shadow' } },
    xAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: '#edf2f7' } },
      axisLabel: { color: '#64748b' },
    },
    yAxis: {
      type: 'category',
      inverse: true,
      data: data.map((item) => item[key]),
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: '#334155', fontWeight: 700 },
    },
    series: [
      {
        type: 'bar',
        barWidth: 14,
        data: data.map((item) => ({
          value: item.count,
          itemStyle: { color: colorForStatus(item[key]), borderRadius: [0, 5, 5, 0] },
        })),
        label: { show: true, position: 'right', color: '#475569' },
      },
    ],
  };
}

function buildResultTrendOption(rows) {
  const data = Array.isArray(rows) ? rows : [];
  if (!data.length) return null;
  const catalogs = Array.from(new Set(data.flatMap((item) => Object.keys(item.by_catalog || {}))));
  const colors = ['#2563eb', '#16a34a', '#d97706', '#7c3aed'];
  return {
    animation: false,
    color: colors,
    legend: {
      top: 0,
      right: 0,
      itemWidth: 12,
      itemHeight: 8,
      textStyle: { color: '#475569', fontSize: 11 },
    },
    grid: { left: 42, right: 12, top: 34, bottom: 28 },
    tooltip: { trigger: 'axis', confine: true },
    xAxis: {
      type: 'category',
      data: data.map((item) => item.month),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#cbd5e1' } },
      axisLabel: { color: '#64748b' },
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: '#edf2f7' } },
      axisLabel: { color: '#64748b' },
    },
    series: catalogs.map((catalog) => ({
      name: catalog === 'sbas_insar' ? 'SBAS' : catalog.toUpperCase(),
      type: 'bar',
      stack: 'result',
      barMaxWidth: 28,
      data: data.map((item) => item.by_catalog?.[catalog] || 0),
    })),
  };
}

function KpiCard({ item }) {
  return (
    <article className={`statistics-kpi-card statistics-kpi-card--${item.tone || 'primary'}`}>
      <div className="statistics-kpi-label">{item.label}</div>
      <div className="statistics-kpi-main">
        <strong>{formatNumber(item.value)}</strong>
        <span>{item.unit}</span>
      </div>
      <div className="statistics-kpi-note">{item.note}</div>
    </article>
  );
}

function Section({ title, subtitle, className = '', children }) {
  return (
    <section className={`statistics-section ${className}`}>
      <div className="statistics-section-header">
        <h2>{title}</h2>
        {subtitle && <span>{subtitle}</span>}
      </div>
      {children}
    </section>
  );
}

function FamilyLegend({ rows }) {
  const data = Array.isArray(rows) ? rows : [];
  return (
    <div className="statistics-family-list">
      {data.map((item) => (
        <div key={item.family} className="statistics-family-item">
          <span className="statistics-family-swatch" style={{ background: colorForFamily(item.family) }} />
          <span>{item.family}</span>
          <strong>{formatNumber(item.count)}</strong>
          <em>{formatPercent(item.ready_rate)} 可用</em>
        </div>
      ))}
    </div>
  );
}

function ProductionRunList({ rows }) {
  const data = Array.isArray(rows) ? rows : [];
  if (!data.length) {
    return <div className="statistics-empty-line">暂无生产运行记录</div>;
  }
  return (
    <div className="statistics-run-list">
      {data.map((item) => (
        <div key={item.run_id} className="statistics-run-row">
          <div>
            <strong>{item.run_id}</strong>
            <span>{item.engine_code} · {formatDateTime(item.created_at)}</span>
          </div>
          <b style={{ color: colorForStatus(item.status) }}>{item.status}</b>
          <em>{formatNumber(item.completed_items)}/{formatNumber(item.total_items)}</em>
        </div>
      ))}
    </div>
  );
}

function InventoryStateList({ rows }) {
  const data = Array.isArray(rows) ? rows : [];
  if (!data.length) {
    return <div className="statistics-empty-line">暂无扫描状态记录</div>;
  }
  return (
    <div className="statistics-inventory-list">
      {data.slice(0, 6).map((item) => (
        <div key={`${item.inventory_type}-${item.last_scan_finished_at || item.status}`} className="statistics-inventory-row">
          <div>
            <strong>{item.inventory_type || '未分类扫描'}</strong>
            <span>{formatDateTime(item.last_scan_finished_at || item.last_scan_started_at)}</span>
          </div>
          <b style={{ color: colorForStatus(item.status) }}>{item.status}</b>
          <em>{formatNumber(item.last_asset_count)} 项</em>
        </div>
      ))}
    </div>
  );
}

export default function StatisticsDashboard() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [lastLoadedAt, setLastLoadedAt] = useState(null);
  const [coverageMode, setCoverageMode] = useState('source');

  const loadDashboard = async () => {
    setLoading(true);
    setError('');
    try {
      const payload = await getStatisticsDashboard();
      setData(payload);
      setLastLoadedAt(new Date());
    } catch (err) {
      setError(err.response?.data?.detail || err.message || '统计数据获取失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadDashboard();
  }, []);

  const families = useMemo(
    () => (data?.asset?.source_by_family || []).map((item) => item.family),
    [data],
  );

  const sourceOption = useMemo(
    () => buildSourceOption(data?.asset?.source_by_family),
    [data],
  );
  const sourceTrendOption = useMemo(
    () => buildTrendOption(data?.asset?.source_by_month, families),
    [data, families],
  );
  const pipelineOption = useMemo(
    () => buildPipelineOption(data?.asset?.pipeline),
    [data],
  );
  const coverageOption = useMemo(
    () => buildCoverageOption(
      buildCityCoverageGrid(data?.coverage?.city_regions, coverageMode)
        || (coverageMode === 'results' ? data?.coverage?.results : data?.coverage?.source),
      coverageMode,
    ),
    [data, coverageMode],
  );
  const productionStatusOption = useMemo(
    () => buildStatusOption(data?.production?.run_status),
    [data],
  );
  const taskStatusOption = useMemo(
    () => buildStatusOption(data?.production?.dinsar_task_status),
    [data],
  );
  const resultTrendOption = useMemo(
    () => buildResultTrendOption(data?.results?.results_by_month),
    [data],
  );
  const issueCodeOption = useMemo(
    () => buildStatusOption(data?.issues?.by_code, 'code'),
    [data],
  );
  const activeCoverage = buildCityCoverageGrid(data?.coverage?.city_regions, coverageMode)
    || (coverageMode === 'results' ? data?.coverage?.results : data?.coverage?.source);
  const coverageSubtitle = coverageMode === 'results'
    ? `按市级行政区统计，命中 ${formatNumber(activeCoverage?.cell_count)} 个市，已定位 ${formatNumber(activeCoverage?.covered_count)} / ${formatNumber(activeCoverage?.total)} 项`
    : `按市级行政区统计，命中 ${formatNumber(activeCoverage?.cell_count)} 个市，已定位 ${formatNumber(activeCoverage?.covered_count)} / ${formatNumber(activeCoverage?.total || data?.coverage?.point_total)} 景`;

  return (
    <div className="statistics-workspace">
      <div className="statistics-content">
        <header className="statistics-hero">
          <div>
            <h1>InSAR 数据与生产统计</h1>
            <p>
              汇总源数据接入、元数据解析、精轨保障、生产运行和成果健康状态，用于判断系统工程能力和待处理风险。
            </p>
          </div>
          <div className="statistics-hero-actions">
            <span>{data?.generated_at ? `统计时间 ${formatDateTime(data.generated_at)}` : '等待统计数据'}</span>
            {data?.cache_meta?.enabled && (
              <span>
                {data.cache_meta.hit ? '缓存命中' : '已重算'}
                {data.cache_meta.ttl_seconds ? ` · ${data.cache_meta.ttl_seconds}秒缓存` : ''}
              </span>
            )}
            <button type="button" onClick={loadDashboard} disabled={loading}>
              {loading ? '刷新中' : '刷新'}
            </button>
          </div>
        </header>

        {error && (
          <div className="statistics-state statistics-state--error">
            {error}
          </div>
        )}

        {loading && !data ? (
          <div className="statistics-skeleton-grid">
            {Array.from({ length: 8 }).map((_, index) => (
              <div key={index} className="statistics-skeleton" />
            ))}
          </div>
        ) : (
          <>
            <section className="statistics-kpi-grid" aria-label="核心统计指标">
              {(data?.kpis || []).map((item) => (
                <KpiCard key={item.key} item={item} />
              ))}
            </section>

            <section className="statistics-command-grid">
              <div className="statistics-map-panel">
                <div className="statistics-map-header">
                  <div>
                    <h2>{coverageMode === 'results' ? '成果市级覆盖热力' : '源数据市级覆盖热力'}</h2>
                    <span>
                      {coverageSubtitle}
                    </span>
                  </div>
                  <div className="statistics-map-tools">
                    <div className="statistics-segmented-control" aria-label="覆盖统计对象">
                      <button
                        type="button"
                        className={coverageMode === 'source' ? 'is-active' : ''}
                        onClick={() => setCoverageMode('source')}
                      >
                        源数据
                      </button>
                      <button
                        type="button"
                        className={coverageMode === 'results' ? 'is-active' : ''}
                        onClick={() => setCoverageMode('results')}
                      >
                        成果
                      </button>
                    </div>
                    {coverageMode === 'source' && <FamilyLegend rows={data?.asset?.source_by_family} />}
                  </div>
                </div>
                <Chart option={coverageOption} className="statistics-map-chart" emptyText="暂无可用空间覆盖数据" />
              </div>

              <div className="statistics-side-stack">
                <Section title="数据底座" subtitle="按数据源统计">
                  <Chart option={sourceOption} className="statistics-chart-sm" />
                </Section>
                <Section title="生产链路" subtitle="入库到可生产">
                  <Chart option={pipelineOption} className="statistics-chart-sm" />
                </Section>
              </div>
            </section>

            <section className="statistics-dashboard-grid">
              <Section title="源数据时间分布" subtitle="按影像月份">
                <Chart option={sourceTrendOption} className="statistics-chart-md" />
              </Section>

              <Section title="精轨保障" subtitle={`${formatNumber(data?.orbit?.selected_bindings)} 景已选中`}>
                <div className="statistics-orbit-summary">
                  <div>
                    <span>精轨资产</span>
                    <strong>{formatNumber(data?.orbit?.orbit_total)}</strong>
                  </div>
                  <div>
                    <span>绑定覆盖率</span>
                    <strong>{formatPercent(data?.orbit?.selected_rate)}</strong>
                  </div>
                  <div>
                    <span>匹配记录</span>
                    <strong>{formatNumber(data?.orbit?.matched_bindings)}</strong>
                  </div>
                </div>
                <div className="statistics-mini-bars">
                  {(data?.orbit?.orbit_by_family || []).map((item) => (
                    <div key={item.family}>
                      <span>{item.family}</span>
                      <b>{formatNumber(item.count)}</b>
                      <i style={{ width: `${Math.min(100, Math.max(2, item.ok_rate * 100))}%` }} />
                    </div>
                  ))}
                </div>
              </Section>

              <Section title="D-InSAR 生产运行" subtitle={`平均耗时 ${formatDuration(data?.production?.avg_duration_seconds)}`}>
                <Chart option={productionStatusOption} className="statistics-chart-md" />
              </Section>

              <Section title="任务规划状态" subtitle={`${formatNumber(data?.production?.dinsar_task_count)} 个任务项`}>
                <Chart option={taskStatusOption} className="statistics-chart-md" />
              </Section>

              <Section title="成果产出趋势" subtitle={`${formatNumber(data?.results?.result_total)} 项成果`}>
                <Chart option={resultTrendOption} className="statistics-chart-md" />
              </Section>

              <Section title="问题闭环" subtitle={`${formatNumber(data?.issues?.open_issue_total)} 个开放问题`}>
                <Chart option={issueCodeOption} className="statistics-chart-md" emptyText="当前无开放问题" />
              </Section>
            </section>

            <section className="statistics-bottom-grid">
              <Section title="最近生产批次" subtitle="D-InSAR 运行记录">
                <ProductionRunList rows={data?.production?.recent_runs} />
              </Section>
              <Section title="最近扫描状态" subtitle="资产接入任务">
                <InventoryStateList rows={data?.inventory?.states} />
              </Section>
            </section>

            <footer className="statistics-footer-note">
              <span>页面不自动轮询，避免统计聚合影响生产服务。</span>
              {lastLoadedAt && <span>本地刷新时间 {formatDateTime(lastLoadedAt.toISOString())}</span>}
            </footer>
          </>
        )}
      </div>
    </div>
  );
}
