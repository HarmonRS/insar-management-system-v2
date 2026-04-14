import React, { useCallback, useEffect, useState } from 'react';

import apiClient from '../api/client';
import {
  getPsinsarCatalogStatus,
  getPsinsarProductDetail,
  listPsinsarProducts,
  queuePsinsarCatalogRebuild,
} from '../api/psinsarProducts';

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

export default function PsinsarCatalogPanel({
  readOnly = false,
  showActions = true,
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
  const [publishRoot, setPublishRoot] = useState('');

  const previewBaseUrl = apiClient.defaults.baseURL || '/api';

  const loadCatalog = useCallback(async () => {
    setLoading(true);
    try {
      const [statusData, productData] = await Promise.all([
        getPsinsarCatalogStatus(),
        listPsinsarProducts({ limit: 20, offset: 0 }),
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
      setActionMessage(`PS-InSAR 结果目录状态加载失败：${error?.response?.data?.detail || error.message}`);
      setCatalogStatus(null);
      setProducts([]);
      setSelectedProductId(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadProductDetail = useCallback(async productId => {
    if (!productId) {
      setSelectedProduct(null);
      return;
    }
    setDetailLoading(true);
    try {
      const detail = await getPsinsarProductDetail(productId);
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

  const handleQueueRebuild = async () => {
    if (readOnly) return;
    setActionLoading(true);
    setActionMessage('');
    try {
      const result = await queuePsinsarCatalogRebuild({
        publish_root: publishRoot.trim() || null,
        full_rebuild: true,
      });
      setActionMessage(`PS-InSAR 结果目录重建任务已入队：${result.task_id}`);
      onTaskQueued?.(result.task_id);
      await loadCatalog();
    } catch (error) {
      setActionMessage(`PS-InSAR 结果目录重建失败：${error?.response?.data?.detail || error.message}`);
    } finally {
      setActionLoading(false);
    }
  };

  const catalogColor = statusColorMap[catalogStatus?.status] || '#64748b';
  const selectedAssets = Array.isArray(selectedProduct?.assets) ? selectedProduct.assets : [];
  const selectedIssues = Array.isArray(selectedProduct?.issues) ? selectedProduct.issues : [];

  return (
    <div style={panelCardStyle}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginBottom: 10 }}>
        <div>
          <strong style={{ fontSize: 14 }}>PS-InSAR 结果目录</strong>
          <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>
            结果目录以 `psinsar.publish.v1` bundle 为事实源，数据库仅保存索引与展示信息。
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

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 8, marginBottom: 10 }}>
        <div style={{ padding: '8px 10px', borderRadius: 6, background: '#f8fafc' }}>
          <div style={{ fontSize: 11, color: '#64748b' }}>目录状态</div>
          <div style={{ marginTop: 4 }}>
            <StatusPill label={catalogStatus?.status || 'UNKNOWN'} color={catalogColor} />
          </div>
        </div>
        <div style={{ padding: '8px 10px', borderRadius: 6, background: '#f8fafc' }}>
          <div style={{ fontSize: 11, color: '#64748b' }}>需要重建</div>
          <div style={{ marginTop: 4, fontSize: 16, fontWeight: 700, color: catalogStatus?.needs_rebuild ? '#dc2626' : '#16a34a' }}>
            {catalogStatus?.needs_rebuild ? '是' : '否'}
          </div>
        </div>
        <div style={{ padding: '8px 10px', borderRadius: 6, background: '#f8fafc' }}>
          <div style={{ fontSize: 11, color: '#64748b' }}>Manifest / DB</div>
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

      <div style={{ fontSize: 12, color: '#475569', marginBottom: 10, wordBreak: 'break-all' }}>
        <div><strong>目录根路径：</strong>{catalogStatus?.storage_root || '-'}</div>
        <div><strong>最近消息：</strong>{catalogStatus?.last_message || '-'}</div>
        <div><strong>最近重建：</strong>{formatDateTime(catalogStatus?.last_full_rebuild_at)}</div>
      </div>

      {showActions && (
        <div style={{ marginBottom: 12, padding: '10px 12px', borderRadius: 6, border: '1px solid #e2e8f0', background: '#f8fafc' }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: '#0f172a', marginBottom: 8 }}>目录管理</div>
          <input
            value={publishRoot}
            onChange={event => setPublishRoot(event.target.value)}
            placeholder="可选：自定义 PS-InSAR 发布根目录，留空使用系统默认目录"
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
          <button
            onClick={handleQueueRebuild}
            disabled={readOnly || actionLoading}
            style={{
              padding: '6px 14px',
              borderRadius: 6,
              border: '1px solid #cbd5e1',
              background: '#fff',
              color: '#0f172a',
              cursor: readOnly ? 'not-allowed' : 'pointer',
              fontSize: 12,
            }}
          >
            {actionLoading ? '处理中...' : '重建 PS-InSAR 结果目录'}
          </button>
          {actionMessage && (
            <div style={{ marginTop: 8, fontSize: 12, color: actionMessage.includes('失败') ? '#dc2626' : '#166534' }}>
              {actionMessage}
            </div>
          )}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(260px, 360px) 1fr', gap: 12 }}>
        <div style={{ border: '1px solid #e2e8f0', borderRadius: 6, overflow: 'hidden' }}>
          <div style={{ padding: '8px 10px', background: '#f8fafc', fontSize: 12, fontWeight: 600 }}>
            产品列表 ({products.length})
          </div>
          {products.length === 0 ? (
            <div style={{ padding: '12px', fontSize: 12, color: '#94a3b8' }}>
              {loading ? '正在加载结果...' : '当前没有已登记的 PS-InSAR 产品。'}
            </div>
          ) : (
            <div style={{ maxHeight: 420, overflowY: 'auto' }}>
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
                      <strong style={{ fontSize: 12, color: '#0f172a', wordBreak: 'break-all' }}>
                        {item.display_name || item.product_id}
                      </strong>
                      <span style={{ fontSize: 11, color }}>{item.status}</span>
                    </div>
                    <div style={{ fontSize: 11, color: '#64748b' }}>
                      {item.reference_date || '-'} / {item.stack_size || 0} 景 / {formatDateTime(item.published_at)}
                    </div>
                    <div style={{ fontSize: 11, color: '#64748b', marginTop: 2, wordBreak: 'break-all' }}>
                      {item.run_key || '-'}
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        <div style={{ border: '1px solid #e2e8f0', borderRadius: 6, overflow: 'hidden' }}>
          <div style={{ padding: '8px 10px', background: '#f8fafc', fontSize: 12, fontWeight: 600 }}>
            产品详情
          </div>
          {!selectedProductId ? (
            <div style={{ padding: '12px', fontSize: 12, color: '#94a3b8' }}>请选择一个产品查看详情。</div>
          ) : detailLoading ? (
            <div style={{ padding: '12px', fontSize: 12, color: '#94a3b8' }}>正在加载详情...</div>
          ) : selectedProduct?.error ? (
            <div style={{ padding: '12px', fontSize: 12, color: '#dc2626' }}>{selectedProduct.error}</div>
          ) : (
            <div style={{ padding: '12px', display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{ display: 'grid', gridTemplateColumns: '180px 1fr', gap: 12 }}>
                <div>
                  <div style={{ border: '1px solid #e2e8f0', borderRadius: 6, overflow: 'hidden', background: '#f8fafc' }}>
                    <img
                      src={`${previewBaseUrl}/ps-products/${selectedProduct.id}/preview`}
                      alt={selectedProduct.display_name}
                      style={{ display: 'block', width: '100%', minHeight: 120, objectFit: 'cover', background: '#e2e8f0' }}
                    />
                  </div>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 12, color: '#334155' }}>
                  <div><strong>名称：</strong>{selectedProduct.display_name || '-'}</div>
                  <div><strong>产品编号：</strong>{selectedProduct.product_id || '-'}</div>
                  <div><strong>运行标识：</strong>{selectedProduct.run_key || '-'}</div>
                  <div><strong>参考日期：</strong>{selectedProduct.reference_date || '-'}</div>
                  <div><strong>影像数：</strong>{selectedProduct.stack_size || 0}</div>
                  <div><strong>引擎：</strong>{selectedProduct.engine_code || '-'}</div>
                  <div><strong>处理器：</strong>{selectedProduct.profile_code || '-'}</div>
                  <div><strong>状态：</strong>{selectedProduct.status || '-'} / {selectedProduct.health_status || '-'}</div>
                  <div><strong>发布时间：</strong>{formatDateTime(selectedProduct.published_at)}</div>
                  <div><strong>发布日期列表：</strong>{(selectedProduct.stack_dates || []).join(', ') || '-'}</div>
                </div>
              </div>

              <div style={{ fontSize: 12, color: '#334155' }}>
                <div><strong>Manifest：</strong>{selectedProduct.manifest_path || '-'}</div>
                <div><strong>发布目录：</strong>{selectedProduct.publish_dir || '-'}</div>
                <div><strong>主科学产物：</strong>{selectedProduct.source_primary_path || '-'}</div>
                <div><strong>主展示产物：</strong>{selectedProduct.primary_asset_path || '-'}</div>
              </div>

              <div style={{ borderTop: '1px dashed #cbd5e1', paddingTop: 10 }}>
                <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>质量摘要</div>
                <pre
                  style={{
                    margin: 0,
                    padding: '8px 10px',
                    background: '#f8fafc',
                    borderRadius: 6,
                    border: '1px solid #e2e8f0',
                    fontSize: 11,
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-all',
                  }}
                >
                  {JSON.stringify(selectedProduct.quality || {}, null, 2)}
                </pre>
              </div>

              <div style={{ borderTop: '1px dashed #cbd5e1', paddingTop: 10 }}>
                <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>资产列表 ({selectedAssets.length})</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {selectedAssets.map(asset => (
                    <div
                      key={asset.id}
                      style={{ padding: '8px 10px', borderRadius: 6, border: '1px solid #e2e8f0', background: '#fff', fontSize: 12 }}
                    >
                      <div><strong>{asset.asset_role}</strong> / {asset.asset_name}</div>
                      <div style={{ color: '#64748b', wordBreak: 'break-all' }}>{asset.relative_path}</div>
                      <div style={{ color: asset.exists_flag ? '#166534' : '#dc2626' }}>
                        {asset.exists_flag ? '文件存在' : '文件缺失'}
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div style={{ borderTop: '1px dashed #cbd5e1', paddingTop: 10 }}>
                <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>问题列表 ({selectedIssues.length})</div>
                {selectedIssues.length === 0 ? (
                  <div style={{ fontSize: 12, color: '#16a34a' }}>当前未检测到问题。</div>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {selectedIssues.map(issue => (
                      <div
                        key={issue.id}
                        style={{
                          padding: '8px 10px',
                          borderRadius: 6,
                          border: '1px solid #e2e8f0',
                          background: '#fff',
                          fontSize: 12,
                          color: issue.severity === 'ERROR' ? '#dc2626' : '#b45309',
                        }}
                      >
                        <div><strong>{issue.severity}</strong> / {issue.issue_code}</div>
                        <div>{issue.message}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
