import React, { useCallback, useEffect, useMemo, useState } from 'react';

import apiClient from '../api/client';
import {
  getDinsarCatalogStatus,
  getDinsarProductDetail,
  listDinsarProducts,
  queueDinsarCatalogRebuild,
  queueDinsarProductPublish,
} from '../api/dinsarProducts';

const panelCardStyle = {
  background: '#fff',
  padding: '12px',
  borderRadius: '8px',
  border: '1px solid #e2e8f0',
};

const statusColorMap = {
  READY: '#16a34a',
  PARTIAL: '#b45309',
  QUARANTINED: '#dc2626',
  WARN: '#b45309',
  ERROR: '#dc2626',
  REBUILDING: '#2563eb',
};

function formatDateTime(value) {
  if (!value) return '-';
  try {
    return new Date(value).toLocaleString();
  } catch {
    return String(value);
  }
}

function parseDirectoryList(value) {
  return [...new Set(
    String(value || '')
      .split(/[\r\n,;]+/)
      .map(item => item.trim())
      .filter(Boolean)
  )];
}

function StatusPill({ label, color }) {
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '2px 10px',
        borderRadius: 999,
        background: `${color}14`,
        color,
        fontSize: 12,
        fontWeight: 600,
      }}
    >
      <span
        style={{
          width: 7,
          height: 7,
          borderRadius: '50%',
          background: color,
          display: 'inline-block',
        }}
      />
      {label}
    </span>
  );
}

