import React, { useCallback, useEffect, useMemo, useState } from 'react';

import {
  getSbasInsarCatalogStatus,
  getSbasInsarProductAssetUrl,
  getSbasInsarProductDetail,
  getSbasInsarProductPreviewUrl,
  listSbasInsarProducts,
  queueSbasInsarCatalogRebuild,
} from './api/sbasInsarProducts';

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

function ProductPreview({ title, asset, productId }) {
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
      <div style={{ padding: '8px 10px', fontSize: 12, fontWeight: 700, color: '#0f172a', background: '#f8fafc' }}>
        {title}
      </div>
      <img
        src={getSbasInsarProductAssetUrl(productId, asset.id)}
        alt={title}
        style={{ display: 'block', width: '100%', maxHeight: 300, objectFit: 'contain', background: '#0f172a' }}
      />
      <div style={{ ...mutedStyle, padding: '7px 10px', wordBreak: 'break-all' }}>{asset.relative_path}</div>
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
          <a href={getSbasInsarProductAssetUrl(productId, asset.id)} target="_blank" rel="noreferrer" style={{ ...buttonStyle, textDecoration: 'none' }}>
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

  const selectedAssets = Array.isArray(detail?.assets) ? detail.assets : [];
  const selectedIssues = Array.isArray(detail?.issues) ? detail.issues : [];
  const velocityPreview = useMemo(() => findFirstAsset(selectedAssets, ['primary_geocoded_preview']), [selectedAssets]);
  const sigmaPreview = useMemo(() => findFirstAsset(selectedAssets, ['quality_geocoded_preview']), [selectedAssets]);
  const monitorPreviews = useMemo(() => findAssets(selectedAssets, ['monitor_point_curve']), [selectedAssets]);
  const pointVectorAsset = useMemo(() => findFirstAsset(selectedAssets, ['point_vector_geojson_gz']), [selectedAssets]);
  const pointVectorSummary = detail?.point_vector || {};
  const monitorPoints = detail?.monitor_points?.monitor_points || detail?.geographic_coverage?.monitor_points || [];
  const coverage = detail?.geographic_coverage || {};
  const center = detail?.center || coverage.center || bboxCenter(coverage.bbox);
  const adminRegion = detail?.admin_region || coverage.admin_region;
  const quality = detail?.quality || {};
  const rateStats = quality.los_rate_toward_mm_per_year_rdc || quality.los_rate_toward_m_per_year_rdc || {};
  const sigmaStats = quality.los_sigma_mm_per_year_rdc || quality.los_sigma_m_per_year_rdc || {};
  const catalogColor = statusColors[catalogStatus?.status] || '#64748b';

  return (
    <div style={panelStyle}>
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
                      src={getSbasInsarProductPreviewUrl(detail.id)}
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
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 12 }}>
                  <ProductPreview title="LOS 速率图" asset={velocityPreview} productId={detail.id} />
                  <ProductPreview title="LOS Sigma 图" asset={sigmaPreview} productId={detail.id} />
                </div>
                <div style={{ marginTop: 12 }}>
                  <PointVectorDownload asset={pointVectorAsset} summary={pointVectorSummary} productId={detail.id} />
                </div>
                <div style={{ marginTop: 12 }}>
                  <div style={{ fontSize: 13, fontWeight: 800, color: '#0f172a', marginBottom: 8 }}>监测点形变曲线</div>
                  {monitorPreviews.length === 0 ? (
                    <div style={{ ...mutedStyle, border: '1px solid #e2e8f0', borderRadius: 8, padding: 10, background: '#f8fafc' }}>
                      暂无监测点曲线。
                    </div>
                  ) : (
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 12 }}>
                      {monitorPreviews.map(asset => (
                        <ProductPreview key={asset.id} title={asset.asset_name || '监测点曲线'} asset={asset} productId={detail.id} />
                      ))}
                    </div>
                  )}
                </div>
              </section>

              <section style={sectionStyle}>
                <h4 style={{ margin: '0 0 10px', fontSize: 15, color: '#0f172a' }}>统计摘要</h4>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 8 }}>
                  <Metric label="速率中位数" value={`${formatNumber(rateStats.median, 2)} mm/yr`} />
                  <Metric label="速率 P05 / P95" value={`${formatNumber(rateStats.p05, 2)} / ${formatNumber(rateStats.p95, 2)}`} />
                  <Metric label="Sigma 中位数" value={`${formatNumber(sigmaStats.median, 2)} mm/yr`} />
                  <Metric label="有效像元" value={rateStats.valid_count ?? '-'} />
                </div>
              </section>

              <section style={sectionStyle}>
                <h4 style={{ margin: '0 0 10px', fontSize: 15, color: '#0f172a' }}>资产下载</h4>
                <div style={{ display: 'grid', gap: 7 }}>
                  {selectedAssets.map(asset => (
                    <div
                      key={asset.id}
                      style={{
                        display: 'grid',
                        gridTemplateColumns: 'minmax(160px, 220px) minmax(0, 1fr) auto',
                        gap: 10,
                        alignItems: 'center',
                        border: '1px solid #e2e8f0',
                        borderRadius: 8,
                        padding: '8px 10px',
                        background: asset.exists_flag ? '#ffffff' : '#fef2f2',
                      }}
                    >
                      <div>
                        <div style={{ fontSize: 12, fontWeight: 750, color: '#0f172a' }}>{asset.asset_role}</div>
                        <div style={mutedStyle}>{formatBytes(asset.file_size)} / {asset.format || '-'}</div>
                      </div>
                      <div style={{ ...mutedStyle, wordBreak: 'break-all' }}>{asset.relative_path}</div>
                      {asset.exists_flag ? (
                        <a href={getSbasInsarProductAssetUrl(detail.id, asset.id)} target="_blank" rel="noreferrer" style={{ color: '#1d4ed8', fontSize: 12, fontWeight: 750 }}>
                          打开
                        </a>
                      ) : (
                        <span style={{ color: '#dc2626', fontSize: 12 }}>缺失</span>
                      )}
                    </div>
                  ))}
                </div>
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
