import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { LineChart } from 'echarts/charts';
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  ToolboxComponent,
} from 'echarts/components';
import * as echarts from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';

import {
  getSbasInsarCatalogStatus,
  getSbasInsarProductAssetUrl,
  getSbasInsarProductDetail,
  getSbasInsarProductPreviewUrl,
  listSbasInsarProducts,
  querySbasInsarPointTimeseries,
  queueSbasInsarCatalogRebuild,
} from './api/sbasInsarProducts';

echarts.use([
  LineChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  ToolboxComponent,
  MarkLineComponent,
  CanvasRenderer,
]);

const statusColors = {
  READY: '#15803d',
  WARN: '#b45309',
  REBUILDING: '#2563eb',
  INCOMPLETE: '#b45309',
  ERROR: '#dc2626',
};

const panelStyle = { display: 'grid', gap: 12 };
const sectionStyle = {
  background: '#ffffff',
  border: '1px solid #d8dee8',
  borderRadius: 8,
  padding: 14,
};
const mutedStyle = { color: '#64748b', fontSize: 12, lineHeight: 1.55 };
const buttonStyle = {
  border: '1px solid #cbd5e1',
  borderRadius: 6,
  background: '#ffffff',
  color: '#0f172a',
  cursor: 'pointer',
  fontSize: 12,
  fontWeight: 650,
  padding: '7px 11px',
};

function formatDateTime(value) {
  if (!value) return '-';
  try {
    return new Date(value).toLocaleString();
  } catch {
    return String(value);
  }
}

function formatNumber(value, digits = 4) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '-';
  return numeric.toFixed(digits);
}

