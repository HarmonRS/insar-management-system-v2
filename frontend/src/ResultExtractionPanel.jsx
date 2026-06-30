import { useEffect, useMemo, useState } from 'react';

import { getDinsarResults } from './api/dinsar';
import { listSbasInsarProducts } from './api/sbasInsarProducts';
import {
  createResultDelivery,
  getResultDeliveryArchiveUrl,
  getResultDeliveryDownloadUrl,
  getResultDeliveryManifestUrl,
  listResultDeliveryCatalog,
  listResultDeliveries,
} from './api/resultDeliveries';
import { getDinsarEngineMeta } from './utils/dinsarEngines';

const PAGE_SIZE = 100;

const PRODUCT_CHANNELS = [
  {
    key: 'dinsar',
    group: 'InSAR 成果',
    label: 'D-InSAR 结果',
    state: 'ready',
    stateText: '可交付',
    description: '从已登记的 D-InSAR catalog 中选择成果，后台生成受控交付包并下载到本地。',
  },
  {
    key: 'sbas',
    group: 'InSAR 成果',
    label: 'SBAS-InSAR 结果',
    state: 'planned',
    stateText: '目录可查',
    description: 'SBAS 成果目录已接入，本阶段只展示目录状态，交付打包后续接入。',
  },
  {
    key: 'lt1_ortho',
    group: '正射成果',
    label: 'LT-1 正射结果',
    state: 'ready',
    stateText: '可交付',
    description: '服务器生产的 LT-1 分析就绪正射 GeoTIFF 已接入交付，可打包下载到本地。',
  },
  {
    key: 's1_ortho',
    group: '正射成果',
    label: 'Sentinel-1 正射结果',
    state: 'placeholder',
    stateText: '待接入',
    description: 'Sentinel-1 正射生产尚未接入，当前只保留交付通道占位。',
  },
  {
    key: 'gf3_ortho',
    group: '正射成果',
    label: 'GF3 SARscape _geo',
    state: 'ready',
    stateText: '可交付',
    description: '已登记的 GF3 SARscape 标准化正射成品可直接创建交付包。',
  },
];

function formatNumber(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '-';
  return new Intl.NumberFormat('zh-CN').format(number);
}

function formatBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let size = bytes;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${size.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function normalizeItems(payload) {
  return Array.isArray(payload?.items) ? payload.items : [];
}

function extractTotal(payload, fallback = 0) {
  const total = Number(payload?.total);
  return Number.isFinite(total) ? total : fallback;
}

function resultDisplayName(result) {
  return String(result?.display_name || result?.name || result?.task_alias || result?.task_name || result?.product_id || `#${result?.id || ''}`).trim();
}

function resultItemId(result) {
  return result?.item_id ?? result?.id;
}

function selectionKey(channelKey, id) {
  return `${channelKey}:${id}`;
}

function resultDateText(result) {
  const name = resultDisplayName(result);
  if (result?.imaging_date) return String(result.imaging_date);
  const matches = name.match(/(\d{8})/g);
  if (matches?.length >= 2) return `${matches[0]} / ${matches[1]}`;
  if (matches?.length === 1) return matches[0];
  return '-';
}

function channelMetricLabel(channelKey) {
  return {
    dinsar: 'D-InSAR 可交付',
    sbas: 'SBAS 目录',
    lt1_ortho: 'LT-1 正射',
    gf3_ortho: 'GF3 正射',
    s1_ortho: 'Sentinel-1 正射',
  }[channelKey] || '可交付结果';
}

function channelListTitle(channelKey) {
  return {
    dinsar: 'D-InSAR 结果列表',
    lt1_ortho: 'LT-1 正射结果列表',
    gf3_ortho: 'GF3 正射结果列表',
  }[channelKey] || '结果列表';
}

function channelEmptyText(channelKey) {
  return {
    dinsar: '当前条件下没有可交付的 D-InSAR 结果。',
    lt1_ortho: '当前条件下没有可交付的 LT-1 正射结果。',
    gf3_ortho: '当前条件下没有可交付的 GF3 正射结果。',
  }[channelKey] || '当前条件下没有可交付结果。';
}

function channelCreateTitle(channelKey) {
  return {
    dinsar: 'D-InSAR 成果交付',
    lt1_ortho: 'LT-1 正射成果交付',
    gf3_ortho: 'GF3 正射成果交付',
  }[channelKey] || '成果交付';
}

function channelCreateSubtitle(channelKey) {
  return {
    dinsar: '选择已登记结果并生成下载包',
    lt1_ortho: '选择服务器已生产正射 GeoTIFF 并生成下载包',
    gf3_ortho: '选择已登记 GF3 SARscape 正射成品并生成下载包',
  }[channelKey] || '选择结果并生成下载包';
}

function itemMetaText(item, channelKey) {
  if (channelKey === 'dinsar') {
    return `${resultDateText(item)} · ${item.pair_key || item.product_id || '-'}`;
  }
  const parts = [
    item.imaging_date || resultDateText(item),
    item.polarization,
    item.pixel_size_m ? `${item.pixel_size_m} m` : null,
    item.product_id,
  ].filter(Boolean);
  return parts.join(' · ') || '-';
}

function itemStatusText(item, channelKey) {
  if (channelKey === 'dinsar') {
    return item.is_cached ? '预览就绪' : '预览待建';
  }
  if (item.file_size) return formatBytes(item.file_size);
  return item.health_status || item.status || 'READY';
}

function stateClass(state) {
  if (state === 'ready') return 'ready';
  if (state === 'planned') return 'planned';
  return 'pending';
}

function statusText(status) {
  const value = String(status || '').toUpperCase();
  return {
    PENDING: '排队中',
    RUNNING: '生成中',
    READY: '可下载',
    FAILED: '失败',
    CANCELLED: '已取消',
    EXPIRED: '已过期',
  }[value] || value || '-';
}

function statusClass(status) {
  const value = String(status || '').toUpperCase();
  if (value === 'READY') return 'ready';
  if (value === 'RUNNING' || value === 'PENDING') return 'planned';
  return 'pending';
}

export default function ResultExtractionPanel({ readOnly = false }) {
  const [activeChannel, setActiveChannel] = useState('dinsar');
  const [dinsarPayload, setDinsarPayload] = useState({ items: [], total: 0 });
  const [sbasPayload, setSbasPayload] = useState({ items: [], total: 0 });
  const [lt1Payload, setLt1Payload] = useState({ items: [], total: 0 });
  const [gf3Payload, setGf3Payload] = useState({ items: [], total: 0 });
  const [deliveriesPayload, setDeliveriesPayload] = useState({ items: [], total: 0 });
  const [loading, setLoading] = useState(true);
  const [deliveryLoading, setDeliveryLoading] = useState(false);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [selectedIds, setSelectedIds] = useState(() => new Set());
  const [packageMode, setPackageMode] = useState('directory');
  const [includeChecksums, setIncludeChecksums] = useState(true);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState('');
  const [createResult, setCreateResult] = useState(null);

  const selectedChannel = PRODUCT_CHANNELS.find(channel => channel.key === activeChannel) || PRODUCT_CHANNELS[0];

  const loadDeliveries = async () => {
    setDeliveryLoading(true);
    try {
      const payload = await listResultDeliveries({ mine: true, limit: 20, offset: 0 });
      const items = normalizeItems(payload);
      setDeliveriesPayload({ ...payload, items, total: extractTotal(payload, items.length) });
    } catch (err) {
      setCreateError(err?.response?.data?.detail || err.message || '交付包列表加载失败');
    } finally {
      setDeliveryLoading(false);
    }
  };

  const loadCatalogs = async () => {
    setLoading(true);
    setError('');
    try {
      const [dinsarData, sbasData, deliveryData] = await Promise.all([
        getDinsarResults({ limit: PAGE_SIZE, offset: 0 }),
        listSbasInsarProducts({ limit: 30, offset: 0 }),
        listResultDeliveries({ mine: true, limit: 20, offset: 0 }),
      ]);
      const [lt1Data, gf3Data] = await Promise.all([
        listResultDeliveryCatalog('lt1_ortho', { limit: PAGE_SIZE, offset: 0 }),
        listResultDeliveryCatalog('gf3_ortho', { limit: PAGE_SIZE, offset: 0 }),
      ]);
      const dinsarItems = normalizeItems(dinsarData);
      setDinsarPayload({ ...dinsarData, items: dinsarItems, total: extractTotal(dinsarData, dinsarItems.length) });
      const sbasItems = normalizeItems(sbasData);
      setSbasPayload({ ...sbasData, items: sbasItems, total: extractTotal(sbasData, sbasItems.length) });
      const lt1Items = normalizeItems(lt1Data);
      setLt1Payload({ ...lt1Data, items: lt1Items, total: extractTotal(lt1Data, lt1Items.length) });
      const gf3Items = normalizeItems(gf3Data);
      setGf3Payload({ ...gf3Data, items: gf3Items, total: extractTotal(gf3Data, gf3Items.length) });
      const deliveryItems = normalizeItems(deliveryData);
      setDeliveriesPayload({ ...deliveryData, items: deliveryItems, total: extractTotal(deliveryData, deliveryItems.length) });
      setSelectedIds(new Set(dinsarItems.map(item => resultItemId(item)).filter(id => id !== undefined && id !== null).map(id => selectionKey('dinsar', id))));
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || '结果目录加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadCatalogs();
  }, []);

  useEffect(() => {
    const hasActiveDelivery = (deliveriesPayload.items || []).some(item => {
      const status = String(item.status || '').toUpperCase();
      return status === 'PENDING' || status === 'RUNNING';
    });
    if (!hasActiveDelivery) return undefined;
    const timer = window.setInterval(() => {
      loadDeliveries();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [deliveriesPayload.items]);

  const activePayload = activeChannel === 'lt1_ortho'
    ? lt1Payload
    : activeChannel === 'gf3_ortho'
      ? gf3Payload
      : activeChannel === 'sbas'
        ? sbasPayload
        : activeChannel === 's1_ortho'
          ? { items: [], total: 0 }
          : dinsarPayload;
  const activeItems = activePayload.items || [];

  const filteredResults = useMemo(() => {
    const value = query.trim().toLowerCase();
    const items = activeItems;
    if (!value) return items;
    return items.filter(item => {
      const haystack = [
        item.display_name,
        item.name,
        item.task_name,
        item.task_alias,
        item.pair_key,
        item.product_id,
        item.engine_code,
        item.profile_code,
        item.imaging_date,
        item.polarization,
        item.file_path,
        item.primary_asset_path,
        item.publish_dir,
      ].filter(Boolean).join(' ').toLowerCase();
      return haystack.includes(value);
    });
  }, [activeItems, query]);

  const filteredIds = useMemo(
    () => filteredResults.map(item => resultItemId(item)).filter(id => id !== undefined && id !== null),
    [filteredResults],
  );

  const filteredKeys = useMemo(
    () => filteredIds.map(id => selectionKey(activeChannel, id)),
    [activeChannel, filteredIds],
  );

  const selectedCountInView = filteredKeys.filter(key => selectedIds.has(key)).length;
  const allVisibleSelected = filteredIds.length > 0 && selectedCountInView === filteredIds.length;
  const latestDelivery = deliveriesPayload.items?.[0] || null;

  const readyOrthoCount = Number(lt1Payload.total || 0) + Number(gf3Payload.total || 0);
  const currentCatalogTotal = Number(dinsarPayload.total || 0) + Number(sbasPayload.total || 0) + readyOrthoCount;

  const metrics = [
    {
      label: channelMetricLabel(activeChannel),
      value: activePayload.total,
      note: `当前载入 ${filteredResults.length}/${activeItems.length} 条`,
      tone: 'primary',
    },
    {
      label: '我的交付包',
      value: deliveriesPayload.total,
      note: latestDelivery ? `最近状态：${statusText(latestDelivery.status)}` : '暂无交付记录',
      tone: 'neutral',
    },
    {
      label: '当前接入目录',
      value: currentCatalogTotal,
      note: 'D-InSAR + SBAS + LT-1/GF3 正射',
      tone: 'neutral',
    },
    {
      label: '可交付正射',
      value: readyOrthoCount,
      note: 'LT-1 与 GF3 已接入，S1 预留',
      tone: 'neutral',
    },
  ];

  const toggleOne = (id) => {
    const key = selectionKey(activeChannel, id);
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  };

  const toggleVisible = () => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (allVisibleSelected) {
        filteredKeys.forEach(key => next.delete(key));
      } else {
        filteredKeys.forEach(key => next.add(key));
      }
      return next;
    });
  };

  const handleCreateDelivery = async () => {
    const ids = filteredIds.filter(id => selectedIds.has(selectionKey(activeChannel, id)));
    if (ids.length === 0) {
      setCreateError(`请至少选择一条${selectedChannel.label}。`);
      return;
    }
    setCreating(true);
    setCreateError('');
    setCreateResult(null);
    try {
      const payload = {
        channel: activeChannel,
        package_mode: packageMode,
        include_checksums: includeChecksums,
      };
      if (activeChannel === 'dinsar') {
        payload.compat_result_ids = ids;
      } else {
        payload.item_ids = ids;
      }
      const response = await createResultDelivery(payload);
      setCreateResult(response);
      await loadDeliveries();
    } catch (err) {
      setCreateError(err?.response?.data?.detail || err.message || '成果交付任务创建失败');
    } finally {
      setCreating(false);
    }
  };

  const renderDeliveryDownloads = (delivery) => {
    if (String(delivery.status || '').toUpperCase() !== 'READY') {
      return null;
    }
    const items = Array.isArray(delivery.items) ? delivery.items : [];
    return (
      <div className="result-delivery-downloads">
        <a href={getResultDeliveryManifestUrl(delivery.delivery_id)} target="_blank" rel="noreferrer">manifest</a>
        {delivery.zip_path && (
          <a href={getResultDeliveryArchiveUrl(delivery.delivery_id)} target="_blank" rel="noreferrer">zip</a>
        )}
        {items.slice(0, 3).map(item => (
          <a key={item.id} href={getResultDeliveryDownloadUrl(delivery.delivery_id, item.id)} target="_blank" rel="noreferrer">
            {item.relative_path?.split(/[\\/]/).pop() || `文件 ${item.id}`}
          </a>
        ))}
      </div>
    );
  };

  const renderDeliveryList = () => (
    <section className="result-delivery-panel">
      <div className="result-extraction-list-head">
        <span>我的交付包</span>
        <strong>{deliveryLoading ? '刷新中' : `${deliveriesPayload.items.length}/${deliveriesPayload.total}`}</strong>
      </div>
      <div className="result-delivery-list">
        {deliveriesPayload.items.length === 0 ? (
          <div className="result-extraction-empty">还没有创建过成果交付包。</div>
        ) : (
          deliveriesPayload.items.map(delivery => (
            <div key={delivery.delivery_id} className="result-delivery-row">
              <div className="result-delivery-row-main">
                <strong>{delivery.delivery_id}</strong>
                <span>
                  {delivery.channel}
                  {' · '}
                  {delivery.package_mode}
                  {' · '}
                  {formatNumber(delivery.item_count)} 文件
                  {' · '}
                  {formatBytes(delivery.copied_bytes || delivery.total_bytes)}
                </span>
                {delivery.error_message && <em>{delivery.error_message}</em>}
                {renderDeliveryDownloads(delivery)}
              </div>
              <span className={`result-extraction-state ${statusClass(delivery.status)}`}>
                {statusText(delivery.status)}
              </span>
            </div>
          ))
        )}
      </div>
    </section>
  );

  const renderReadyWorkspace = () => (
    <section className="result-extraction-main-card">
      <div className="result-extraction-card-head">
        <div>
          <span>{channelCreateTitle(activeChannel)}</span>
          <strong>{channelCreateSubtitle(activeChannel)}</strong>
        </div>
        <button type="button" onClick={loadCatalogs} disabled={loading || creating}>
          刷新目录
        </button>
      </div>

      <div className="result-extraction-controls">
        <label className="result-extraction-field">
          <span>结果检索</span>
          <input
            type="text"
            value={query}
            onChange={event => setQuery(event.target.value)}
            placeholder="任务名、日期、产品号、极化、引擎"
            disabled={loading || creating}
          />
        </label>
        <label className="result-extraction-field">
          <span>交付模式</span>
          <select value={packageMode} onChange={event => setPackageMode(event.target.value)} disabled={loading || creating}>
            <option value="directory">目录交付</option>
            <option value="zip">压缩包交付</option>
          </select>
        </label>
        <label className="result-extraction-checkbox">
          <input
            type="checkbox"
            checked={includeChecksums}
            onChange={event => setIncludeChecksums(event.target.checked)}
            disabled={loading || creating}
          />
          <span>生成 SHA256 校验</span>
        </label>
        <div className="result-extraction-action-stack">
          <button type="button" onClick={toggleVisible} disabled={loading || creating || filteredIds.length === 0}>
            {allVisibleSelected ? '取消本页' : '选择本页'}
          </button>
          <button
            type="button"
            className="primary"
            onClick={handleCreateDelivery}
            disabled={creating || loading || selectedCountInView === 0}
          >
            {creating ? '正在提交' : `生成 ${selectedCountInView} 项`}
          </button>
        </div>
      </div>

      <div className="result-extraction-hint">
        普通用户不再输入服务器路径。系统会在受控交付区生成临时包，完成后可下载到本地；生产结果入库和系统清理由管理员处理。
      </div>

      {error && <div className="result-extraction-message error">{error}</div>}
      {createError && <div className="result-extraction-message error">{createError}</div>}
      {createResult && (
        <div className="result-extraction-message success">
          <strong>交付任务已创建</strong>
          <span>任务 ID：{createResult.task_id || '-'}，交付包：{createResult.delivery_id}</span>
          <code>{createResult.delivery_dir}</code>
        </div>
      )}

      <div className="result-extraction-workspace-split">
        <div className="result-extraction-result-column">
          <div className="result-extraction-list-head">
            <span>{channelListTitle(activeChannel)}</span>
            <strong>{selectedCountInView}/{filteredResults.length}</strong>
          </div>
          <div className="result-extraction-result-list">
            {loading ? (
              <div className="result-extraction-empty">正在加载成果目录...</div>
            ) : filteredResults.length === 0 ? (
              <div className="result-extraction-empty">{channelEmptyText(activeChannel)}</div>
            ) : (
              filteredResults.map(result => {
                const id = resultItemId(result);
                const engineMeta = activeChannel === 'dinsar'
                  ? getDinsarEngineMeta(result.engine_code)
                  : { tone: 'landsar', shortLabel: result.engine_code || result.profile_code || 'ORTHO' };
                return (
                  <label key={`${activeChannel}-${id}`} className="result-extraction-result-row">
                    <input
                      type="checkbox"
                      checked={selectedIds.has(selectionKey(activeChannel, id))}
                      onChange={() => toggleOne(id)}
                      disabled={creating}
                    />
                    <span className="result-extraction-result-main">
                      <strong title={result.file_path || result.primary_asset_path || resultDisplayName(result)}>{resultDisplayName(result)}</strong>
                      <span>{itemMetaText(result, activeChannel)}</span>
                    </span>
                    <span className={`dinsar-engine-badge tone-${engineMeta.tone}`}>{engineMeta.shortLabel}</span>
                    <span className="result-extraction-status-chip">{itemStatusText(result, activeChannel)}</span>
                  </label>
                );
              })
            )}
          </div>
        </div>
        {renderDeliveryList()}
      </div>
    </section>
  );

  const renderPlaceholderWorkspace = () => (
    <section className="result-extraction-main-card">
      <div className="result-extraction-placeholder">
        <span className={`result-extraction-state ${stateClass(selectedChannel.state)}`}>
          {selectedChannel.stateText}
        </span>
        <strong>{selectedChannel.label}</strong>
        <p>{selectedChannel.description}</p>
        <div className="result-extraction-contract">
          <div>
            <span>登记入口</span>
            <strong>{selectedChannel.key === 'sbas' ? 'SBAS-InSAR 成果目录' : '生产结果 catalog'}</strong>
          </div>
          <div>
            <span>交付接口</span>
            <strong>{selectedChannel.state === 'ready' ? '已接入' : '待实现'}</strong>
          </div>
          <div>
            <span>用户权限</span>
            <strong>登录用户自助申请</strong>
          </div>
        </div>
        {selectedChannel.key === 'sbas' && (
          <div className="result-extraction-sbas-sample">
            <span>当前 SBAS 目录样例</span>
            {sbasPayload.items.length === 0 ? (
              <p>暂无可展示的 SBAS-InSAR 成果。</p>
            ) : (
              sbasPayload.items.slice(0, 5).map(item => (
                <div key={item.id || item.product_id} className="result-extraction-sbas-row">
                  <strong>{item.product_id || item.name || `#${item.id}`}</strong>
                  <span>{item.status || 'UNKNOWN'}</span>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </section>
  );

  return (
    <div className="result-extraction-page">
      <section className="result-extraction-hero">
        <div>
          <span>成果交付出口</span>
          <strong>结果提取工作台</strong>
          <p>
            将 D-InSAR、LT-1 正射和 GF3 正射成果交付下载统一管理。后台生成受控交付包，用户完成后下载到本地；
            SBAS 和 Sentinel-1 正射保留清晰接入状态。
          </p>
        </div>
        <div className="result-extraction-hero-meta">
          <span>D-InSAR {formatNumber(dinsarPayload.total)}</span>
          <span>LT-1 {formatNumber(lt1Payload.total)}</span>
          <span>GF3 {formatNumber(gf3Payload.total)}</span>
          <span>{readOnly ? '自助交付' : '管理员'}</span>
        </div>
      </section>

      <section className="result-extraction-metrics">
        {metrics.map(metric => (
          <div key={metric.label} className={`result-extraction-metric tone-${metric.tone}`}>
            <span>{metric.label}</span>
            <strong>{formatNumber(metric.value)}</strong>
            <p>{metric.note}</p>
          </div>
        ))}
      </section>

      <section className="result-extraction-layout">
        <aside className="result-extraction-channel-list">
          {PRODUCT_CHANNELS.map(channel => (
            <button
              key={channel.key}
              type="button"
              className={activeChannel === channel.key ? 'active' : ''}
              onClick={() => setActiveChannel(channel.key)}
            >
              <span>{channel.group}</span>
              <strong>{channel.label}</strong>
              <em className={stateClass(channel.state)}>{channel.stateText}</em>
            </button>
          ))}
        </aside>
        {selectedChannel.state === 'ready' ? renderReadyWorkspace() : renderPlaceholderWorkspace()}
      </section>
    </div>
  );
}
