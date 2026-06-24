import { useEffect, useMemo, useState } from 'react';

import { exportDinsarResults, getDinsarResults } from './api/dinsar';
import { listSbasInsarProducts } from './api/sbasInsarProducts';
import { getDinsarEngineMeta } from './utils/dinsarEngines';

const DEFAULT_TARGET_DIR = String.raw`D:\Result_Export\DInSAR`;
const PAGE_SIZE = 100;

const PRODUCT_CHANNELS = [
  {
    key: 'dinsar',
    group: 'InSAR 成果',
    label: 'D-InSAR 结果',
    state: 'ready',
    stateText: '可提取',
    description: '从已登记的 D-InSAR 成果中选择位移结果，复制到服务器指定交付目录。',
  },
  {
    key: 'sbas',
    group: 'InSAR 成果',
    label: 'SBAS-InSAR 结果',
    state: 'planned',
    stateText: '目录可查',
    description: '成果目录和预览已接入，统一提取接口待补齐。',
  },
  {
    key: 'lt1_ortho',
    group: '正射成果',
    label: 'LT-1 正射结果',
    state: 'placeholder',
    stateText: '待接入',
    description: '陆探一正射生产结果后续接入标准成果目录，并开放提取。',
  },
  {
    key: 's1_ortho',
    group: '正射成果',
    label: 'Sentinel-1 正射结果',
    state: 'placeholder',
    stateText: '待接入',
    description: 'Sentinel-1 正射生产占位，后续登记后统一提取。',
  },
  {
    key: 'gf3_ortho',
    group: '正射成果',
    label: 'GF3 SARscape _geo',
    state: 'placeholder',
    stateText: '待接入',
    description: 'GF3 外部生产后的 _geo 二进制和 WebP 已按本机登记思路设计，统一导出接口待接入。',
  },
];

function formatNumber(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '-';
  return new Intl.NumberFormat('zh-CN').format(number);
}

function normalizeItems(payload) {
  return Array.isArray(payload?.items) ? payload.items : [];
}

function extractTotal(payload, fallback = 0) {
  const total = Number(payload?.total);
  return Number.isFinite(total) ? total : fallback;
}

function resultDisplayName(result) {
  return String(result?.name || result?.task_alias || result?.task_name || result?.product_id || `#${result?.id || ''}`).trim();
}

function resultDateText(result) {
  const name = resultDisplayName(result);
  const matches = name.match(/(\d{8})/g);
  if (matches?.length >= 2) return `${matches[0]} / ${matches[1]}`;
  if (matches?.length === 1) return matches[0];
  return '-';
}

function stateClass(state) {
  if (state === 'ready') return 'ready';
  if (state === 'planned') return 'planned';
  return 'pending';
}

