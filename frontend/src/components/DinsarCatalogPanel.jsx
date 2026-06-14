import React, { useCallback, useEffect, useMemo, useState } from 'react';

import apiClient from '../api/client';
import {
  getDinsarCatalogStatus,
  getDinsarProductDetail,
  getDinsarProductCleanupPlan,
  listDinsarProductPairs,
  queueDinsarCatalogRebuild,
  queueDinsarProductPublish,
} from '../api/dinsarProducts';
import {
  DINSAR_ENGINE_ALL,
  buildDinsarEngineOptions,
  getDinsarEngineMeta,
} from '../utils/dinsarEngines';
import { formatSatelliteFamilyLabel, inferSatelliteFamilyFromResultLike } from '../utils/satelliteFamily';

const STATUS_TONE_MAP = {
  READY: 'ready',
  PARTIAL: 'warn',
  QUARANTINED: 'error',
  WARN: 'warn',
  ERROR: 'error',
  REBUILDING: 'info',
  ready: 'ready',
  missing: 'neutral',
  failed: 'error',
  blocked: 'warn',
  legacy: 'neutral',
};

function formatDateTime(value) {
  if (!value) return '-';
  try {
    return new Date(value).toLocaleString();
  } catch {
    return String(value);
  }
}

function formatBytes(value) {
  const size = Number(value || 0);
  if (!Number.isFinite(size) || size <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let next = size;
  let index = 0;
  while (next >= 1024 && index < units.length - 1) {
    next /= 1024;
    index += 1;
  }
  return `${next.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function parseDirectoryList(value) {
  return [...new Set(
    String(value || '')
      .split(/[\r\n,;]+/)
      .map((item) => item.trim())
      .filter(Boolean)
  )];
}

function getMessageTone(message) {
  return /失败|error|Error|ERROR/.test(String(message || '')) ? 'error' : 'success';
}

function StatusPill({ label, tone = 'neutral' }) {
  return <span className={`dinsar-status-pill tone-${tone}`}>{label}</span>;
}

function engineStatusTone(status) {
  const normalized = String(status || '').toLowerCase();
  if (normalized === 'ready') return 'ready';
  if (normalized === 'failed') return 'error';
  if (normalized === 'blocked') return 'warn';
  if (normalized === 'running') return 'info';
  return 'neutral';
}

function MetaField({ label, value, multiline = false }) {
  const displayValue = value === null || value === undefined || value === '' ? '-' : value;
  return (
    <div className="dinsar-catalog-meta-field">
      <span>{label}</span>
      <strong className={multiline ? 'break-all' : ''}>{displayValue}</strong>
    </div>
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
  const [productPairs, setProductPairs] = useState([]);
  const [selectedPairKey, setSelectedPairKey] = useState('');
  const [selectedProductId, setSelectedProductId] = useState(null);
  const [selectedProduct, setSelectedProduct] = useState(null);
  const [cleanupPlan, setCleanupPlan] = useState(null);
  const [cleanupPlanLoading, setCleanupPlanLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [actionMessage, setActionMessage] = useState('');
  const [sourceDirectoriesText, setSourceDirectoriesText] = useState(initialSourceDir || '');
  const [publishRoot, setPublishRoot] = useState('');
  const [engineFilter, setEngineFilter] = useState(DINSAR_ENGINE_ALL);
  const [queryDraft, setQueryDraft] = useState('');
  const [queryApplied, setQueryApplied] = useState('');

  const listLimit = compact ? 8 : 24;
  const previewBaseUrl = apiClient.defaults.baseURL || '/api';

  useEffect(() => {
    if (!initialSourceDir) return;
    setSourceDirectoriesText((current) => (current.trim() ? current : initialSourceDir));
  }, [initialSourceDir]);

  const sourceDirectories = useMemo(
    () => parseDirectoryList(sourceDirectoriesText),
    [sourceDirectoriesText]
  );

  const engineOptions = useMemo(
    () => buildDinsarEngineOptions([], { includeKnown: true }),
    []
  );
  const selectedEngineMeta = useMemo(
    () => (engineFilter === DINSAR_ENGINE_ALL ? null : getDinsarEngineMeta(engineFilter)),
    [engineFilter]
  );

  useEffect(() => {
    if (engineFilter === DINSAR_ENGINE_ALL) return;
    if (!engineOptions.some((option) => option.value === engineFilter)) {
      setEngineFilter(DINSAR_ENGINE_ALL);
    }
  }, [engineFilter, engineOptions]);

  const loadCatalog = useCallback(async () => {
    setLoading(true);
    try {
      const [statusData, productData] = await Promise.all([
        getDinsarCatalogStatus(),
        listDinsarProductPairs({
          limit: listLimit,
          offset: 0,
          engine_code: engineFilter === DINSAR_ENGINE_ALL ? undefined : engineFilter,
          query: queryApplied || undefined,
        }),
      ]);
      setCatalogStatus(statusData);
      const nextPairs = Array.isArray(productData?.items) ? productData.items : [];
      setProductPairs(nextPairs);
      const nextItems = nextPairs
        .map((item) => ({
          id: item.primary_product_id,
          engine_code: item.primary_engine_code,
          pair_key: item.pair_key,
        }))
        .filter((item) => item.id);
      setProducts(nextItems);
      setSelectedPairKey((current) => {
        if (current && nextPairs.some((item) => (item.pair_key || `pair:${item.primary_product_id}`) === current)) {
          return current;
        }
        const first = nextPairs[0];
        return first ? (first.pair_key || `pair:${first.primary_product_id}`) : '';
      });
      setSelectedProductId((current) => {
        if (current && nextItems.some((item) => item.id === current)) {
          return current;
        }
        return nextPairs[0]?.primary_product_id ?? null;
      });
    } catch (error) {
      setActionMessage(`结果目录状态加载失败：${error?.response?.data?.detail || error.message}`);
      setCatalogStatus(null);
      setProducts([]);
      setProductPairs([]);
      setSelectedPairKey('');
      setSelectedProductId(null);
    } finally {
      setLoading(false);
    }
  }, [engineFilter, listLimit, queryApplied]);

  const loadProductDetail = useCallback(async (productId) => {
    if (!productId) {
      setSelectedProduct(null);
      setCleanupPlan(null);
      return;
    }
    setSelectedProduct(null);
    setCleanupPlan(null);
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

  const loadCleanupPlan = useCallback(async () => {
    if (!selectedProductId) return;
    setCleanupPlanLoading(true);
    try {
      const plan = await getDinsarProductCleanupPlan(selectedProductId);
      setCleanupPlan(plan);
    } catch (error) {
      setCleanupPlan({
        error: error?.response?.data?.detail || error.message || '中间文件清理计划加载失败',
      });
    } finally {
      setCleanupPlanLoading(false);
    }
  }, [selectedProductId]);

  useEffect(() => {
    loadCatalog();
  }, [loadCatalog]);

  useEffect(() => {
    loadProductDetail(selectedProductId);
  }, [loadProductDetail, selectedProductId]);

  const handleApplyFilters = useCallback(() => {
    setQueryApplied(queryDraft.trim());
  }, [queryDraft]);

  const handleResetFilters = useCallback(() => {
    setEngineFilter(DINSAR_ENGINE_ALL);
    setQueryDraft('');
    setQueryApplied('');
  }, []);

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

  const catalogTone = STATUS_TONE_MAP[catalogStatus?.status] || 'neutral';
  const actionTone = getMessageTone(actionMessage);
  const selectedIssues = Array.isArray(selectedProduct?.issues) ? selectedProduct.issues : [];
  const selectedAssets = Array.isArray(selectedProduct?.assets) ? selectedProduct.assets : [];
  const selectedPairingTrace = selectedProduct?.pairing_trace || null;
  const selectedPairingNetwork = selectedProduct?.pairing_network || null;
  const selectedPairingRun = selectedPairingNetwork?.run || null;
  const selectedPairingEdge = selectedPairingNetwork?.edge || null;
  const selectedPairingMetric = selectedPairingNetwork?.metric || null;
  const selectedProductEngine = getDinsarEngineMeta(selectedProduct?.engine_code);
  const selectedStatusTone = STATUS_TONE_MAP[selectedProduct?.status] || 'neutral';

  return (
    <div className={`dinsar-catalog-shell ${compact ? 'compact' : ''}`}>
      <div className="dinsar-catalog-header">
        <div className="dinsar-catalog-header-copy">
          <strong>{compact ? '结果目录状态' : '标准结果包目录'}</strong>
          <p>
            {compact
              ? '查看结果包目录与数据库索引是否一致。'
              : '统一结果目录按 engine + pair + run 管理，便于同一对影像保留多套生产结果并行对比。'}
          </p>
        </div>
        <div className="dinsar-catalog-header-actions">
          {selectedEngineMeta && (
            <span className={`dinsar-engine-badge tone-${selectedEngineMeta.tone}`}>
              {selectedEngineMeta.shortLabel}
            </span>
          )}
          <button onClick={loadCatalog} disabled={loading || actionLoading}>
            {loading ? '刷新中...' : '刷新'}
          </button>
        </div>
      </div>

      <div className="dinsar-catalog-summary">
        <div className="dinsar-catalog-stat-card">
          <span>目录状态</span>
          <strong>{catalogStatus?.status || '未知'}</strong>
          <StatusPill label={catalogStatus?.status || 'UNKNOWN'} tone={catalogTone} />
        </div>
        <div className="dinsar-catalog-stat-card">
          <span>需要重建</span>
          <strong>{catalogStatus?.needs_rebuild ? '是' : '否'}</strong>
          <small>{catalogStatus?.needs_rebuild ? 'Manifest 与数据库存在漂移' : '目录登记正常'}</small>
        </div>
        <div className="dinsar-catalog-stat-card">
          <span>Manifest / 数据库</span>
          <strong>{catalogStatus?.manifest_count ?? 0} / {catalogStatus?.db_count ?? 0}</strong>
          <small>已登记结果包总量</small>
        </div>
        <div className="dinsar-catalog-stat-card">
          <span>问题数量</span>
          <strong>{catalogStatus?.issue_count ?? 0}</strong>
          <small>含缺失文件与健康异常</small>
        </div>
      </div>

      <div className="dinsar-catalog-meta-strip">
        <div><strong>结果包根目录：</strong>{catalogStatus?.storage_root || '-'}</div>
        <div><strong>最近消息：</strong>{catalogStatus?.last_message || '-'}</div>
        <div><strong>最近全量重建：</strong>{formatDateTime(catalogStatus?.last_full_rebuild_at)}</div>
      </div>

      {actionMessage && (
        <div className={`dinsar-catalog-message tone-${actionTone}`}>
          {actionMessage}
        </div>
      )}

      {!compact && (
        <div className="dinsar-catalog-manage">
          <div className="dinsar-catalog-manage-copy">
            <strong>手动发布与目录重建</strong>
            <p>
              这里用于把既有结果目录重新发布为标准结果包，并按最新规则重建目录索引。
              同一对影像的 ENVI/SARscape、LandSAR、Gamma/PyINT 结果会按任务聚合展示，底层仍依赖 `engine_code` 与 `run_key` 分别登记。
            </p>
          </div>
          <div className="dinsar-catalog-manage-form">
            <textarea
              value={sourceDirectoriesText}
              onChange={(event) => setSourceDirectoriesText(event.target.value)}
              placeholder="输入一个或多个结果源目录，支持换行、逗号或分号分隔"
              disabled={readOnly || actionLoading}
            />
            <input
              value={publishRoot}
              onChange={(event) => setPublishRoot(event.target.value)}
              placeholder="可选：自定义标准结果包根目录，留空使用系统配置"
              disabled={readOnly || actionLoading}
            />
            <div className="dinsar-catalog-manage-actions">
              <button
                type="button"
                className="primary"
                onClick={handleQueuePublish}
                disabled={readOnly || actionLoading || sourceDirectories.length === 0}
              >
                {actionLoading ? '处理中...' : '发布结果包并重建'}
              </button>
              <button
                type="button"
                onClick={handleQueueRebuild}
                disabled={readOnly || actionLoading}
              >
                仅重建目录
              </button>
            </div>
          </div>
        </div>
      )}

      <div className={`dinsar-catalog-workspace ${compact ? 'compact' : ''}`}>
        <aside className="dinsar-catalog-list-card">
          <div className="dinsar-catalog-card-head">
            <div>
              <strong>结果包列表</strong>
              <span>
                {loading ? '加载中...' : `当前展示 ${productPairs.length} 个任务`}
              </span>
            </div>
            {queryApplied && <StatusPill label={`检索: ${queryApplied}`} tone="info" />}
          </div>

          <div className="dinsar-catalog-filter-bar">
            <label className="dinsar-catalog-filter-field">
              <span>生产引擎</span>
              <select value={engineFilter} onChange={(event) => setEngineFilter(event.target.value)}>
                <option value={DINSAR_ENGINE_ALL}>全部引擎</option>
                {engineOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="dinsar-catalog-filter-field search">
              <span>检索</span>
              <input
                value={queryDraft}
                onChange={(event) => setQueryDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault();
                    handleApplyFilters();
                  }
                }}
                placeholder="搜索任务名 / pair / run / 引擎"
              />
            </label>

            <div className="dinsar-catalog-filter-actions">
              <button type="button" onClick={handleApplyFilters}>查询</button>
              <button type="button" onClick={handleResetFilters}>重置</button>
            </div>
          </div>

          {productPairs.length === 0 ? (
            <div className="dinsar-catalog-empty">
              {loading ? '正在加载结果包...' : '当前筛选条件下没有结果包。'}
            </div>
          ) : (
            <div className="dinsar-catalog-list">
              {productPairs.map((item) => {
                const tone = STATUS_TONE_MAP[item.status] || 'neutral';
                const satelliteFamily = inferSatelliteFamilyFromResultLike(item);
                const rowKey = item.pair_key || `pair:${item.primary_product_id}`;
                return (
                  <button
                    key={rowKey}
                    type="button"
                    className={`dinsar-catalog-list-item ${selectedPairKey === rowKey ? 'active' : ''}`}
                    onClick={() => {
                      setSelectedPairKey(rowKey);
                      setSelectedProductId(item.primary_product_id || null);
                    }}
                  >
                    <div className="dinsar-catalog-list-item-top">
                      <strong>{item.task_alias || item.task_name || item.pair_key || '未命名任务'}</strong>
                      <StatusPill label={item.status || 'UNKNOWN'} tone={tone} />
                    </div>
                    <div className="dinsar-catalog-list-item-badges">
                      {satelliteFamily && (
                        <span className="dinsar-engine-badge tone-unknown">{formatSatelliteFamilyLabel(satelliteFamily)}</span>
                      )}
                      <span>{formatDateTime(item.latest_published_at)}</span>
                    </div>
                    <div className="dinsar-engine-result-row">
                      {['sarscape', 'landsar', 'pyint'].map((engineCode) => {
                        const result = item.engine_results?.[engineCode] || {};
                        const engineMeta = getDinsarEngineMeta(engineCode);
                        return (
                          <span
                            key={engineCode}
                            className={`dinsar-engine-result-chip tone-${engineStatusTone(result.status)}`}
                          >
                            {engineMeta.shortLabel}: {result.status || 'missing'}
                          </span>
                        );
                      })}
                    </div>
                    <div className="dinsar-catalog-list-item-meta">
                      已有结果 {item.available_engine_count || 0} / 3，ready {item.ready_engine_count || 0}
                    </div>
                    <div className="dinsar-catalog-list-item-meta">
                      {item.pair_key || '-'}
                    </div>
                    {(item.selection_strategy || item.network_run_id || item.network_edge_id != null) && (
                      <div className="dinsar-catalog-list-item-trace">
                        {(item.selection_strategy || 'trace')}
                        {item.network_edge_id != null ? ` / edge ${item.network_edge_id}` : ''}
                        {item.network_run_id ? ` / ${item.network_run_id}` : ''}
                      </div>
                    )}
                  </button>
                );
              })}
            </div>
          )}
        </aside>

        <section className="dinsar-catalog-detail-card">
          <div className="dinsar-catalog-card-head">
            <div>
              <strong>结果包详情</strong>
              <span>查看选中结果的发布信息、配对溯源与资产健康</span>
            </div>
          </div>

          {!selectedProductId ? (
            <div className="dinsar-catalog-empty">请选择一个已有结果的任务查看详情。</div>
          ) : detailLoading || !selectedProduct ? (
            <div className="dinsar-catalog-empty">正在加载详情...</div>
          ) : selectedProduct?.error ? (
            <div className="dinsar-catalog-empty error">{selectedProduct.error}</div>
          ) : (
            <div className="dinsar-catalog-detail-body">
              {(() => {
                const satelliteFamily = inferSatelliteFamilyFromResultLike(selectedProduct?.profile || selectedProduct);
                return (
              <div className="dinsar-catalog-hero">
                <div className="dinsar-catalog-preview-frame">
                  <img
                    src={`${previewBaseUrl}/dinsar-products/${selectedProduct.id}/preview`}
                    alt={selectedProduct.display_name || selectedProduct.product_id}
                  />
                </div>

                <div className="dinsar-catalog-hero-meta">
                  <div className="dinsar-catalog-hero-title-row">
                    <div>
                      <h4>{selectedProduct.display_name || selectedProduct.product_id}</h4>
                      <p>{selectedProduct.task_alias || selectedProduct.task_name || '未命名任务'}</p>
                    </div>
                    <div className="dinsar-catalog-hero-badges">
                      <span className={`dinsar-engine-badge tone-${selectedProductEngine.tone}`}>
                        {selectedProductEngine.shortLabel}
                      </span>
                      {satelliteFamily && (
                        <span className="dinsar-engine-badge tone-unknown">
                          {formatSatelliteFamilyLabel(satelliteFamily)}
                        </span>
                      )}
                      <StatusPill label={selectedProduct.status || 'UNKNOWN'} tone={selectedStatusTone} />
                    </div>
                  </div>

                  <div className="dinsar-catalog-kv-grid">
                    <MetaField label="产品编号" value={selectedProduct.product_id} multiline />
                    <MetaField label="配对标识" value={selectedProduct.pair_key} multiline />
                    <MetaField label="场景配对 UID" value={selectedProduct.pair_uid} multiline />
                    <MetaField label="运行标识" value={selectedProduct.run_key} multiline />
                    <MetaField label="生产配置" value={selectedProduct.profile_code} />
                    <MetaField label="健康状态" value={selectedProduct.health_status} />
                    <MetaField label="主文件" value={selectedProduct.primary_asset_path} multiline />
                    <MetaField label="源文件" value={selectedProduct.source_primary_path} multiline />
                    <MetaField label="结果包目录" value={selectedProduct.publish_dir} multiline />
                  </div>
                </div>
              </div>
                );
              })()}

              <div className="dinsar-catalog-detail-grid">
                <div className="dinsar-catalog-section-card">
                  <div className="dinsar-catalog-section-title">时空概览</div>
                  <MetaField label="主影像日期" value={selectedProduct.profile?.master_imaging_date} />
                  <MetaField label="从影像日期" value={selectedProduct.profile?.slave_imaging_date} />
                  <MetaField label="时间基线" value={selectedProduct.profile?.time_baseline_days} />
                  <MetaField label="footprint 中心距" value={selectedProduct.profile?.scene_center_distance_meters ?? selectedProduct.profile?.spatial_baseline_meters} />
                </div>
                <div className="dinsar-catalog-section-card">
                  <div className="dinsar-catalog-section-title">空间范围</div>
                  <MetaField label="最小坐标" value={`${selectedProduct.min_lon ?? '-'}, ${selectedProduct.min_lat ?? '-'}`} />
                  <MetaField label="最大坐标" value={`${selectedProduct.max_lon ?? '-'}, ${selectedProduct.max_lat ?? '-'}`} />
                  <MetaField label="登记时间" value={formatDateTime(selectedProduct.registered_at)} />
                  <MetaField label="发布时间" value={formatDateTime(selectedProduct.published_at)} />
                </div>
              </div>

              <div className="dinsar-catalog-section-card">
                <div className="dinsar-catalog-section-title">配对追踪</div>
                {!selectedPairingTrace?.network_run_id ? (
                  <div className="dinsar-catalog-empty inline">当前结果未携带完整的配对网络追踪信息。</div>
                ) : (
                  <div className="dinsar-catalog-detail-grid">
                    <div className="dinsar-catalog-section-card nested">
                      <MetaField label="network_run_id" value={selectedPairingTrace.network_run_id} multiline />
                      <MetaField label="network_edge_id" value={selectedPairingTrace.network_edge_id} />
                      <MetaField label="pair_uid" value={selectedPairingTrace.pair_uid} multiline />
                      <MetaField label="选择策略" value={selectedPairingTrace.selection_strategy} />
                      <MetaField label="策略版本" value={selectedPairingTrace.policy_version} />
                    </div>
                    <div className="dinsar-catalog-section-card nested">
                      <MetaField label="网络记录" value={selectedPairingNetwork?.run_found ? '已找到' : '未找到'} />
                      <MetaField label="边记录" value={selectedPairingNetwork?.edge_found ? '已找到' : '未找到'} />
                      <MetaField label="运行状态" value={selectedPairingRun?.status} />
                      <MetaField label="候选边数" value={selectedPairingRun?.candidate_count} />
                      <MetaField label="入选边数" value={selectedPairingRun?.selected_edge_count} />
                      <MetaField label="告警数" value={selectedPairingRun?.warning_count} />
                    </div>
                    <div className="dinsar-catalog-section-card nested">
                      <MetaField label="edge_rank" value={selectedPairingEdge?.edge_rank} />
                      <MetaField label="selection_reason" value={selectedPairingEdge?.selection_reason} multiline />
                      <MetaField label="selection_score" value={selectedPairingEdge?.selection_score} />
                      <MetaField label="reference_edge" value={selectedPairingEdge?.is_reference_edge ? '是' : '否'} />
                      <MetaField label="metric_cache_ref_id" value={selectedPairingEdge?.metric_cache_ref_id} />
                    </div>
                    <div className="dinsar-catalog-section-card nested">
                      <MetaField label="主从日期" value={`${selectedPairingMetric?.master_imaging_date || '-'} / ${selectedPairingMetric?.slave_imaging_date || '-'}`} />
                      <MetaField label="主从卫星" value={`${selectedPairingMetric?.master_satellite || '-'} / ${selectedPairingMetric?.slave_satellite || '-'}`} />
                      <MetaField label="主从模式" value={`${selectedPairingMetric?.master_imaging_mode || '-'} / ${selectedPairingMetric?.slave_imaging_mode || '-'}`} />
                      <MetaField label="主从极化" value={`${selectedPairingMetric?.master_polarization || '-'} / ${selectedPairingMetric?.slave_polarization || '-'}`} />
                      <MetaField label="时间基线" value={selectedPairingMetric?.time_baseline_days} />
                      <MetaField label="footprint 中心距" value={selectedPairingMetric?.scene_center_distance_meters ?? selectedPairingMetric?.spatial_baseline_meters} />
                    </div>
                  </div>
                )}
              </div>

              <div className="dinsar-catalog-detail-grid">
                <div className="dinsar-catalog-section-card">
                  <div className="dinsar-catalog-section-title">资产列表 ({selectedAssets.length})</div>
                  {selectedAssets.length === 0 ? (
                    <div className="dinsar-catalog-empty inline">暂无资产记录。</div>
                  ) : (
                    <div className="dinsar-catalog-asset-list">
                      {selectedAssets.map((asset) => (
                        <div key={asset.id} className={`dinsar-catalog-asset-item ${asset.exists_flag ? 'ok' : 'missing'}`}>
                          <div className="dinsar-catalog-asset-top">
                            <strong>{asset.asset_role}</strong>
                            <span>{asset.exists_flag ? '文件存在' : '文件缺失'}</span>
                          </div>
                          <div>{asset.asset_name}</div>
                          <div className="break-all">{asset.absolute_path}</div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                <div className="dinsar-catalog-section-card">
                  <div className="dinsar-catalog-section-title">问题列表 ({selectedIssues.length})</div>
                  {selectedIssues.length === 0 ? (
                    <div className="dinsar-catalog-empty inline ok">当前没有登记问题。</div>
                  ) : (
                    <div className="dinsar-catalog-issue-list">
                      {selectedIssues.map((issue) => (
                        <div key={issue.id} className={`dinsar-catalog-issue-item ${String(issue.severity || '').toUpperCase() === 'ERROR' ? 'error' : 'warn'}`}>
                          <div className="dinsar-catalog-issue-top">
                            <strong>{issue.issue_code}</strong>
                            <span>{issue.severity}</span>
                          </div>
                          <div>{issue.message}</div>
                          {issue.repair_action && (
                            <div className="dinsar-catalog-issue-action">
                              建议修复动作：{issue.repair_action}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              <div className="dinsar-catalog-section-card">
                <div className="dinsar-catalog-section-title">中间文件清理计划</div>
                <div className="dinsar-catalog-cleanup-head">
                  <div>
                    <MetaField label="当前能力" value="dry-run，只生成计划，不执行删除" />
                  </div>
                  <button type="button" onClick={loadCleanupPlan} disabled={cleanupPlanLoading}>
                    {cleanupPlanLoading ? '计算中...' : '生成计划'}
                  </button>
                </div>
                {!cleanupPlan ? (
                  <div className="dinsar-catalog-empty inline">尚未生成清理计划。</div>
                ) : cleanupPlan.error ? (
                  <div className="dinsar-catalog-empty inline error">{cleanupPlan.error}</div>
                ) : (
                  <div className="dinsar-catalog-cleanup-plan">
                    <div className="dinsar-catalog-detail-grid">
                      <div className="dinsar-catalog-section-card nested">
                        <MetaField label="可清理" value={cleanupPlan.deletable ? '是' : '否'} />
                        <MetaField label="候选项" value={cleanupPlan.candidate_count} />
                        <MetaField label="候选大小" value={formatBytes(cleanupPlan.total_size_bytes)} />
                      </div>
                      <div className="dinsar-catalog-section-card nested">
                        <MetaField label="manifest" value={cleanupPlan.checks?.manifest_exists ? '存在' : '缺失'} />
                        <MetaField label="必要资产" value={cleanupPlan.checks?.required_assets_ok ? '完整' : '缺失'} />
                        <MetaField label="当前引擎" value={cleanupPlan.checks?.current_engine ? '是' : '否'} />
                      </div>
                    </div>
                    {Array.isArray(cleanupPlan.blockers) && cleanupPlan.blockers.length > 0 && (
                      <div className="dinsar-catalog-issue-list">
                        {cleanupPlan.blockers.map((blocker) => (
                          <div key={blocker} className="dinsar-catalog-issue-item warn">
                            {blocker}
                          </div>
                        ))}
                      </div>
                    )}
                    <div className="dinsar-catalog-asset-list">
                      {(cleanupPlan.candidates || []).map((candidate) => (
                        <div key={candidate.path} className={`dinsar-catalog-asset-item ${candidate.exists ? 'ok' : 'missing'}`}>
                          <div className="dinsar-catalog-asset-top">
                            <strong>{candidate.reason}</strong>
                            <span>{candidate.exists ? formatBytes(candidate.size_bytes) : '不存在'}</span>
                          </div>
                          <div className="break-all">{candidate.path}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