export default function DinsarCatalogPanel({
  readOnly = false,
  compact = false,
  initialSourceDir = '',
  onTaskQueued,
}) {
  const [catalogStatus, setCatalogStatus] = useState(null);
  const [products, setProducts] = useState([]);
  const [selectedProductId, setSelectedProductId] = useState(null);
  const [selectedProduct, setSelectedProduct] = useState(null);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [actionMessage, setActionMessage] = useState('');
  const [sourceDirectoriesText, setSourceDirectoriesText] = useState(initialSourceDir || '');
  const [publishRoot, setPublishRoot] = useState('');

  const listLimit = compact ? 6 : 12;
  const previewBaseUrl = apiClient.defaults.baseURL || '/api';

  useEffect(() => {
    if (!initialSourceDir) return;
    setSourceDirectoriesText(current => (current.trim() ? current : initialSourceDir));
  }, [initialSourceDir]);

  const sourceDirectories = useMemo(
    () => parseDirectoryList(sourceDirectoriesText),
    [sourceDirectoriesText]
  );

  const loadCatalog = useCallback(async () => {
    setLoading(true);
    try {
      const [statusData, productData] = await Promise.all([
        getDinsarCatalogStatus(),
        listDinsarProducts({ limit: listLimit, offset: 0 }),
      ]);
      setCatalogStatus(statusData);
      const nextItems = Array.isArray(productData?.items) ? productData.items : [];
      setProducts(nextItems);
      setSelectedProductId(current => {
        if (current && nextItems.some(item => item.id === current)) {
          return current;
        }
        return nextItems[0]?.id ?? null;
      });
    } catch (error) {
      setActionMessage(`结果目录状态加载失败：${error?.response?.data?.detail || error.message}`);
      setCatalogStatus(null);
      setProducts([]);
      setSelectedProductId(null);
    } finally {
      setLoading(false);
    }
  }, [listLimit]);

  const loadProductDetail = useCallback(async (productId) => {
    if (!productId) {
      setSelectedProduct(null);
      return;
    }
    setDetailLoading(true);
    try {
      const detail = await getDinsarProductDetail(productId);
      setSelectedProduct(detail);
    } catch (error) {
      setSelectedProduct({
        error: error?.response?.data?.detail || error.message || '结果详情加载失败',
      });
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    loadCatalog();
    const timer = setInterval(() => {
      if (!actionLoading) {
        loadCatalog();
      }
    }, 10000);
    return () => clearInterval(timer);
  }, [actionLoading, loadCatalog]);

  useEffect(() => {
    loadProductDetail(selectedProductId);
  }, [loadProductDetail, selectedProductId]);

  const handleQueuePublish = async () => {
    if (readOnly || sourceDirectories.length === 0) return;
    setActionLoading(true);
    setActionMessage('');
    try {
      const result = await queueDinsarProductPublish({
        source_directories: sourceDirectories,
        publish_root: publishRoot.trim() || null,
        rebuild_catalog: true,
      });
      setActionMessage(`结果包发布任务已入队：${result.task_id}`);
      onTaskQueued?.(result.task_id);
      await loadCatalog();
    } catch (error) {
      setActionMessage(`结果包发布失败：${error?.response?.data?.detail || error.message}`);
    } finally {
      setActionLoading(false);
    }
  };

  const handleQueueRebuild = async () => {
    if (readOnly) return;
    setActionLoading(true);
    setActionMessage('');
    try {
      const result = await queueDinsarCatalogRebuild({
        publish_root: publishRoot.trim() || null,
        full_rebuild: true,
      });
      setActionMessage(`结果目录重建任务已入队：${result.task_id}`);
      onTaskQueued?.(result.task_id);
      await loadCatalog();
    } catch (error) {
      setActionMessage(`结果目录重建失败：${error?.response?.data?.detail || error.message}`);
    } finally {
      setActionLoading(false);
    }
  };

  const catalogColor = statusColorMap[catalogStatus?.status] || '#64748b';
  const selectedIssues = Array.isArray(selectedProduct?.issues) ? selectedProduct.issues : [];
  const selectedAssets = Array.isArray(selectedProduct?.assets) ? selectedProduct.assets : [];
  const selectedPairingTrace = selectedProduct?.pairing_trace || null;
  const selectedPairingNetwork = selectedProduct?.pairing_network || null;
  const selectedPairingRun = selectedPairingNetwork?.run || null;
  const selectedPairingEdge = selectedPairingNetwork?.edge || null;
  const selectedPairingMetric = selectedPairingNetwork?.metric || null;

  return (
    <div style={panelCardStyle}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginBottom: 10 }}>
        <div>
          <strong style={{ fontSize: 14 }}>{compact ? '结果目录状态' : '标准结果包目录'}</strong>
          <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>
            {compact ? '展示结果包目录与数据库索引状态' : '结果文件以结果包目录为真源，数据库仅保存索引与检索信息'}
          </div>
        </div>
        <button
          onClick={loadCatalog}
          disabled={loading || actionLoading}
          style={{
            fontSize: 12,
            padding: '4px 10px',
            borderRadius: 4,
            border: '1px solid #e2e8f0',
            background: '#f8fafc',
            cursor: 'pointer',
          }}
        >
          {loading ? '刷新中...' : '刷新'}
        </button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: compact ? 'repeat(2, minmax(0, 1fr))' : 'repeat(4, minmax(0, 1fr))', gap: 8, marginBottom: 10 }}>
        <div style={{ padding: '8px 10px', borderRadius: 6, background: '#f8fafc' }}>
          <div style={{ fontSize: 11, color: '#64748b' }}>目录状态</div>
          <div style={{ marginTop: 4 }}>
            <StatusPill label={catalogStatus?.status || '未知'} color={catalogColor} />
          </div>
        </div>
        <div style={{ padding: '8px 10px', borderRadius: 6, background: '#f8fafc' }}>
          <div style={{ fontSize: 11, color: '#64748b' }}>需要重建</div>
          <div style={{ marginTop: 4, fontSize: 16, fontWeight: 700, color: catalogStatus?.needs_rebuild ? '#dc2626' : '#16a34a' }}>
            {catalogStatus?.needs_rebuild ? '是' : '否'}
          </div>
        </div>
        <div style={{ padding: '8px 10px', borderRadius: 6, background: '#f8fafc' }}>
          <div style={{ fontSize: 11, color: '#64748b' }}>Manifest / 数据库</div>
          <div style={{ marginTop: 4, fontSize: 16, fontWeight: 700 }}>
            {(catalogStatus?.manifest_count ?? 0)} / {(catalogStatus?.db_count ?? 0)}
          </div>
        </div>
        <div style={{ padding: '8px 10px', borderRadius: 6, background: '#f8fafc' }}>
          <div style={{ fontSize: 11, color: '#64748b' }}>问题数量</div>
          <div style={{ marginTop: 4, fontSize: 16, fontWeight: 700, color: (catalogStatus?.issue_count ?? 0) > 0 ? '#b45309' : '#16a34a' }}>
            {catalogStatus?.issue_count ?? 0}
          </div>
        </div>
      </div>

      <div style={{ fontSize: 12, color: '#475569', marginBottom: 8, wordBreak: 'break-all' }}>
        <div><strong>结果包根目录：</strong>{catalogStatus?.storage_root || '-'}</div>
        <div><strong>最近消息：</strong>{catalogStatus?.last_message || '-'}</div>
        <div><strong>最近重建：</strong>{formatDateTime(catalogStatus?.last_full_rebuild_at)}</div>
      </div>

      {compact && actionMessage && (
        <div style={{ marginBottom: 8, fontSize: 12, color: actionMessage.includes('失败') ? '#dc2626' : '#166534' }}>
          {actionMessage}
        </div>
      )}

      {!compact && (
        <div style={{ marginBottom: 12, padding: '10px 12px', borderRadius: 6, border: '1px solid #e2e8f0', background: '#f8fafc' }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: '#0f172a', marginBottom: 8 }}>手动发布与重建</div>
          <div style={{ fontSize: 12, color: '#475569', marginBottom: 8 }}>
            旧的“提取位移结果”入口已经会自动尝试发布标准结果包。这里保留显式入口，方便你对任意目录重新发布和重建索引。
          </div>
          <textarea
            value={sourceDirectoriesText}
            onChange={event => setSourceDirectoriesText(event.target.value)}
            placeholder="输入一个或多个结果根目录，支持换行、逗号或分号分隔"
            disabled={readOnly || actionLoading}
            style={{
              width: '100%',
              minHeight: 72,
              resize: 'vertical',
              padding: '8px 10px',
              boxSizing: 'border-box',
              borderRadius: 6,
              border: '1px solid #cbd5e1',
              fontSize: 12,
              marginBottom: 8,
            }}
          />
          <input
            value={publishRoot}
            onChange={event => setPublishRoot(event.target.value)}
            placeholder="可选：自定义结果包根目录，留空使用系统默认目录"
            disabled={readOnly || actionLoading}
            style={{
              width: '100%',
              padding: '6px 10px',
              boxSizing: 'border-box',
              borderRadius: 6,
              border: '1px solid #cbd5e1',
              fontSize: 12,
              marginBottom: 8,
            }}
          />
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button
              onClick={handleQueuePublish}
              disabled={readOnly || actionLoading || sourceDirectories.length === 0}
              style={{
                padding: '6px 14px',
                borderRadius: 6,
                border: 'none',
                background: '#2563eb',
                color: '#fff',
                cursor: 'pointer',
                fontSize: 12,
              }}
            >
              {actionLoading ? '处理中...' : '发布结果包并重建'}
            </button>
            <button
              onClick={handleQueueRebuild}
              disabled={readOnly || actionLoading}
              style={{
                padding: '6px 14px',
                borderRadius: 6,
                border: '1px solid #cbd5e1',
                background: '#fff',
                color: '#0f172a',
                cursor: 'pointer',
                fontSize: 12,
              }}
            >
              仅重建目录索引
            </button>
          </div>
          {actionMessage && (
            <div style={{ marginTop: 8, fontSize: 12, color: actionMessage.includes('失败') ? '#dc2626' : '#166534' }}>
              {actionMessage}
            </div>
          )}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: compact ? '1fr' : 'minmax(260px, 360px) 1fr', gap: 12 }}>
        <div style={{ border: '1px solid #e2e8f0', borderRadius: 6, overflow: 'hidden' }}>
          <div style={{ padding: '8px 10px', background: '#f8fafc', fontSize: 12, fontWeight: 600 }}>
            最新结果包 ({products.length})
          </div>
          {products.length === 0 ? (
            <div style={{ padding: '12px', fontSize: 12, color: '#94a3b8' }}>
              {loading ? '正在加载结果包...' : '当前没有已注册的结果包。'}
            </div>
          ) : (
            <div style={{ maxHeight: compact ? 280 : 360, overflowY: 'auto' }}>
              {products.map(item => {
                const color = statusColorMap[item.status] || '#64748b';
                return (
                  <button
                    key={item.id}
                    onClick={() => setSelectedProductId(item.id)}
                    style={{
                      display: 'block',
                      width: '100%',
                      textAlign: 'left',
                      border: 'none',
                      borderTop: '1px solid #f1f5f9',
                      background: selectedProductId === item.id ? '#eff6ff' : '#fff',
                      padding: '10px 12px',
                      cursor: 'pointer',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, marginBottom: 4 }}>
                      <strong style={{ fontSize: 12, color: '#0f172a', wordBreak: 'break-all' }}>{item.display_name || item.product_id}</strong>
                      <span style={{ fontSize: 11, color }}>{item.status}</span>
                    </div>
                    <div style={{ fontSize: 11, color: '#64748b' }}>
                      {item.engine_code || '-'} · {formatDateTime(item.published_at)}
                    </div>
                    <div style={{ fontSize: 11, color: '#64748b', marginTop: 2, wordBreak: 'break-all' }}>
                      {(item.task_alias || item.task_name || '-')}{item.run_key ? ` / ${item.run_key}` : ''}
                    </div>
                    {(item.selection_strategy || item.network_run_id || item.network_edge_id) && (
                      <div style={{ fontSize: 11, color: '#475569', marginTop: 2, wordBreak: 'break-all' }}>
                        {(item.selection_strategy || 'trace')}
                        {item.network_edge_id ? ` / edge ${item.network_edge_id}` : ''}
                        {item.network_run_id ? ` / ${item.network_run_id}` : ''}
                      </div>
                    )}
                  </button>
                );
              })}
            </div>
          )}
        </div>

        <div style={{ border: '1px solid #e2e8f0', borderRadius: 6, overflow: 'hidden' }}>
          <div style={{ padding: '8px 10px', background: '#f8fafc', fontSize: 12, fontWeight: 600 }}>
            结果包详情
          </div>
          {!selectedProductId ? (
            <div style={{ padding: '12px', fontSize: 12, color: '#94a3b8' }}>请选择一个结果包查看详情。</div>
          ) : detailLoading ? (
            <div style={{ padding: '12px', fontSize: 12, color: '#94a3b8' }}>正在加载详情...</div>
          ) : selectedProduct?.error ? (
            <div style={{ padding: '12px', fontSize: 12, color: '#dc2626' }}>{selectedProduct.error}</div>
          ) : (
            <div style={{ padding: '12px', display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{ display: 'grid', gridTemplateColumns: compact ? '1fr' : '180px 1fr', gap: 12 }}>
                <div>
                  <div style={{ border: '1px solid #e2e8f0', borderRadius: 6, overflow: 'hidden', background: '#f8fafc' }}>
                    <img
                      src={`${previewBaseUrl}/dinsar-products/${selectedProduct.id}/preview`}
                      alt={selectedProduct.display_name}
                      style={{ display: 'block', width: '100%', minHeight: 120, objectFit: 'cover', background: '#e2e8f0' }}
                    />
                  </div>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 12, color: '#334155' }}>
                  <div><strong>名称：</strong>{selectedProduct.display_name || '-'}</div>
                  <div><strong>产品编号：</strong>{selectedProduct.product_id || '-'}</div>
                  <div><strong>任务别名：</strong>{selectedProduct.task_alias || selectedProduct.task_name || '-'}</div>
                  <div><strong>配对标识：</strong><span style={{ wordBreak: 'break-all' }}>{selectedProduct.pair_key || '-'}</span></div>
                  <div><strong>场景对 UID：</strong><span style={{ wordBreak: 'break-all' }}>{selectedProduct.pair_uid || '-'}</span></div>
                  <div><strong>运行标识：</strong><span style={{ wordBreak: 'break-all' }}>{selectedProduct.run_key || '-'}</span></div>
                  <div><strong>生产配置：</strong>{selectedProduct.profile_code || '-'}</div>
                  <div><strong>引擎：</strong>{selectedProduct.engine_code || '-'}</div>
                  <div><strong>状态：</strong>{selectedProduct.status || '-'} / {selectedProduct.health_status || '-'}</div>
                  <div><strong>主文件：</strong><span style={{ wordBreak: 'break-all' }}>{selectedProduct.primary_asset_path || '-'}</span></div>
                  <div><strong>来源文件：</strong><span style={{ wordBreak: 'break-all' }}>{selectedProduct.source_primary_path || '-'}</span></div>
                  <div><strong>结果包目录：</strong><span style={{ wordBreak: 'break-all' }}>{selectedProduct.publish_dir || '-'}</span></div>
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: compact ? '1fr' : 'repeat(2, minmax(0, 1fr))', gap: 8 }}>
                <div style={{ padding: '8px 10px', borderRadius: 6, background: '#f8fafc', fontSize: 12 }}>
                  <div><strong>主影像日期：</strong>{selectedProduct.profile?.master_imaging_date || '-'}</div>
                  <div><strong>辅影像日期：</strong>{selectedProduct.profile?.slave_imaging_date || '-'}</div>
                  <div><strong>时间基线：</strong>{selectedProduct.profile?.time_baseline_days ?? '-'}</div>
                  <div><strong>空间基线：</strong>{selectedProduct.profile?.spatial_baseline_meters ?? '-'}</div>
                </div>
                <div style={{ padding: '8px 10px', borderRadius: 6, background: '#f8fafc', fontSize: 12 }}>
                  <div><strong>BBox：</strong></div>
                  <div>{selectedProduct.min_lon ?? '-'}, {selectedProduct.min_lat ?? '-'}</div>
                  <div>{selectedProduct.max_lon ?? '-'}, {selectedProduct.max_lat ?? '-'}</div>
                  <div style={{ marginTop: 4 }}><strong>注册时间：</strong>{formatDateTime(selectedProduct.registered_at)}</div>
                </div>
              </div>

              <div style={{ fontSize: 12 }}>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>配对追踪</div>
                {!selectedPairingTrace?.network_run_id ? (
                  <div style={{ color: '#94a3b8' }}>当前结果未携带配对网络追踪信息。</div>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    <div style={{ display: 'grid', gridTemplateColumns: compact ? '1fr' : 'repeat(2, minmax(0, 1fr))', gap: 8 }}>
                      <div style={{ padding: '8px 10px', borderRadius: 6, background: '#f8fafc' }}>
                        <div><strong>network_run_id：</strong><span style={{ wordBreak: 'break-all' }}>{selectedPairingTrace.network_run_id || '-'}</span></div>
                        <div><strong>network_edge_id：</strong>{selectedPairingTrace.network_edge_id ?? '-'}</div>
                        <div><strong>pair_uid：</strong><span style={{ wordBreak: 'break-all' }}>{selectedPairingTrace.pair_uid || '-'}</span></div>
                        <div><strong>策略：</strong>{selectedPairingTrace.selection_strategy || '-'}</div>
                        <div><strong>策略版本：</strong>{selectedPairingTrace.policy_version || '-'}</div>
                      </div>
                      <div style={{ padding: '8px 10px', borderRadius: 6, background: '#f8fafc' }}>
                        <div><strong>网络记录：</strong>{selectedPairingNetwork?.run_found ? '已找到' : '未找到'}</div>
                        <div><strong>边记录：</strong>{selectedPairingNetwork?.edge_found ? '已找到' : '未找到'}</div>
                        <div><strong>运行状态：</strong>{selectedPairingRun?.status || '-'}</div>
                        <div><strong>候选边数：</strong>{selectedPairingRun?.candidate_count ?? '-'}</div>
                        <div><strong>入选边数：</strong>{selectedPairingRun?.selected_edge_count ?? '-'}</div>
                        <div><strong>告警数：</strong>{selectedPairingRun?.warning_count ?? '-'}</div>
                      </div>
                    </div>

                    {(selectedPairingEdge || selectedPairingMetric) && (
                      <div style={{ display: 'grid', gridTemplateColumns: compact ? '1fr' : 'repeat(2, minmax(0, 1fr))', gap: 8 }}>
                        <div style={{ padding: '8px 10px', borderRadius: 6, background: '#f8fafc' }}>
                          <div style={{ fontWeight: 600, marginBottom: 4 }}>网络边</div>
                          <div><strong>edge_rank：</strong>{selectedPairingEdge?.edge_rank ?? '-'}</div>
                          <div><strong>selection_reason：</strong>{selectedPairingEdge?.selection_reason || '-'}</div>
                          <div><strong>selection_score：</strong>{selectedPairingEdge?.selection_score ?? '-'}</div>
                          <div><strong>reference_edge：</strong>{selectedPairingEdge?.is_reference_edge ? '是' : '否'}</div>
                          <div><strong>metric_cache_ref_id：</strong>{selectedPairingEdge?.metric_cache_ref_id ?? '-'}</div>
                        </div>
                        <div style={{ padding: '8px 10px', borderRadius: 6, background: '#f8fafc' }}>
                          <div style={{ fontWeight: 600, marginBottom: 4 }}>度量快照</div>
                          <div><strong>主从日期：</strong>{selectedPairingMetric?.master_imaging_date || '-'} / {selectedPairingMetric?.slave_imaging_date || '-'}</div>
                          <div><strong>主从卫星：</strong>{selectedPairingMetric?.master_satellite || '-'} / {selectedPairingMetric?.slave_satellite || '-'}</div>
                          <div><strong>主从模式：</strong>{selectedPairingMetric?.master_imaging_mode || '-'} / {selectedPairingMetric?.slave_imaging_mode || '-'}</div>
                          <div><strong>主从极化：</strong>{selectedPairingMetric?.master_polarization || '-'} / {selectedPairingMetric?.slave_polarization || '-'}</div>
                          <div><strong>时间基线：</strong>{selectedPairingMetric?.time_baseline_days ?? '-'}</div>
                          <div><strong>空间基线：</strong>{selectedPairingMetric?.spatial_baseline_meters ?? '-'}</div>
                          <div><strong>重叠率：</strong>{selectedPairingMetric?.scene_overlap_ratio ?? '-'}</div>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>

              <div style={{ fontSize: 12 }}>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>资产列表 ({selectedAssets.length})</div>
                {selectedAssets.length === 0 ? (
                  <div style={{ color: '#94a3b8' }}>暂无资产记录。</div>
                ) : (
                  selectedAssets.map(asset => (
                    <div
                      key={asset.id}
                      style={{
                        padding: '6px 8px',
                        borderRadius: 6,
                        background: '#f8fafc',
                        marginBottom: 6,
                        color: '#334155',
                      }}
                    >
                      <div><strong>{asset.asset_role}</strong> · {asset.asset_name}</div>
                      <div style={{ color: asset.exists_flag ? '#166534' : '#dc2626' }}>
                        {asset.exists_flag ? '文件存在' : '文件缺失'}
                      </div>
                      <div style={{ wordBreak: 'break-all', color: '#64748b' }}>{asset.absolute_path}</div>
                    </div>
                  ))
                )}
              </div>

              <div style={{ fontSize: 12 }}>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>问题列表 ({selectedIssues.length})</div>
                {selectedIssues.length === 0 ? (
                  <div style={{ color: '#16a34a' }}>当前没有登记问题。</div>
                ) : (
                  selectedIssues.map(issue => (
                    <div
                      key={issue.id}
                      style={{
                        padding: '6px 8px',
                        borderRadius: 6,
                        background: issue.severity === 'ERROR' ? '#fef2f2' : '#fff7ed',
                        color: issue.severity === 'ERROR' ? '#991b1b' : '#9a3412',
                        marginBottom: 6,
                      }}
                    >
                      <div><strong>{issue.issue_code}</strong> · {issue.severity}</div>
                      <div>{issue.message}</div>
                      {issue.repair_action && (
                        <div style={{ color: '#64748b', marginTop: 2 }}>
                          建议修复动作：{issue.repair_action}
                        </div>
                      )}
                    </div>
                  ))
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