export default function ResultExtractionPanel({ readOnly = false }) {
  const [activeChannel, setActiveChannel] = useState('dinsar');
  const [dinsarPayload, setDinsarPayload] = useState({ items: [], total: 0 });
  const [sbasPayload, setSbasPayload] = useState({ items: [], total: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [targetDir, setTargetDir] = useState(DEFAULT_TARGET_DIR);
  const [selectedIds, setSelectedIds] = useState(() => new Set());
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState('');
  const [exportResult, setExportResult] = useState(null);

  const selectedChannel = PRODUCT_CHANNELS.find(channel => channel.key === activeChannel) || PRODUCT_CHANNELS[0];

  const loadCatalogs = async () => {
    setLoading(true);
    setError('');
    try {
      const [dinsarData, sbasData] = await Promise.all([
        getDinsarResults({ limit: PAGE_SIZE, offset: 0 }),
        listSbasInsarProducts({ limit: 30, offset: 0 }),
      ]);
      const dinsarItems = normalizeItems(dinsarData);
      setDinsarPayload({ ...dinsarData, items: dinsarItems, total: extractTotal(dinsarData, dinsarItems.length) });
      const sbasItems = normalizeItems(sbasData);
      setSbasPayload({ ...sbasData, items: sbasItems, total: extractTotal(sbasData, sbasItems.length) });
      setSelectedIds(new Set(dinsarItems.map(item => item.id).filter(id => id !== undefined && id !== null)));
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || '结果目录加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadCatalogs();
  }, []);

  const filteredDinsar = useMemo(() => {
    const value = query.trim().toLowerCase();
    const items = dinsarPayload.items || [];
    if (!value) return items;
    return items.filter(item => {
      const haystack = [
        item.name,
        item.task_name,
        item.task_alias,
        item.pair_key,
        item.product_id,
        item.engine_code,
        item.file_path,
      ].filter(Boolean).join(' ').toLowerCase();
      return haystack.includes(value);
    });
  }, [dinsarPayload.items, query]);

  const filteredIds = useMemo(
    () => filteredDinsar.map(item => item.id).filter(id => id !== undefined && id !== null),
    [filteredDinsar],
  );

  const selectedCountInView = filteredIds.filter(id => selectedIds.has(id)).length;
  const allVisibleSelected = filteredIds.length > 0 && selectedCountInView === filteredIds.length;

  const orthoPlaceholderCount = PRODUCT_CHANNELS.filter(channel => channel.group === '正射成果').length;
  const currentCatalogTotal = Number(dinsarPayload.total || 0) + Number(sbasPayload.total || 0);

  const metrics = [
    {
      label: 'D-InSAR 可提取',
      value: dinsarPayload.total,
      note: `当前载入 ${filteredDinsar.length}/${dinsarPayload.items.length} 条`,
      tone: 'primary',
    },
    {
      label: 'SBAS 目录',
      value: sbasPayload.total,
      note: '统一提取接口待接入',
      tone: 'neutral',
    },
    {
      label: '当前接入目录',
      value: currentCatalogTotal,
      note: 'D-InSAR + SBAS 已接入清单',
      tone: 'neutral',
    },
    {
      label: '正射通道',
      value: orthoPlaceholderCount,
      note: 'LT-1 / S1 / GF3 占位',
      tone: 'warning',
    },
  ];

  const toggleOne = (id) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const toggleVisible = () => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (allVisibleSelected) {
        filteredIds.forEach(id => next.delete(id));
      } else {
        filteredIds.forEach(id => next.add(id));
      }
      return next;
    });
  };

  const handleExport = async () => {
    const dir = targetDir.trim();
    if (!dir) {
      setExportError('请输入服务器目标目录。');
      return;
    }
    const ids = [...selectedIds].filter(id => filteredIds.includes(id));
    if (ids.length === 0) {
      setExportError('请至少选择一条 D-InSAR 结果。');
      return;
    }
    setExporting(true);
    setExportError('');
    setExportResult(null);
    try {
      const response = await exportDinsarResults(ids, dir);
      setExportResult(response);
    } catch (err) {
      setExportError(err?.response?.data?.detail || err.message || 'D-InSAR 结果提取失败');
    } finally {
      setExporting(false);
    }
  };

  const renderDinsarWorkspace = () => (
    <section className="result-extraction-main-card">
      <div className="result-extraction-card-head">
        <div>
          <span>D-InSAR 交付提取</span>
          <strong>选择已登记结果并复制到服务器目录</strong>
        </div>
        <button type="button" onClick={loadCatalogs} disabled={loading || exporting}>
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
            placeholder="任务名、日期、pair_key、引擎"
            disabled={loading || exporting}
          />
        </label>
        <label className="result-extraction-field result-extraction-field-wide">
          <span>服务器目标目录</span>
          <input
            type="text"
            value={targetDir}
            onChange={event => setTargetDir(event.target.value)}
            placeholder={DEFAULT_TARGET_DIR}
            disabled={loading || exporting || readOnly}
          />
        </label>
        <div className="result-extraction-action-stack">
          <button type="button" onClick={toggleVisible} disabled={loading || exporting || filteredIds.length === 0}>
            {allVisibleSelected ? '取消本页' : '选择本页'}
          </button>
          <button
            type="button"
            className="primary"
            onClick={handleExport}
            disabled={readOnly || exporting || loading || selectedCountInView === 0 || !targetDir.trim()}
          >
            {exporting ? '正在提取' : `提取 ${selectedCountInView} 项`}
          </button>
        </div>
      </div>

      <div className="result-extraction-hint">
        目标目录是服务器可访问路径，后端会按任务名或成果名创建子目录，避免直接覆盖同名成果。
      </div>

      {error && <div className="result-extraction-message error">{error}</div>}
      {exportError && <div className="result-extraction-message error">{exportError}</div>}
      {exportResult && (
        <div className="result-extraction-message success">
          <strong>提取完成</strong>
          <span>复制 {formatNumber(exportResult.copied)} 项，跳过 {formatNumber(exportResult.skipped)} 项，失败 {formatNumber(exportResult.failed)} 项。</span>
          <code>{exportResult.target_dir}</code>
        </div>
      )}

      <div className="result-extraction-list-head">
        <span>结果列表</span>
        <strong>{selectedCountInView}/{filteredDinsar.length}</strong>
      </div>
      <div className="result-extraction-result-list">
        {loading ? (
          <div className="result-extraction-empty">正在加载成果目录...</div>
        ) : filteredDinsar.length === 0 ? (
          <div className="result-extraction-empty">当前条件下没有可提取的 D-InSAR 结果。</div>
        ) : (
          filteredDinsar.map(result => {
            const id = result.id;
            const engineMeta = getDinsarEngineMeta(result.engine_code);
            return (
              <label key={id} className="result-extraction-result-row">
                <input
                  type="checkbox"
                  checked={selectedIds.has(id)}
                  onChange={() => toggleOne(id)}
                  disabled={exporting}
                />
                <span className="result-extraction-result-main">
                  <strong title={result.file_path || resultDisplayName(result)}>{resultDisplayName(result)}</strong>
                  <span>
                    {resultDateText(result)}
                    {' · '}
                    {result.pair_key || result.product_id || '-'}
                  </span>
                </span>
                <span className={`dinsar-engine-badge tone-${engineMeta.tone}`}>{engineMeta.shortLabel}</span>
                <span className="result-extraction-status-chip">{result.is_cached ? '预览就绪' : '预览待建'}</span>
              </label>
            );
          })
        )}
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
            <strong>{selectedChannel.key === 'sbas' ? 'SBAS-InSAR 成果目录' : '生产管理成果登记'}</strong>
          </div>
          <div>
            <span>提取接口</span>
            <strong>待实现</strong>
          </div>
          <div>
            <span>交付目录</span>
            <strong>服务器固定/指定路径</strong>
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
            将三类正射生产成果、D-InSAR 成果和 SBAS-InSAR 成果集中管理。当前 D-InSAR 已接入真实提取，
            其余链路先保留清晰占位，避免把未完成流程误当成可执行功能。
          </p>
        </div>
        <div className="result-extraction-hero-meta">
          <span>D-InSAR {formatNumber(dinsarPayload.total)}</span>
          <span>SBAS {formatNumber(sbasPayload.total)}</span>
          <span>{readOnly ? '只读账号' : '可执行账号'}</span>
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
        {activeChannel === 'dinsar' ? renderDinsarWorkspace() : renderPlaceholderWorkspace()}
      </section>
    </div>
  );
}