function formatBytes(value) {
  const size = Number(value || 0);
  if (!Number.isFinite(size) || size <= 0) return '-';
  if (size < 1024) return `${size} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let current = size / 1024;
  let index = 0;
  while (current >= 1024 && index < units.length - 1) {
    current /= 1024;
    index += 1;
  }
  return `${current.toFixed(current >= 100 ? 0 : 1)} ${units[index]}`;
}

function normalizeBbox(bbox) {
  if (!bbox || typeof bbox !== 'object') return null;
  const minLon = Number(bbox.min_lon);
  const minLat = Number(bbox.min_lat);
  const maxLon = Number(bbox.max_lon);
  const maxLat = Number(bbox.max_lat);
  if (![minLon, minLat, maxLon, maxLat].every(Number.isFinite)) return null;
  if (minLon >= maxLon || minLat >= maxLat) return null;
  return { min_lon: minLon, min_lat: minLat, max_lon: maxLon, max_lat: maxLat };
}

function bboxCenter(bbox) {
  const normalized = normalizeBbox(bbox);
  if (!normalized) return null;
  return {
    lon: (normalized.min_lon + normalized.max_lon) / 2,
    lat: (normalized.min_lat + normalized.max_lat) / 2,
  };
}

function formatBbox(bbox) {
  const normalized = normalizeBbox(bbox);
  if (!normalized) return '-';
  return [
    formatNumber(normalized.min_lon, 5),
    formatNumber(normalized.min_lat, 5),
    formatNumber(normalized.max_lon, 5),
    formatNumber(normalized.max_lat, 5),
  ].join(', ');
}

function formatCenter(center) {
  if (!center) return '-';
  const lon = Number(center.lon);
  const lat = Number(center.lat);
  if (!Number.isFinite(lon) || !Number.isFinite(lat)) return '-';
  return `${lon.toFixed(5)}, ${lat.toFixed(5)}`;
}

function formatAdminRegion(region) {
  if (!region || typeof region !== 'object') return '-';
  return region.display_name || region.name || region.tree_id || '-';
}

function StatusBadge({ value }) {
  const color = statusColors[value] || '#64748b';
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '2px 9px',
        borderRadius: 999,
        background: `${color}16`,
        color,
        fontSize: 12,
        fontWeight: 700,
      }}
    >
      <span style={{ width: 7, height: 7, borderRadius: 999, background: color }} />
      {value || 'UNKNOWN'}
    </span>
  );
}

function Metric({ label, value, accent }) {
  return (
    <div style={{ border: '1px solid #e2e8f0', borderRadius: 8, padding: '9px 10px', background: '#f8fafc' }}>
      <div style={{ color: '#64748b', fontSize: 12 }}>{label}</div>
      <div style={{ color: accent || '#0f172a', fontSize: 15, fontWeight: 750, marginTop: 4 }}>{value}</div>
    </div>
  );
}

function findFirstAsset(assets, roles) {
  const roleSet = new Set(roles);
  return assets.find(asset => roleSet.has(asset.asset_role) && asset.exists_flag);
}

function findAssets(assets, roles) {
  const roleSet = new Set(roles);
  return assets.filter(asset => roleSet.has(asset.asset_role) && asset.exists_flag);
}

function assetCacheKey(asset) {
  if (!asset) return '';
  return [asset.id, asset.file_size, asset.updated_at || asset.created_at || asset.relative_path].filter(Boolean).join(':');
}

function productCacheKey(product) {
  if (!product) return '';
  return [
    product.id,
    product.status,
    product.health_status,
    product.updated_at || product.published_at || product.produced_at || product.manifest_fingerprint,
  ].filter(Boolean).join(':');
}

const assetRoleInfo = {
  run_manifest: {
    label: '运行清单',
    description: '记录本次 SBAS 运行的参数、状态、引擎信息和可追溯入口。',
  },
  stack_manifest: {
    label: '影像栈清单',
    description: '记录参考景、参与景、日期序列、轨道和输入栈元数据。',
  },
  workflow_summary: {
    label: '处理流程摘要',
    description: '记录生产流程、步骤状态、命令链路和关键执行信息。',
  },
  product_summary: {
    label: '产品摘要',
    description: '旧版托管产品摘要；专家 Gamma 模式下不再作为必需产物。',
  },
  quality_summary: {
    label: '质量摘要',
    description: '旧版质量统计摘要；专家 Gamma 模式下统计由 GeoTIFF 派生。',
  },
  monitor_points_summary: {
    label: '监测点摘要',
    description: '记录监测点选择、曲线产物和点位时序文件。',
  },
  monitor_point_selection_metadata: {
    label: '监测点选点策略元数据',
    description: '记录每个自动监测点的选点策略、顺序、说明和雷达坐标。',
  },
  unwrapped_phase_summary: {
    label: '解缠相位摘要',
    description: '记录 final_unw_tab 中最终解缠相位文件的地理编码导出状态。',
  },
  unwrapped_phase_geotiff: {
    label: '解缠相位 GeoTIFF',
    description: '由 Gamma SBAS 最终解缠相位文件地理编码得到的检查栅格，单位为 rad。',
  },
  unwrapped_phase_preview: {
    label: '解缠相位预览图',
    description: '解缠相位 GeoTIFF 的快速浏览 PNG，用于检查空间连续性和异常条带。',
  },
  unwrapped_phase_radar_preview: {
    label: '解缠相位雷达坐标预览图',
    description: '由 final_unw_tab 最终解缠相位在雷达坐标下渲染的彩色检查图；色表采用 Gamma rmg.cm。',
  },
  unwrapped_phase_radar_bmp: {
    label: '解缠相位雷达坐标 BMP',
    description: 'Gamma 风格的雷达坐标解缠相位原始浏览 BMP，适合专家复核。',
  },
  unwrapped_phase_radar_colorbar: {
    label: '解缠相位色卡',
    description: '解缠相位浏览色卡，Gamma rmg.cm 仅表示所用色表，显示范围为 -6.28 到 6.28 rad。',
  },
  point_vector_summary: {
    label: '速率点矢量摘要',
    description: '记录 LOS 速率点矢量的字段、数量、坐标系和导出状态。',
  },
  point_vector_geojson_gz: {
    label: '速率点矢量 GeoJSON',
    description: '用于 GIS 或前端叠加检视的压缩点矢量文件。',
  },
  primary_geocoded_preview: {
    label: 'LOS 速率预览图',
    description: '面向快速检视的地理编码速率图，专家 Gamma 模式对应 geo_los_def_rate RGB 预览。',
  },
  primary_rate_color_preview: {
    label: 'LOS 速率纯色图',
    description: '由 geo_los_def_rate.tif 按 Gamma hls.cm 直接上色的速率图，不叠加强度图或底图；0 速率按无效值透明处理。',
  },
  primary_preview: {
    label: 'LOS 结果预览图',
    description: '面向快速检视的 SBAS 结果预览图。',
  },
  quality_geocoded_preview: {
    label: 'LOS Sigma 预览图',
    description: '速度不确定性或残差质量图的预览图；专家 Gamma 当前链路未生成时不显示。',
  },
  primary_geotiff: {
    label: 'LOS 速率 GeoTIFF',
    description: '核心结果栅格，可用于 QGIS、ArcGIS、Python 和后续统计分析。',
  },
  alternate_geotiff: {
    label: 'LOS 反向约定 GeoTIFF',
    description: '旧版 away-from-radar 符号约定下的备用速率栅格。',
  },
  quality_geotiff: {
    label: 'LOS Sigma GeoTIFF',
    description: '速度不确定性或残差质量栅格。',
  },
  primary_rgb_geotiff: {
    label: 'LOS 速率 RGB GeoTIFF',
    description: '按 Gamma hls.cm 色表渲染后的 RGB 栅格，适合制图和浏览。',
  },
  quality_rgb_geotiff: {
    label: 'LOS Sigma RGB GeoTIFF',
    description: '质量或 Sigma 栅格渲染后的 RGB 浏览文件。',
  },
  primary_colorbar: {
    label: 'LOS 速率色卡',
    description: '与速率预览图一致的 Gamma hls.cm 色卡，用于解释颜色和速率范围。',
  },
  gamma_phase_rate: {
    label: 'Gamma 相位速率栅格',
    description: 'Gamma 原生相位速率中间产物，用于专家复核。',
  },
  gamma_sigma_rate: {
    label: 'Gamma Sigma 速率栅格',
    description: 'Gamma 原生 Sigma 速率中间产物，用于质量复核。',
  },
  gamma_qc_baseline_plot: {
    label: 'Gamma 基线网络图',
    description: '由 base_plot 输出的干涉网络/基线分布图，用于检查参与干涉对的时空基线关系。',
  },
  gamma_qc_mean_coherence: {
    label: 'Gamma 平均相干掩膜',
    description: '由解缠阶段使用的 mean.cc_mask.bmp，用于检查解缠有效区和低相干屏蔽范围。',
  },
  gamma_qc_unwrapped_phase: {
    label: 'Gamma 代表性解缠质控图',
    description: '从进入 final_unw_tab 的干涉对中抽取的滤波解缠相位 BMP，用于快速复核中间过程。',
  },
  height_correction: {
    label: '高程改正栅格',
    description: '高度误差改正相关产物，用于检查 DEM 或高程残差影响。',
  },
  monitor_points: {
    label: '监测点时序表',
    description: '专家命令 disp_prt_2d 输出的监测点形变时序原始表。',
  },
  monitor_point_items: {
    label: '监测点字段说明',
    description: 'disp_prt_2d 输出表的列定义和日期字段说明。',
  },
  monitor_point_selection: {
    label: '监测点选点文件',
    description: '专家链路使用的雷达坐标监测点选择文件。',
  },
  monitor_point_curve: {
    label: '监测点形变曲线',
    description: '由 disp_prt_2d 时序表派生的点位形变折线图。',
  },
  monitor_point_csv: {
    label: '监测点时序 CSV',
    description: '单个监测点的日期-形变量表格，便于复核和二次绘图。',
  },
  monitor_point_metadata: {
    label: '监测点元数据',
    description: '单个监测点的位置、高程、速率和残差信息。',
  },
  command_manifest: {
    label: '命令清单',
    description: '记录外部处理器命令、参数和执行链路。',
  },
  native_console_log: {
    label: '原生日志目录',
    description: '外部处理器的控制台日志或运行日志。',
  },
  secondary_geotiff: {
    label: '辅助 GeoTIFF',
    description: '处理器导出的辅助栅格结果。',
  },
};

function getAssetRoleInfo(asset) {
  const role = asset?.asset_role || '';
  return assetRoleInfo[role] || {
    label: asset?.asset_name || role || '未命名资产',
    description: '系统登记的补充资产，用于下载、归档或专家复核。',
  };
}

function ProductPreview({ title, asset, productId, imageMaxHeight = 300, onOpen }) {
  if (!asset) {
    return (
      <div style={{ border: '1px solid #e2e8f0', borderRadius: 8, padding: 10, background: '#f8fafc' }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: '#0f172a' }}>{title}</div>
        <div style={{ ...mutedStyle, marginTop: 6 }}>暂无预览。</div>
      </div>
    );
  }
  return (
    <div style={{ border: '1px solid #e2e8f0', borderRadius: 8, overflow: 'hidden', background: '#ffffff' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'center', padding: '8px 10px', background: '#f8fafc' }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: '#0f172a' }}>{title}</div>
        {onOpen && (
          <button type="button" onClick={onOpen} style={{ ...buttonStyle, padding: '4px 8px' }}>
            查看大图
          </button>
        )}
      </div>
      <img
        src={getSbasInsarProductAssetUrl(productId, asset.id, assetCacheKey(asset))}
        alt={title}
        onClick={onOpen}
        style={{
          display: 'block',
          width: '100%',
          maxHeight: imageMaxHeight,
          objectFit: 'contain',
          background: '#ffffff',
          cursor: onOpen ? 'zoom-in' : 'default',
        }}
      />
      <div style={{ ...mutedStyle, padding: '7px 10px', wordBreak: 'break-all' }}>{asset.relative_path}</div>
    </div>
  );
}

function ImageLightbox({ image, onClose }) {
  if (!image) return null;
  return (
    <div
      role="dialog"
      aria-modal="true"
      onMouseDown={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 2000,
        background: 'rgba(15, 23, 42, 0.76)',
        display: 'grid',
        placeItems: 'center',
        padding: 20,
      }}
    >
      <div
        onMouseDown={event => event.stopPropagation()}
        style={{
          width: 'min(1500px, 96vw)',
          maxHeight: '94vh',
          background: '#ffffff',
          borderRadius: 8,
          border: '1px solid #cbd5e1',
          display: 'grid',
          gridTemplateRows: 'auto minmax(0, 1fr) auto',
          overflow: 'hidden',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', padding: '10px 12px', background: '#f8fafc', borderBottom: '1px solid #e2e8f0' }}>
          <div style={{ fontSize: 13, fontWeight: 800, color: '#0f172a' }}>{image.title}</div>
          <button type="button" onClick={onClose} style={buttonStyle}>关闭</button>
        </div>
        <div style={{ overflow: 'auto', background: '#ffffff', padding: 12 }}>
          <img
            src={image.src}
            alt={image.title}
            style={{ display: 'block', width: '100%', height: 'auto', objectFit: 'contain', background: '#ffffff' }}
          />
        </div>
        <div style={{ ...mutedStyle, padding: '8px 12px', borderTop: '1px solid #e2e8f0', wordBreak: 'break-all' }}>
          {image.path || '-'}
        </div>
      </div>
    </div>
  );
}

function AssetActionLink({ label, asset, productId }) {
  if (!asset?.exists_flag) return null;
  return (
    <a
      href={getSbasInsarProductAssetUrl(productId, asset.id, assetCacheKey(asset))}
      target="_blank"
      rel="noreferrer"
      style={{ ...buttonStyle, textDecoration: 'none', display: 'inline-flex', justifyContent: 'center' }}
    >
      {label}
    </a>
  );
}

function VelocityInspectionPanel({
  asset,
  colorbarAsset,
  colorPolicy,
  primaryGeotiff,
  rgbGeotiff,
  productId,
  onOpen,
}) {
  const range = Array.isArray(colorPolicy?.display_range_mm_per_year) ? colorPolicy.display_range_mm_per_year : null;
  const rangeText = range && range.length >= 2 ? `${formatNumber(range[0], 0)} 到 ${formatNumber(range[1], 0)} mm/yr` : '-';
  const sourceText = colorPolicy?.source || 'expert_gamma_command';
  const browseText = colorPolicy?.browse_command || 'rasdt_pwr / geocode_back / data2geotiff';

  if (!asset) {
    return (
      <div style={{ border: '1px solid #e2e8f0', borderRadius: 8, padding: 12, background: '#f8fafc' }}>
        <div style={{ fontSize: 13, fontWeight: 800, color: '#0f172a' }}>LOS 速率检视</div>
        <div style={{ ...mutedStyle, marginTop: 6 }}>暂无预览。</div>
      </div>
    );
  }

  return (
    <div style={{ border: '1px solid #cbd5e1', borderRadius: 8, overflow: 'hidden', background: '#ffffff' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start', padding: '12px 14px', background: '#f8fafc', borderBottom: '1px solid #e2e8f0' }}>
        <div>
          <div style={{ fontSize: 15, fontWeight: 850, color: '#0f172a' }}>LOS 速率检视</div>
          <div style={{ ...mutedStyle, marginTop: 4 }}>Gamma expert chain: {browseText}</div>
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, justifyContent: 'flex-end' }}>
          <button type="button" onClick={onOpen} style={buttonStyle}>查看大图</button>
          <AssetActionLink label="打开 GeoTIFF" asset={primaryGeotiff} productId={productId} />
          <AssetActionLink label="打开 RGB" asset={rgbGeotiff} productId={productId} />
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(230px, 300px)', gap: 0 }}>
        <div style={{ minWidth: 0, background: '#ffffff', borderRight: '1px solid #e2e8f0' }}>
          <img
            src={getSbasInsarProductAssetUrl(productId, asset.id, assetCacheKey(asset))}
            alt="LOS 速率图"
            onClick={onOpen}
            style={{ display: 'block', width: '100%', maxHeight: 680, objectFit: 'contain', background: '#ffffff', cursor: 'zoom-in' }}
          />
        </div>

        <div style={{ display: 'grid', alignContent: 'start', gap: 10, padding: 12, background: '#ffffff' }}>
          <Metric label="色表" value={colorPolicy?.colormap || 'Gamma hls.cm'} />
          <Metric label="显示范围" value={rangeText} />
          <Metric label="来源" value={sourceText} />
          <Metric label="预览文件" value={formatBytes(asset.file_size)} />
          {colorbarAsset ? (
            <div>
              <div style={{ fontSize: 12, fontWeight: 800, color: '#0f172a', marginBottom: 6 }}>速率色卡</div>
              <img
                src={getSbasInsarProductAssetUrl(productId, colorbarAsset.id, assetCacheKey(colorbarAsset))}
                alt="Gamma hls.cm colorbar"
                style={{ display: 'block', width: '100%', height: 'auto', background: '#ffffff', border: '1px solid #e2e8f0', borderRadius: 6 }}
              />
            </div>
          ) : null}
        </div>
      </div>

      <div style={{ ...mutedStyle, padding: '8px 12px', borderTop: '1px solid #e2e8f0', wordBreak: 'break-all' }}>
        {asset.relative_path}
      </div>
    </div>
  );
}

function PointVectorDownload({ asset, summary, productId }) {
  if (!asset && !summary) return null;
  const fields = Array.isArray(summary?.fields) ? summary.fields : [];
  return (
    <div style={{ border: '1px solid #e2e8f0', borderRadius: 8, padding: 12, background: '#f8fafc' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'flex-start' }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 800, color: '#0f172a' }}>全量有效点 GeoJSON.gz</div>
          <div style={{ ...mutedStyle, marginTop: 4 }}>
            仅提供下载，不在前端渲染；用于 QGIS、ArcGIS、Python 或精细制图。
          </div>
        </div>
        {asset ? (
          <a href={getSbasInsarProductAssetUrl(productId, asset.id, assetCacheKey(asset))} target="_blank" rel="noreferrer" style={{ ...buttonStyle, textDecoration: 'none' }}>
            下载
          </a>
        ) : (
          <span style={{ color: '#dc2626', fontSize: 12 }}>缺失</span>
        )}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 8, marginTop: 10 }}>
        <Metric label="点数" value={summary?.feature_count ?? '-'} />
        <Metric label="文件大小" value={formatBytes(asset?.file_size || summary?.output_size_bytes)} />
        <Metric label="坐标系" value={summary?.crs || 'EPSG:4326'} />
        <Metric label="策略" value="download only" />
      </div>
      {fields.length > 0 && (
        <div style={{ ...mutedStyle, marginTop: 9, wordBreak: 'break-word' }}>
          字段：{fields.join(', ')}
        </div>
      )}
    </div>
  );
}

function GammaIntermediateQcPanel({ assets = [], productId, onOpen }) {
  const [expanded, setExpanded] = useState(false);
  const readyAssets = (Array.isArray(assets) ? assets : []).filter(asset => asset?.exists_flag);
  if (!readyAssets.length) return null;

  const baseline = readyAssets.find(asset => asset.asset_role === 'gamma_qc_baseline_plot');
  const coherence = readyAssets.find(asset => asset.asset_role === 'gamma_qc_mean_coherence');
  const unwrapped = readyAssets.filter(asset => asset.asset_role === 'gamma_qc_unwrapped_phase');
  const previewAssets = [baseline, coherence, ...unwrapped].filter(Boolean);

  return (
    <div style={{ border: '1px solid #dbe3ef', borderRadius: 8, background: '#ffffff', overflow: 'hidden' }}>
      <button
        type="button"
        onClick={() => setExpanded(value => !value)}
        style={{
          width: '100%',
          border: 0,
          background: '#f8fafc',
          cursor: 'pointer',
          padding: '10px 12px',
          display: 'flex',
          justifyContent: 'space-between',
          gap: 12,
          alignItems: 'center',
          textAlign: 'left',
        }}
      >
        <span>
          <span style={{ display: 'block', fontSize: 13, fontWeight: 850, color: '#0f172a' }}>Gamma 中间质控图</span>
          <span style={{ display: 'block', ...mutedStyle, marginTop: 3 }}>
            只展示少量专家复核入口：基线网络、平均相干掩膜和代表性解缠相位；完整中间文件仍在资产下载中保留。
          </span>
        </span>
        <span style={{ color: '#334155', fontSize: 12, fontWeight: 800, whiteSpace: 'nowrap' }}>
          {readyAssets.length} 项 / {expanded ? '收起' : '展开'}
        </span>
      </button>

      {expanded && (
        <div style={{ display: 'grid', gap: 10, padding: 12, borderTop: '1px solid #e2e8f0' }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 10 }}>
            {previewAssets.map(asset => {
              const info = getAssetRoleInfo(asset);
              const isLargeBmp = asset.format === 'BMP' || String(asset.relative_path || '').toLowerCase().endsWith('.bmp');
              return (
                <ProductPreview
                  key={asset.id}
                  title={info.label}
                  asset={asset}
                  productId={productId}
                  imageMaxHeight={isLargeBmp ? 360 : 300}
                  onOpen={() => onOpen?.(info.label, asset)}
                />
              );
            })}
          </div>
          <div style={mutedStyle}>
            这些图来自 Gamma 中间阶段，主要用于判断网络、相干和解缠是否异常；它们不是最终形变速率成果，不能替代上方 LOS 速率图和 GeoTIFF。
          </div>
        </div>
      )}
    </div>
  );
}

function monitorPointIdFromAsset(asset) {
  const text = [asset?.relative_path, asset?.asset_name].filter(Boolean).join(' ');
  const match = text.match(/expert_point_\d{3}/i);
  return match ? match[0] : '';
}

function pairIdFromUnwrappedAsset(asset) {
  const text = [asset?.relative_path, asset?.asset_name].filter(Boolean).join(' ');
  const match = text.match(/\d{8}_\d{8}/);
  return match ? match[0] : text;
}

function buildMonitorPointCards(monitorPoints, monitorOutputs, assets) {
  const cards = new Map();
  const ensure = pointId => {
    const id = String(pointId || '').trim();
    if (!id) return null;
    if (!cards.has(id)) {
      cards.set(id, { point_id: id, point: { point_id: id }, output: null, assets: {} });
    }
    return cards.get(id);
  };

  (Array.isArray(monitorPoints) ? monitorPoints : []).forEach((point, index) => {
    const fallbackId = `expert_point_${String(index + 1).padStart(3, '0')}`;
    const card = ensure(point?.point_id || fallbackId);
    if (card) card.point = { ...card.point, ...point, point_id: card.point_id };
  });

  (Array.isArray(monitorOutputs) ? monitorOutputs : []).forEach((output, index) => {
    const metadata = output?.metadata || {};
    const fallbackId = `expert_point_${String(index + 1).padStart(3, '0')}`;
    const card = ensure(output?.point_id || metadata.point_id || fallbackId);
    if (card) {
      card.output = output;
      card.point = { ...card.point, ...metadata, point_id: card.point_id };
    }
  });

  (Array.isArray(assets) ? assets : []).forEach(asset => {
    if (!asset?.exists_flag) return;
    if (!['monitor_point_curve', 'monitor_point_csv', 'monitor_point_metadata'].includes(asset.asset_role)) return;
    const card = ensure(monitorPointIdFromAsset(asset));
    if (!card) return;
    if (asset.asset_role === 'monitor_point_curve') card.assets.curve = asset;
    if (asset.asset_role === 'monitor_point_csv') card.assets.csv = asset;
    if (asset.asset_role === 'monitor_point_metadata') card.assets.metadata = asset;
  });

  return Array.from(cards.values()).sort((left, right) => {
    const leftRank = Number(left.point?.selection_rank ?? 9999);
    const rightRank = Number(right.point?.selection_rank ?? 9999);
    if (leftRank !== rightRank) return leftRank - rightRank;
    return left.point_id.localeCompare(right.point_id);
  });
}

function buildUnwrappedPhaseCards(summary, previews, geotiffs, radarPreviews = [], radarBmps = []) {
  const cards = new Map();
  const ensure = pairId => {
    const id = String(pairId || '').trim();
    if (!id) return null;
    if (!cards.has(id)) cards.set(id, { pair_id: id, summary: null, preview: null, geotiff: null, radarPreview: null, radarBmp: null });
    return cards.get(id);
  };

  (Array.isArray(summary?.products) ? summary.products : []).forEach((item, index) => {
    const card = ensure(item?.pair_id || `unwrapped_${String(index + 1).padStart(3, '0')}`);
    if (card) card.summary = item;
  });
  (Array.isArray(previews) ? previews : []).forEach(asset => {
    const card = ensure(pairIdFromUnwrappedAsset(asset));
    if (card) card.preview = asset;
  });
  (Array.isArray(geotiffs) ? geotiffs : []).forEach(asset => {
    const card = ensure(pairIdFromUnwrappedAsset(asset));
    if (card) card.geotiff = asset;
  });
  (Array.isArray(radarPreviews) ? radarPreviews : []).forEach(asset => {
    const card = ensure(pairIdFromUnwrappedAsset(asset));
    if (card) card.radarPreview = asset;
  });
  (Array.isArray(radarBmps) ? radarBmps : []).forEach(asset => {
    const card = ensure(pairIdFromUnwrappedAsset(asset));
    if (card) card.radarBmp = asset;
  });
  return Array.from(cards.values()).sort((left, right) => left.pair_id.localeCompare(right.pair_id));
}

function InlineImageAsset({ title, asset, productId, imageMaxHeight = 260, onOpen }) {
  if (!asset?.exists_flag) {
    return (
      <div style={{ border: '1px dashed #cbd5e1', borderRadius: 6, padding: 10, background: '#f8fafc' }}>
        <div style={{ fontSize: 12, fontWeight: 800, color: '#0f172a' }}>{title}</div>
        <div style={{ ...mutedStyle, marginTop: 4 }}>暂无预览。</div>
      </div>
    );
  }
  return (
    <div style={{ border: '1px solid #e2e8f0', borderRadius: 6, overflow: 'hidden', background: '#ffffff' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center', padding: '7px 9px', background: '#f8fafc' }}>
        <div style={{ fontSize: 12, fontWeight: 800, color: '#0f172a' }}>{title}</div>
        {onOpen && (
          <button type="button" onClick={onOpen} style={{ ...buttonStyle, padding: '4px 8px' }}>
            查看大图
          </button>
        )}
      </div>
      <img
        src={getSbasInsarProductAssetUrl(productId, asset.id, assetCacheKey(asset))}
        alt={title}
        onClick={onOpen}
        style={{
          display: 'block',
          width: '100%',
          maxHeight: imageMaxHeight,
          objectFit: 'contain',
          background: '#ffffff',
          cursor: onOpen ? 'zoom-in' : 'default',
        }}
      />
    </div>
  );
}

const monitorChartColors = ['#1d4ed8', '#dc2626', '#059669', '#7c3aed', '#d97706', '#0f766e'];

function normalizeMonitorDate(value) {
  const raw = String(value || '').trim();
  if (/^\d{8}$/.test(raw)) return `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6, 8)}`;
  return raw.slice(0, 10);
}

function parseMonitorDateMs(value) {
  const date = normalizeMonitorDate(value);
  const match = date.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!match) {
    const fallback = Date.parse(date);
    return Number.isFinite(fallback) ? fallback : NaN;
  }
  return Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}

function niceAxisStep(rawStep) {
  if (!Number.isFinite(rawStep) || rawStep <= 0) return 1;
  const exponent = Math.floor(Math.log10(rawStep));
  const base = rawStep / (10 ** exponent);
  let niceBase = 10;
  if (base <= 1) niceBase = 1;
  else if (base <= 2) niceBase = 2;
  else if (base <= 5) niceBase = 5;
  return niceBase * (10 ** exponent);
}

function buildLinearAxis(values, targetTicks = 6) {
  const finiteValues = values.filter(Number.isFinite);
  if (!finiteValues.length) return { min: -1, max: 1, step: 0.5, ticks: [-1, -0.5, 0, 0.5, 1] };

  const dataMin = Math.min(...finiteValues);
  const dataMax = Math.max(...finiteValues);
  if (dataMin === 0 && dataMax === 0) return { min: -1, max: 1, step: 0.5, ticks: [-1, -0.5, 0, 0.5, 1] };

  const dataRange = Math.max(dataMax - dataMin, Math.max(Math.abs(dataMin), Math.abs(dataMax)) * 0.2, 1);
  let lower = Math.min(dataMin, 0);
  let upper = Math.max(dataMax, 0);
  const padding = dataRange * 0.06;
  if (dataMin < 0) lower -= padding;
  if (dataMax > 0) upper += padding;
  if (lower === upper) {
    lower -= 1;
    upper += 1;
  }

  const step = niceAxisStep((upper - lower) / Math.max(2, targetTicks - 1));
  const min = Math.floor(lower / step) * step;
  const max = Math.ceil(upper / step) * step;
  const ticks = [];
  const count = Math.max(1, Math.round((max - min) / step));
  for (let index = 0; index <= count; index += 1) {
    const value = min + step * index;
    ticks.push(Math.abs(value) < Math.abs(step) * 1e-9 ? 0 : value);
  }
  if (!ticks.includes(0) && min < 0 && max > 0) ticks.push(0);
  ticks.sort((left, right) => left - right);
  return { min, max, step, ticks };
}

function formatAxisTick(value, step) {
  const normalized = Math.abs(value) < Math.max(Math.abs(step), 1) * 1e-9 ? 0 : value;
  const absStep = Math.abs(step);
  const digits = absStep >= 1 ? 0 : absStep >= 0.1 ? 1 : absStep >= 0.01 ? 2 : 3;
  return normalized.toFixed(digits);
}

function formatUtcDate(value) {
  const numeric = Number(value);
  const date = new Date(numeric);
  if (!Number.isFinite(numeric) || Number.isNaN(date.getTime())) return String(value ?? '-');
  const year = date.getUTCFullYear();
  const month = String(date.getUTCMonth() + 1).padStart(2, '0');
  const day = String(date.getUTCDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function normalizeMonitorDisplacements(point) {
  const rows = Array.isArray(point?.displacements) ? point.displacements : [];
  return rows
    .map(item => {
      const date = normalizeMonitorDate(item?.date);
      const time = parseMonitorDateMs(date);
      const displacement = Number(item?.displacement_mm ?? item?.displacement ?? item?.value);
      if (!date || !Number.isFinite(time) || !Number.isFinite(displacement)) return null;
      return { date, time, displacement };
    })
    .filter(Boolean)
    .sort((left, right) => left.time - right.time);
}

function EchartsTimeSeriesCanvas({ option }) {
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
    const handleResize = () => chart.resize();
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      resizeObserver?.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!chartRef.current || !option) return;
    chartRef.current.setOption(option, true);
  }, [option]);

  return <div ref={containerRef} style={{ width: '100%', minWidth: 720, height: 430 }} />;
}

function CombinedMonitorPointChart({ cards, title = '监测点合并形变曲线', subtitle, emptyText }) {
  const chart = useMemo(() => {
    const series = (Array.isArray(cards) ? cards : [])
      .map((card, index) => {
        const point = card.point || {};
        const values = normalizeMonitorDisplacements(point);
        const isQuery = Boolean(card.isQuery || point.is_query || point.matched);
        return {
          pointId: card.point_id || point.point_id || `point_${index + 1}`,
          label: point.selection_label || card.point_id,
          key: point.selection_key || '',
          rate: Number(point.deformation_rate_mm_per_year),
          matched: point.matched || null,
          isQuery,
          color: isQuery ? '#111827' : monitorChartColors[index % monitorChartColors.length],
          values,
        };
      })
      .filter(item => item.values.length > 0);

    const dateEntries = Array.from(
      new Map(
        series
          .flatMap(item => item.values)
          .map(value => [value.date, { date: value.date, time: value.time }]),
      ).values(),
    ).sort((left, right) => left.time - right.time);
    const dates = dateEntries.map(item => item.date);
    const times = dateEntries.map(item => item.time);
    const allValues = series.flatMap(item => item.values.map(value => value.displacement));
    if (!series.length || !dates.length || !allValues.length) return { series: [], dates: [], yMin: -1, yMax: 1 };
    const yAxis = buildLinearAxis(allValues, 6);
    return {
      series,
      dates,
      dateEntries,
      minTime: Math.min(...times),
      maxTime: Math.max(...times),
      yAxis,
      yMin: yAxis.min,
      yMax: yAxis.max,
    };
  }, [cards]);

  const option = useMemo(() => {
    if (!chart.series.length) return null;
    const yAxis = chart.yAxis || { min: chart.yMin, max: chart.yMax, step: 0.5 };
    const oneDay = 24 * 60 * 60 * 1000;
    const xMin = chart.minTime === chart.maxTime ? chart.minTime - oneDay : chart.minTime;
    const xMax = chart.minTime === chart.maxTime ? chart.maxTime + oneDay : chart.maxTime;

    return {
      animation: false,
      backgroundColor: '#ffffff',
      color: chart.series.map(item => item.color),
      tooltip: {
        trigger: 'axis',
        confine: true,
        axisPointer: { type: 'line', snap: true, lineStyle: { color: '#475569', width: 1 } },
        formatter: params => {
          const rows = (Array.isArray(params) ? params : [params]).filter(item => Array.isArray(item?.value));
          if (!rows.length) return '';
          const date = rows[0]?.data?.date || formatUtcDate(rows[0].value[0]);
          const body = rows
            .map(item => {
              const value = Number(item.value[1]);
              return `${item.marker}<span style="font-weight:650">${item.seriesName}</span>: ${formatNumber(value, 2)} mm`;
            })
            .join('<br/>');
          return `<div style="font-weight:750;margin-bottom:4px">${date}</div>${body}`;
        },
      },
      legend: {
        type: 'scroll',
        top: 6,
        left: 8,
        right: 100,
        itemWidth: 14,
        itemHeight: 8,
        textStyle: { color: '#334155', fontSize: 11 },
      },
      toolbox: {
        top: 26,
        right: 12,
        itemSize: 14,
        feature: {
          dataZoom: { yAxisIndex: 'none', title: { zoom: '区域缩放', back: '缩放还原' } },
          restore: { title: '还原' },
          saveAsImage: { title: '保存图片', name: title },
        },
      },
      grid: { left: 72, right: 28, top: 64, bottom: 70 },
      xAxis: {
        type: 'time',
        min: xMin,
        max: xMax,
        name: 'SAR Date',
        nameLocation: 'middle',
        nameGap: 42,
        axisLabel: {
          color: '#64748b',
          hideOverlap: true,
          formatter: value => formatUtcDate(value),
        },
        axisLine: { lineStyle: { color: '#94a3b8' } },
        splitLine: { show: true, lineStyle: { color: '#f1f5f9' } },
      },
      yAxis: {
        type: 'value',
        min: yAxis.min,
        max: yAxis.max,
        interval: yAxis.step,
        name: '累计形变 (mm)',
        nameLocation: 'middle',
        nameGap: 52,
        axisLabel: {
          color: '#64748b',
          formatter: value => formatAxisTick(Number(value), yAxis.step),
        },
        axisLine: { show: true, lineStyle: { color: '#94a3b8' } },
        splitLine: { show: true, lineStyle: { color: '#e2e8f0' } },
      },
      dataZoom: [
        { type: 'inside', xAxisIndex: 0, filterMode: 'none' },
        { type: 'slider', xAxisIndex: 0, filterMode: 'none', height: 24, bottom: 18 },
      ],
      series: chart.series.map((item, index) => ({
        name: item.label,
        type: 'line',
        data: item.values.map(value => ({
          value: [value.time, Number(value.displacement.toFixed(6))],
          date: value.date,
        })),
        showSymbol: true,
        symbol: 'circle',
        symbolSize: item.isQuery ? 8 : 6,
        smooth: false,
        connectNulls: false,
        emphasis: { focus: 'series' },
        lineStyle: { width: item.isQuery ? 3 : 2.2, color: item.color },
        itemStyle: { color: item.color, borderColor: '#ffffff', borderWidth: 1 },
        markLine: index === 0 ? {
          symbol: 'none',
          silent: true,
          data: [{ yAxis: 0, name: '0 mm' }],
          label: { formatter: '0 mm', color: '#475569' },
          lineStyle: { color: '#0f172a', opacity: 0.32, type: 'dashed', width: 1 },
        } : undefined,
      })),
    };
  }, [chart, title]);

  if (!chart.series.length) {
    return (
      <div style={{ border: '1px dashed #cbd5e1', borderRadius: 8, padding: 12, background: '#f8fafc' }}>
        <div style={{ fontSize: 13, fontWeight: 850, color: '#0f172a' }}>{title}</div>
        <div style={{ ...mutedStyle, marginTop: 5 }}>
          {emptyText || '当前摘要还没有内嵌日期-形变序列；重新注册资产后会由 `disp_prt_2d` 输出生成合并曲线。'}
        </div>
      </div>
    );
  }

  return (
    <div style={{ border: '1px solid #cbd5e1', borderRadius: 8, background: '#ffffff', overflow: 'hidden' }}>
      <div style={{ padding: '10px 12px', background: '#f8fafc', borderBottom: '1px solid #e2e8f0' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
          <div>
            <div style={{ fontSize: 14, fontWeight: 850, color: '#0f172a' }}>{title}</div>
            <div style={{ ...mutedStyle, marginTop: 4 }}>
              {subtitle || '同一次 Gamma `disp_prt_2d` 时序输出，按自动选点策略叠加显示；横坐标按实际日期间隔缩放，纵坐标为累计形变 mm。'}
            </div>
          </div>
          <div style={{ color: '#334155', fontSize: 12, fontWeight: 750 }}>
            {chart.series.length} 条曲线 / {chart.dates.length} 期
          </div>
        </div>
      </div>

      <div style={{ padding: '10px 12px 0', overflowX: 'auto' }}>
        <EchartsTimeSeriesCanvas option={option} />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))', gap: 8, padding: '8px 12px 12px' }}>
        {chart.series.map(item => (
          <div key={`legend-${item.pointId}`} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', minWidth: 0 }}>
            <span style={{ width: 10, height: 10, borderRadius: 999, background: item.color, marginTop: 4, flex: '0 0 auto' }} />
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: 12, fontWeight: 800, color: '#0f172a', overflowWrap: 'anywhere' }}>{item.label}</div>
              <div style={{ ...mutedStyle, marginTop: 1 }}>
                {item.pointId}{item.key ? ` / ${item.key}` : ''}，速率 {Number.isFinite(item.rate) ? `${formatNumber(item.rate, 2)} mm/yr` : '-'}
                {item.matched?.used_nearest ? `，最近邻 ${formatNumber(item.matched.distance_m, 1)} m` : ''}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function PointTimeseriesLookup({ productId, result, onResult, onClear }) {
  const [lon, setLon] = useState('');
  const [lat, setLat] = useState('');
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');

  const queryPoint = async () => {
    const numericLon = Number(lon);
    const numericLat = Number(lat);
    if (!Number.isFinite(numericLon) || !Number.isFinite(numericLat)) {
      setMessage('请输入有效的 WGS84 经度和纬度。');
      return;
    }
    if (numericLon < -180 || numericLon > 180 || numericLat < -90 || numericLat > 90) {
      setMessage('经纬度超出 WGS84 范围。');
      return;
    }
    setLoading(true);
    setMessage('');
    try {
      const nextResult = await querySbasInsarPointTimeseries(productId, { lon: numericLon, lat: numericLat });
      onResult?.(nextResult);
      setMessage('查询完成，结果已加入下方 ECharts 曲线。');
    } catch (error) {
      onClear?.();
      setMessage(`查询失败：${error?.response?.data?.detail || error.message}`);
    } finally {
      setLoading(false);
    }
  };

  const matched = result?.matched || {};

  return (
    <div style={{ border: '1px solid #dbe3ef', borderRadius: 8, background: '#ffffff', overflow: 'hidden' }}>
      <div style={{ display: 'grid', gap: 10, padding: 12, background: '#f8fafc', borderBottom: '1px solid #e2e8f0' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'flex-start', flexWrap: 'wrap' }}>
          <div>
            <div style={{ fontSize: 13, fontWeight: 850, color: '#0f172a' }}>按 WGS84 经纬度查询形变曲线</div>
            <div style={{ ...mutedStyle, marginTop: 4 }}>
              输入经纬度后，系统会在有效覆盖区内取该位置或最近有效像元，并返回对应雷达像素的 SBAS 时序。
            </div>
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr)) minmax(150px, auto)', gap: 8, alignItems: 'center' }}>
          <input
            value={lon}
            onChange={event => setLon(event.target.value)}
            onKeyDown={event => {
              if (event.key === 'Enter') queryPoint();
            }}
            placeholder="经度 lon，如 129.18"
            style={{ border: '1px solid #cbd5e1', borderRadius: 6, padding: '7px 9px', fontSize: 12 }}
          />
          <input
            value={lat}
            onChange={event => setLat(event.target.value)}
            onKeyDown={event => {
              if (event.key === 'Enter') queryPoint();
            }}
            placeholder="纬度 lat，如 44.05"
            style={{ border: '1px solid #cbd5e1', borderRadius: 6, padding: '7px 9px', fontSize: 12 }}
          />
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button type="button" onClick={queryPoint} disabled={loading || !productId} style={{ ...buttonStyle, opacity: loading ? 0.65 : 1 }}>
              {loading ? '查询中...' : '查询曲线'}
            </button>
            {result && (
              <button
                type="button"
                onClick={() => {
                  onClear?.();
                  setMessage('已清除查询曲线。');
                }}
                style={buttonStyle}
              >
                清除查询
              </button>
            )}
          </div>
        </div>
        {message && <div style={{ color: message.includes('失败') || message.includes('超出') || message.includes('有效') ? '#dc2626' : '#166534', fontSize: 12 }}>{message}</div>}
      </div>

      {result && (
        <div style={{ display: 'grid', gap: 10, padding: 12 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 8 }}>
            <Metric label="匹配方式" value={matched.used_nearest ? '最近有效像元' : '输入点有效像元'} accent={matched.used_nearest ? '#b45309' : '#15803d'} />
            <Metric label="匹配经纬度" value={`${formatNumber(matched.lon, 6)}, ${formatNumber(matched.lat, 6)}`} />
            <Metric label="距离" value={`${formatNumber(matched.distance_m, 1)} m`} />
            <Metric label="雷达坐标 x,y" value={`${matched.img_x ?? '-'}, ${matched.img_y ?? '-'}`} />
            <Metric label="LOS 速率" value={`${formatNumber(matched.los_rate_mm_per_year, 2)} mm/yr`} />
          </div>
          <div style={mutedStyle}>查询点曲线已叠加到下方主图；若输入位置不是有效像元，图例会标注最近邻距离。</div>
        </div>
      )}
    </div>
  );
}

function MonitorPointInspection({ cards, productId, onOpen }) {
  const [queryResult, setQueryResult] = useState(null);

  useEffect(() => {
    setQueryResult(null);
  }, [productId]);

  const queryCard = useMemo(() => {
    if (!queryResult) return null;
    const matched = queryResult.matched || {};
    const pointId = matched.used_nearest ? 'nearest_wgs84_query' : 'wgs84_query';
    return {
      point_id: pointId,
      isQuery: true,
      point: {
        point_id: pointId,
        is_query: true,
        selection_label: matched.used_nearest ? '查询点最近邻时序' : '查询点时序',
        selection_key: `${formatNumber(matched.lon, 6)}, ${formatNumber(matched.lat, 6)}`,
        deformation_rate_mm_per_year: matched.los_rate_mm_per_year,
        matched,
        displacements: queryResult.displacements || [],
      },
      assets: {},
    };
  }, [queryResult]);

  const chartCards = useMemo(() => (
    queryCard ? [...cards, queryCard] : cards
  ), [cards, queryCard]);

  if (!cards.length) {
    return (
      <div style={{ display: 'grid', gap: 12 }}>
        <PointTimeseriesLookup productId={productId} result={queryResult} onResult={setQueryResult} onClear={() => setQueryResult(null)} />
        <CombinedMonitorPointChart
          cards={chartCards}
          title="查询点形变曲线"
          subtitle="暂无自动监测点时，仍可按 WGS84 经纬度查询有效覆盖区内或最近有效像元的 SBAS 时序。"
          emptyText="暂无监测点或查询点曲线。"
        />
      </div>
    );
  }

  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <div style={mutedStyle}>
        这些点来自同一次 Gamma `disp_prt_2d` 时序结果，只是自动选点策略不同；主视图合并展示，单点 PNG、CSV 和元数据保留为复核入口。
      </div>
      <PointTimeseriesLookup productId={productId} result={queryResult} onResult={setQueryResult} onClear={() => setQueryResult(null)} />
      <CombinedMonitorPointChart
        cards={chartCards}
        title="监测点和查询点形变曲线"
        subtitle="同一次 Gamma `disp_prt_2d` 时序输出；自动选点和 WGS84 查询点共用 ECharts 时间轴，横坐标按实际日期间隔缩放，纵坐标为累计形变 mm。"
      />
      <div style={{ border: '1px solid #e2e8f0', borderRadius: 8, overflow: 'hidden', background: '#ffffff' }}>
        <div style={{ padding: '9px 12px', background: '#f8fafc', borderBottom: '1px solid #e2e8f0', fontSize: 13, fontWeight: 850, color: '#0f172a' }}>
          监测点明细和复核文件
        </div>
        <div style={{ overflowX: 'auto' }}>
          <div style={{ minWidth: 820 }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1.7fr 1fr 1fr 0.85fr 0.8fr 1.6fr', gap: 8, padding: '8px 12px', background: '#f8fafc', borderBottom: '1px solid #e2e8f0', color: '#475569', fontSize: 12, fontWeight: 800 }}>
              <div>选点策略</div>
              <div>雷达坐标</div>
              <div>速率</div>
              <div>残差</div>
              <div>样本</div>
              <div>复核文件</div>
            </div>
        {cards.map(card => {
          const point = card.point || {};
          const curve = card.assets.curve;
          const csv = card.assets.csv;
          const metadata = card.assets.metadata;
          const rank = Number(point.selection_rank);
          const strategyLabel = point.selection_label || '未记录选点策略';
          const strategyKey = point.selection_key || '-';
          const strategyDescription = point.selection_description || '该点缺少选点策略元数据，建议重新生成监测点派生文件。';
          return (
            <div key={card.point_id} style={{ display: 'grid', gridTemplateColumns: '1.7fr 1fr 1fr 0.85fr 0.8fr 1.6fr', gap: 8, alignItems: 'center', padding: '10px 12px', borderBottom: '1px solid #edf2f7' }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ display: 'flex', gap: 7, alignItems: 'center', flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 12, fontWeight: 850, color: '#0f172a' }}>{strategyLabel}</span>
                  <span style={{ color: Number.isFinite(rank) ? '#1d4ed8' : '#64748b', fontSize: 12, fontWeight: 800 }}>
                    {Number.isFinite(rank) ? `策略 ${rank}` : '未排序'}
                  </span>
                </div>
                <div style={{ ...mutedStyle, marginTop: 2 }}>{card.point_id} / {strategyKey}</div>
                <div style={{ ...mutedStyle, marginTop: 3, overflowWrap: 'anywhere' }}>{strategyDescription}</div>
              </div>
              <div style={{ color: '#0f172a', fontSize: 12, fontWeight: 700 }}>{point.img_x ?? '-'}, {point.img_y ?? '-'}</div>
              <div style={{ color: '#0f172a', fontSize: 12, fontWeight: 700 }}>{formatNumber(point.deformation_rate_mm_per_year, 2)} mm/yr</div>
              <div style={{ color: '#0f172a', fontSize: 12, fontWeight: 700 }}>{formatNumber(point.stdev_residual_phase_rad, 3)}</div>
              <div style={{ color: '#0f172a', fontSize: 12, fontWeight: 700 }}>{point.displacement_count ?? '-'}</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {curve && (
                  <button type="button" onClick={() => onOpen?.(`${card.point_id} 形变曲线`, curve)} style={{ ...buttonStyle, padding: '5px 8px' }}>
                    单点图
                  </button>
                )}
                <AssetActionLink label="CSV" asset={csv} productId={productId} />
                <AssetActionLink label="元数据" asset={metadata} productId={productId} />
              </div>
            </div>
          );
        })}
          </div>
        </div>
      </div>
    </div>
  );
}

function UnwrappedPhasePanel({
  summary,
  previews = [],
  geotiffs = [],
  radarPreviews = [],
  radarBmps = [],
  radarColorbar,
  productId,
  onOpen,
}) {
  const [expanded, setExpanded] = useState(false);
  const cards = buildUnwrappedPhaseCards(summary, previews, geotiffs, radarPreviews, radarBmps);
  if (!cards.length && !summary?.source_count && !summary?.error) return null;
  return (
    <div style={{ border: '1px solid #e2e8f0', borderRadius: 8, background: '#ffffff', overflow: 'hidden' }}>
      <button
        type="button"
        onClick={() => setExpanded(value => !value)}
        style={{
          width: '100%',
          border: 0,
          background: '#f8fafc',
          cursor: 'pointer',
          padding: '10px 12px',
          display: 'flex',
          justifyContent: 'space-between',
          gap: 12,
          alignItems: 'center',
          textAlign: 'left',
        }}
      >
        <span>
          <span style={{ display: 'block', fontSize: 13, fontWeight: 850, color: '#0f172a' }}>最终解缠相位检查</span>
          <span style={{ display: 'block', ...mutedStyle, marginTop: 3 }}>
            来自 Gamma `final_unw_tab` 中进入 SBAS 反演的最终解缠相位文件；雷达坐标预览用于检查解缠连续性，GeoTIFF 用于 GIS 复核。
          </span>
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#334155', fontSize: 12, fontWeight: 800, whiteSpace: 'nowrap' }}>
          <StatusBadge value={summary?.ready ? 'READY' : (summary?.error ? 'ERROR' : 'INCOMPLETE')} />
          {cards.length || radarPreviews.length || geotiffs.length} 项 / {expanded ? '收起' : '展开'}
        </span>
      </button>

      {expanded && (
        <div style={{ display: 'grid', gap: 10, padding: 12, borderTop: '1px solid #e2e8f0' }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 8 }}>
            <Metric label="源文件数" value={summary?.source_count ?? geotiffs.length ?? 0} />
            <Metric label="GeoTIFF" value={geotiffs.length || cards.filter(card => card.geotiff).length || 0} />
            <Metric label="雷达坐标预览" value={radarPreviews.length || cards.filter(card => card.radarPreview).length || 0} />
            <Metric label="单位" value="rad" />
          </div>

          {radarColorbar && (
            <div>
              <div style={{ fontSize: 12, fontWeight: 800, color: '#0f172a', marginBottom: 6 }}>解缠相位色卡</div>
              <div style={{ ...mutedStyle, marginBottom: 6 }}>色表：Gamma rmg.cm；显示范围：-6.28 到 6.28 rad。</div>
              <img
                src={getSbasInsarProductAssetUrl(productId, radarColorbar.id, assetCacheKey(radarColorbar))}
                alt="Gamma rmg.cm unwrapped phase colorbar"
                style={{ display: 'block', width: 'min(620px, 100%)', height: 'auto', background: '#ffffff', border: '1px solid #e2e8f0', borderRadius: 6 }}
              />
            </div>
          )}

          {summary?.error && (
            <div style={{ color: '#dc2626', fontSize: 12, wordBreak: 'break-all' }}>{summary.error}</div>
          )}

          {cards.length > 0 && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 10 }}>
              {cards.map(card => (
                <div key={card.pair_id} style={{ border: '1px solid #dbe3ef', borderRadius: 8, background: '#ffffff', overflow: 'hidden' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, padding: '8px 10px', background: '#ffffff', borderBottom: '1px solid #e2e8f0' }}>
                    <div>
                      <div style={{ fontSize: 12, fontWeight: 850, color: '#0f172a' }}>{card.pair_id}</div>
                      <div style={mutedStyle}>有效像元：{card.summary?.valid_count ?? '-'}</div>
                    </div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, justifyContent: 'flex-end' }}>
                      <AssetActionLink label="打开 GeoTIFF" asset={card.geotiff} productId={productId} />
                      <AssetActionLink label="打开雷达坐标 BMP" asset={card.radarBmp} productId={productId} />
                    </div>
                  </div>
                  <InlineImageAsset
                    title="解缠相位雷达坐标预览图"
                    asset={card.radarPreview || card.preview}
                    productId={productId}
                    imageMaxHeight={310}
                    onOpen={(card.radarPreview || card.preview) ? () => onOpen?.(`${card.pair_id} 解缠相位`, card.radarPreview || card.preview) : undefined}
                  />
                  {card.preview && card.radarPreview && (
                    <div style={{ padding: '0 10px 10px' }}>
                      <AssetActionLink label="查看地理编码灰度预览" asset={card.preview} productId={productId} />
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function SbasInsarProductsPanel({ readOnly = false, onJobQueued }) {
  const [catalogStatus, setCatalogStatus] = useState(null);
  const [products, setProducts] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [query, setQuery] = useState('');
  const [adminRegionQuery, setAdminRegionQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [lightboxImage, setLightboxImage] = useState(null);
  const [assetsExpanded, setAssetsExpanded] = useState(false);

  const loadCatalog = useCallback(async () => {
    setLoading(true);
    try {
      const params = { limit: 100, offset: 0 };
      if (query.trim()) params.query = query.trim();
      if (adminRegionQuery.trim()) params.admin_region = adminRegionQuery.trim();
      const [statusData, productData] = await Promise.all([
        getSbasInsarCatalogStatus(),
        listSbasInsarProducts(params),
      ]);
      const nextProducts = Array.isArray(productData?.items) ? productData.items : [];
      setCatalogStatus(statusData);
      setProducts(nextProducts);
      setSelectedId(current => (current && nextProducts.some(item => item.id === current) ? current : nextProducts[0]?.id ?? null));
    } catch (error) {
      setCatalogStatus(null);
      setProducts([]);
      setSelectedId(null);
      setMessage(`SBAS 结果目录加载失败：${error?.response?.data?.detail || error.message}`);
    } finally {
      setLoading(false);
    }
  }, [adminRegionQuery, query]);

  const loadDetail = useCallback(async productId => {
    if (!productId) {
      setDetail(null);
      return;
    }
    setDetailLoading(true);
    try {
      setDetail(await getSbasInsarProductDetail(productId));
    } catch (error) {
      setDetail({ error: error?.response?.data?.detail || error.message });
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    loadCatalog();
  }, [loadCatalog]);

  useEffect(() => {
    loadDetail(selectedId);
  }, [loadDetail, selectedId]);

  useEffect(() => {
    setAssetsExpanded(false);
  }, [selectedId]);

  const handleRebuild = async () => {
    if (readOnly) return;
    setActionLoading(true);
    setMessage('');
    try {
      const result = await queueSbasInsarCatalogRebuild({ full_rebuild: true });
      setMessage(`SBAS 结果目录重建任务已提交：${result.task_id}`);
      onJobQueued?.(result.task_id);
      await loadCatalog();
    } catch (error) {
      setMessage(`SBAS 结果目录重建失败：${error?.response?.data?.detail || error.message}`);
    } finally {
      setActionLoading(false);
    }
  };

  const selectedAssets = useMemo(() => (Array.isArray(detail?.assets) ? detail.assets : []), [detail?.assets]);
  const assetSummary = useMemo(() => {
    const total = selectedAssets.length;
    const ready = selectedAssets.filter(asset => asset?.exists_flag).length;
    return {
      total,
      ready,
      missing: Math.max(0, total - ready),
      complete: total > 0 && ready === total,
    };
  }, [selectedAssets]);
  const selectedIssues = useMemo(() => (Array.isArray(detail?.issues) ? detail.issues : []), [detail?.issues]);
  const velocityPreview = useMemo(() => findFirstAsset(selectedAssets, ['primary_geocoded_preview', 'primary_preview']), [selectedAssets]);
  const velocityPureColorPreview = useMemo(() => findFirstAsset(selectedAssets, ['primary_rate_color_preview']), [selectedAssets]);
  const velocityColorbar = useMemo(() => findFirstAsset(selectedAssets, ['primary_colorbar']), [selectedAssets]);
  const primaryGeotiff = useMemo(() => findFirstAsset(selectedAssets, ['primary_geotiff']), [selectedAssets]);
  const primaryRgbGeotiff = useMemo(() => findFirstAsset(selectedAssets, ['primary_rgb_geotiff']), [selectedAssets]);
  const sigmaPreview = useMemo(() => findFirstAsset(selectedAssets, ['quality_geocoded_preview']), [selectedAssets]);
  const unwrappedPhasePreviews = useMemo(() => findAssets(selectedAssets, ['unwrapped_phase_preview']), [selectedAssets]);
  const unwrappedPhaseGeotiffs = useMemo(() => findAssets(selectedAssets, ['unwrapped_phase_geotiff']), [selectedAssets]);
  const unwrappedPhaseRadarPreviews = useMemo(() => findAssets(selectedAssets, ['unwrapped_phase_radar_preview']), [selectedAssets]);
  const unwrappedPhaseRadarBmps = useMemo(() => findAssets(selectedAssets, ['unwrapped_phase_radar_bmp']), [selectedAssets]);
  const unwrappedPhaseRadarColorbar = useMemo(() => findFirstAsset(selectedAssets, ['unwrapped_phase_radar_colorbar']), [selectedAssets]);
  const gammaIntermediateQcAssets = useMemo(
    () => findAssets(selectedAssets, ['gamma_qc_baseline_plot', 'gamma_qc_mean_coherence', 'gamma_qc_unwrapped_phase']),
    [selectedAssets],
  );
  const pointVectorAsset = useMemo(() => findFirstAsset(selectedAssets, ['point_vector_geojson_gz']), [selectedAssets]);
  const pointVectorSummary = detail?.point_vector || {};
  const monitorPoints = detail?.monitor_points?.monitor_points || detail?.geographic_coverage?.monitor_points || [];
  const monitorOutputs = detail?.monitor_points?.monitor_outputs || [];
  const monitorPointCards = useMemo(
    () => buildMonitorPointCards(monitorPoints, monitorOutputs, selectedAssets),
    [monitorPoints, monitorOutputs, selectedAssets],
  );
  const unwrappedPhaseSummary = detail?.unwrapped_phase || {};
  const coverage = detail?.geographic_coverage || {};
  const center = detail?.center || coverage.center || bboxCenter(coverage.bbox);
  const adminRegion = detail?.admin_region || coverage.admin_region;
  const quality = detail?.quality || {};
  const colorPolicy = detail?.color_policy || {};
  const rateStats = quality.los_rate_toward_mm_per_year_rdc || quality.los_rate_toward_m_per_year_rdc || quality.primary_geotiff || {};
  const sigmaStats = quality.los_sigma_mm_per_year_rdc || quality.los_sigma_m_per_year_rdc || {};
  const hasSigmaStats = Object.keys(sigmaStats || {}).length > 0;
  const catalogColor = statusColors[catalogStatus?.status] || '#64748b';
  const openAssetLightbox = useCallback((title, asset) => {
    if (!detail?.id || !asset) return;
    setLightboxImage({
      title,
      path: asset.relative_path,
      src: getSbasInsarProductAssetUrl(detail.id, asset.id, assetCacheKey(asset)),
    });
  }, [detail?.id]);

  return (
    <div style={panelStyle}>
      <ImageLightbox image={lightboxImage} onClose={() => setLightboxImage(null)} />
      <section style={sectionStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start' }}>
          <div>
            <h3 style={{ margin: 0, color: '#0f172a', fontSize: 18 }}>SBAS-InSAR 结果管理</h3>
            <div style={{ ...mutedStyle, marginTop: 5 }}>
              管理 Gamma SBAS 生产结果、重要预览图、GeoTIFF、监测点曲线和发布资产。
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button type="button" onClick={loadCatalog} disabled={loading || actionLoading} style={buttonStyle}>
              {loading ? '刷新中...' : '刷新'}
            </button>
            <button type="button" onClick={handleRebuild} disabled={readOnly || actionLoading} style={{ ...buttonStyle, opacity: readOnly ? 0.55 : 1 }}>
              {actionLoading ? '提交中...' : '重建目录'}
            </button>
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10, marginTop: 12 }}>
          <Metric label="目录状态" value={<StatusBadge value={catalogStatus?.status || 'UNKNOWN'} />} accent={catalogColor} />
          <Metric label="需要重建" value={catalogStatus?.needs_rebuild ? '是' : '否'} accent={catalogStatus?.needs_rebuild ? '#dc2626' : '#15803d'} />
          <Metric label="Run / DB" value={`${catalogStatus?.run_count ?? catalogStatus?.manifest_count ?? 0} / ${catalogStatus?.db_count ?? 0}`} />
          <Metric label="问题数" value={catalogStatus?.issue_count ?? 0} accent={(catalogStatus?.issue_count ?? 0) > 0 ? '#b45309' : '#15803d'} />
        </div>

        <div style={{ ...mutedStyle, marginTop: 10, wordBreak: 'break-all' }}>
          <div><strong>根目录：</strong>{catalogStatus?.storage_root || '-'}</div>
          <div><strong>最近消息：</strong>{catalogStatus?.last_message || '-'}</div>
          <div><strong>最近重建：</strong>{formatDateTime(catalogStatus?.last_full_rebuild_at)}</div>
        </div>
        {message && (
          <div style={{ marginTop: 10, fontSize: 12, color: message.includes('失败') ? '#dc2626' : '#166534' }}>{message}</div>
        )}
      </section>

      <section style={{ display: 'grid', gridTemplateColumns: 'minmax(280px, 380px) minmax(0, 1fr)', gap: 12, alignItems: 'start' }}>
        <div style={sectionStyle}>
          <div style={{ display: 'grid', gap: 8, marginBottom: 10 }}>
            <input
              value={query}
              onChange={event => setQuery(event.target.value)}
              onKeyDown={event => {
                if (event.key === 'Enter') loadCatalog();
              }}
              placeholder="搜索 run、stack、产品编号"
              style={{ border: '1px solid #cbd5e1', borderRadius: 6, padding: '7px 9px', fontSize: 12 }}
            />
            <div style={{ display: 'flex', gap: 8 }}>
              <input
                value={adminRegionQuery}
                onChange={event => setAdminRegionQuery(event.target.value)}
                onKeyDown={event => {
                  if (event.key === 'Enter') loadCatalog();
                }}
                placeholder="按行政区检索，如 洛阳 / 河南"
                style={{ flex: 1, border: '1px solid #cbd5e1', borderRadius: 6, padding: '7px 9px', fontSize: 12 }}
              />
              <button type="button" onClick={loadCatalog} style={buttonStyle}>查询</button>
            </div>
          </div>

          <div style={{ fontSize: 12, fontWeight: 750, color: '#0f172a', marginBottom: 8 }}>结果列表 ({products.length})</div>
          {products.length === 0 ? (
            <div style={{ ...mutedStyle, padding: '14px 0' }}>{loading ? '正在加载结果...' : '暂无已登记 SBAS 结果。'}</div>
          ) : (
            <div style={{ display: 'grid', gap: 8, maxHeight: 720, overflowY: 'auto', paddingRight: 4 }}>
              {products.map(product => {
                const active = product.id === selectedId;
                const productCenter = product.center || bboxCenter(product);
                return (
                  <button
                    key={product.id}
                    type="button"
                    onClick={() => setSelectedId(product.id)}
                    style={{
                      textAlign: 'left',
                      border: `1px solid ${active ? '#93c5fd' : '#e2e8f0'}`,
                      borderRadius: 8,
                      background: active ? '#eff6ff' : '#ffffff',
                      padding: '10px 11px',
                      cursor: 'pointer',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, marginBottom: 4 }}>
                      <strong style={{ fontSize: 12, color: '#0f172a', wordBreak: 'break-all' }}>
                        {product.display_name || product.product_id}
                      </strong>
                      <StatusBadge value={product.status} />
                    </div>
                    <div style={mutedStyle}>
                      {product.date_start || '-'} 至 {product.date_end || '-'} / {product.stack_size || product.scene_count || 0} 景 / {product.pair_count || 0} 对
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 3 }}>行政区：{formatAdminRegion(product.admin_region)}</div>
                    <div style={{ ...mutedStyle, marginTop: 3 }}>中心点：{formatCenter(productCenter)}</div>
                    <div style={{ ...mutedStyle, wordBreak: 'break-all', marginTop: 3 }}>{product.run_key || '-'}</div>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        <div style={{ display: 'grid', gap: 12 }}>
          {!selectedId ? (
            <section style={sectionStyle}>
              <div style={mutedStyle}>请选择一个 SBAS 结果。</div>
            </section>
          ) : detailLoading ? (
            <section style={sectionStyle}>
              <div style={mutedStyle}>正在加载结果详情...</div>
            </section>
          ) : detail?.error ? (
            <section style={sectionStyle}>
              <div style={{ color: '#dc2626', fontSize: 13 }}>{detail.error}</div>
            </section>
          ) : detail ? (
            <>
              <section style={sectionStyle}>
                <div style={{ display: 'grid', gridTemplateColumns: 'minmax(180px, 260px) minmax(0, 1fr)', gap: 14, alignItems: 'start' }}>
                  <div style={{ border: '1px solid #e2e8f0', borderRadius: 8, overflow: 'hidden', background: '#0f172a' }}>
                    <img
                      src={getSbasInsarProductPreviewUrl(detail.id, productCacheKey(detail))}
                      alt={detail.display_name}
                      style={{ display: 'block', width: '100%', minHeight: 150, objectFit: 'contain' }}
                    />
                  </div>
                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'flex-start' }}>
                      <div>
                        <h3 style={{ margin: 0, fontSize: 18, color: '#0f172a' }}>{detail.display_name || detail.product_id}</h3>
                        <div style={{ ...mutedStyle, marginTop: 4, wordBreak: 'break-all' }}>{detail.product_id}</div>
                      </div>
                      <StatusBadge value={detail.status} />
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 8, marginTop: 12 }}>
                      <Metric label="参考日期" value={detail.reference_date || '-'} />
                      <Metric label="时间范围" value={`${detail.date_start || '-'} 至 ${detail.date_end || '-'}`} />
                      <Metric label="景数 / 干涉对" value={`${detail.scene_count || detail.stack_size || 0} / ${detail.pair_count || 0}`} />
                      <Metric label="监测点" value={monitorPoints.length || 0} />
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 10 }}>
                      <div><strong>LOS 约定：</strong>{detail.los_sign_convention || 'toward radar positive; away from radar negative'}</div>
                      <div><strong>Run：</strong>{detail.run_id || detail.run_key || '-'}</div>
                      <div><strong>Stack：</strong>{detail.stack_key || '-'}</div>
                      <div><strong>Manifest：</strong>{detail.manifest_path || '-'}</div>
                    </div>
                  </div>
                </div>
              </section>

              <section style={sectionStyle}>
                <h4 style={{ margin: '0 0 10px', fontSize: 15, color: '#0f172a' }}>位置摘要</h4>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 8 }}>
                  <Metric label="中心点 lon, lat" value={formatCenter(center)} />
                  <Metric label="中心点行政区" value={formatAdminRegion(adminRegion)} />
                  <Metric label="影像 BBox" value={formatBbox(coverage.bbox)} />
                  <Metric label="交集 BBox" value={formatBbox(coverage.bbox_intersection)} />
                  <Metric label="单景范围数" value={(coverage.scene_footprints_geojson?.features || []).length || coverage.scene_bbox_count || 0} />
                </div>
              </section>

              <section style={sectionStyle}>
                <h4 style={{ margin: '0 0 10px', fontSize: 15, color: '#0f172a' }}>重要产物预览</h4>
                <div style={{ display: 'grid', gap: 12 }}>
                  <VelocityInspectionPanel
                    asset={velocityPreview}
                    colorbarAsset={velocityColorbar}
                    colorPolicy={colorPolicy}
                    primaryGeotiff={primaryGeotiff}
                    rgbGeotiff={primaryRgbGeotiff}
                    productId={detail.id}
                    onOpen={velocityPreview ? () => openAssetLightbox('LOS 速率图', velocityPreview) : undefined}
                  />
                  {velocityPureColorPreview && (
                    <ProductPreview
                      title="LOS 速率纯色图（无底图）"
                      asset={velocityPureColorPreview}
                      productId={detail.id}
                      imageMaxHeight={620}
                      onOpen={() => openAssetLightbox('LOS 速率纯色图（无底图）', velocityPureColorPreview)}
                    />
                  )}
                  {sigmaPreview && <ProductPreview title="LOS Sigma 图" asset={sigmaPreview} productId={detail.id} />}
                </div>
                <div style={{ marginTop: 12 }}>
                  <PointVectorDownload asset={pointVectorAsset} summary={pointVectorSummary} productId={detail.id} />
                </div>
                <div style={{ marginTop: 12 }}>
                  <UnwrappedPhasePanel
                    summary={unwrappedPhaseSummary}
                    previews={unwrappedPhasePreviews}
                    geotiffs={unwrappedPhaseGeotiffs}
                    radarPreviews={unwrappedPhaseRadarPreviews}
                    radarBmps={unwrappedPhaseRadarBmps}
                    radarColorbar={unwrappedPhaseRadarColorbar}
                    productId={detail.id}
                    onOpen={openAssetLightbox}
                  />
                </div>
                <div style={{ marginTop: 12 }}>
                  <GammaIntermediateQcPanel
                    assets={gammaIntermediateQcAssets}
                    productId={detail.id}
                    onOpen={openAssetLightbox}
                  />
                </div>
                <div style={{ marginTop: 12 }}>
                  <div style={{ fontSize: 13, fontWeight: 800, color: '#0f172a', marginBottom: 8 }}>监测点检查</div>
                  <MonitorPointInspection cards={monitorPointCards} productId={detail.id} onOpen={openAssetLightbox} />
                </div>
              </section>

              <section style={sectionStyle}>
                <h4 style={{ margin: '0 0 10px', fontSize: 15, color: '#0f172a' }}>统计摘要</h4>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 8 }}>
                  <Metric label="速率中位数" value={`${formatNumber(rateStats.median, 2)} mm/yr`} />
                  <Metric label="速率 P05 / P95" value={`${formatNumber(rateStats.p05, 2)} / ${formatNumber(rateStats.p95, 2)}`} />
                  {hasSigmaStats && <Metric label="Sigma 中位数" value={`${formatNumber(sigmaStats.median, 2)} mm/yr`} />}
                  <Metric label="有效像元" value={rateStats.valid_count ?? '-'} />
                  <Metric label="0 速率" value={rateStats.zero_is_valid ? '按稳定值参与统计' : '按无效值处理'} />
                  <Metric label="有效规则" value={rateStats.validity_rule === 'expert_rgb_coverage_finite_nonzero_values' ? '专家覆盖区内有限非零值' : (rateStats.validity_rule || '-')} />
                </div>
              </section>

              <section style={sectionStyle}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                    <h4 style={{ margin: 0, fontSize: 15, color: '#0f172a' }}>资产下载</h4>
                    <span
                      style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: 6,
                        padding: '3px 9px',
                        borderRadius: 999,
                        background: assetSummary.complete ? '#dcfce7' : '#fef3c7',
                        color: assetSummary.complete ? '#166534' : '#92400e',
                        fontSize: 12,
                        fontWeight: 850,
                      }}
                    >
                      <span style={{ width: 7, height: 7, borderRadius: 999, background: assetSummary.complete ? '#16a34a' : '#f59e0b' }} />
                      {assetSummary.complete ? '资产完备' : `缺失 ${assetSummary.missing} 项`}
                    </span>
                    <span style={{ color: '#334155', fontSize: 12, fontWeight: 800 }}>
                      {assetSummary.ready}/{assetSummary.total}
                    </span>
                    <span style={mutedStyle}>默认折叠，展开后查看全部下载文件。</span>
                  </div>
                  <button type="button" onClick={() => setAssetsExpanded(value => !value)} style={buttonStyle}>
                    {assetsExpanded ? '收起' : '展开'}
                  </button>
                </div>
                {assetsExpanded && (
                  <div style={{ display: 'grid', gap: 7, marginTop: 10 }}>
                    {selectedAssets.map(asset => {
                      const assetInfo = getAssetRoleInfo(asset);
                      return (
                        <div
                          key={asset.id}
                          style={{
                            display: 'grid',
                            gridTemplateColumns: 'minmax(220px, 300px) minmax(0, 1fr) auto',
                            gap: 12,
                            alignItems: 'center',
                            border: '1px solid #e2e8f0',
                            borderRadius: 8,
                            padding: '9px 10px',
                            background: asset.exists_flag ? '#ffffff' : '#fef2f2',
                          }}
                        >
                          <div>
                            <div style={{ fontSize: 13, fontWeight: 800, color: '#0f172a' }}>{assetInfo.label}</div>
                            <div style={{ ...mutedStyle, marginTop: 2 }}>
                              {formatBytes(asset.file_size)} / {asset.format || '-'} / {asset.exists_flag ? '已生成' : '缺失'}
                            </div>
                            <div style={{ ...mutedStyle, marginTop: 2 }}>内部角色：{asset.asset_role || '-'}</div>
                          </div>
                          <div>
                            <div style={{ color: '#334155', fontSize: 12, lineHeight: 1.55 }}>{assetInfo.description}</div>
                            <div style={{ ...mutedStyle, wordBreak: 'break-all', marginTop: 3 }}>{asset.relative_path}</div>
                          </div>
                          {asset.exists_flag ? (
                            <a href={getSbasInsarProductAssetUrl(detail.id, asset.id, assetCacheKey(asset))} target="_blank" rel="noreferrer" style={{ color: '#1d4ed8', fontSize: 12, fontWeight: 750 }}>
                              打开
                            </a>
                          ) : (
                            <span style={{ color: '#dc2626', fontSize: 12, fontWeight: 750 }}>缺失</span>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </section>

              <section style={sectionStyle}>
                <h4 style={{ margin: '0 0 10px', fontSize: 15, color: '#0f172a' }}>问题</h4>
                {selectedIssues.length === 0 ? (
                  <div style={{ color: '#15803d', fontSize: 12 }}>当前目录索引未发现问题。</div>
                ) : (
                  <div style={{ display: 'grid', gap: 7 }}>
                    {selectedIssues.map(issue => (
                      <div key={issue.id} style={{ border: '1px solid #e2e8f0', borderRadius: 8, padding: '8px 10px', fontSize: 12 }}>
                        <strong style={{ color: issue.severity === 'ERROR' ? '#dc2626' : '#b45309' }}>{issue.severity} / {issue.issue_code}</strong>
                        <div style={{ color: '#334155', marginTop: 3 }}>{issue.message}</div>
                      </div>
                    ))}
                  </div>
                )}
              </section>
            </>
          ) : null}
        </div>
      </section>
    </div>
  );
}
