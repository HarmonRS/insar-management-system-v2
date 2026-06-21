import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  auditSourceArchiveIntegrity,
  getAssetInventoryStatus,
  listAssetIssues,
  listOrbitAssets,
  listSourceAssets,
  scanAssetInventory,
} from './api/assets';

const PAGE_SIZE = 100;

const fmtDateTime = (value) => {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString();
};

const fmtBytes = (value) => {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return '-';
  if (n >= 1024 ** 3) return `${(n / (1024 ** 3)).toFixed(2)} GB`;
  if (n >= 1024 ** 2) return `${(n / (1024 ** 2)).toFixed(1)} MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${n} B`;
};

const StatusBadge = ({ value }) => {
  const text = String(value || '-');
  const status = text.toUpperCase();
  const tone = status === 'OK' || status === 'MATCHED' || status === 'SELECTED'
    ? 'ok'
    : status === 'WARNING' || status === 'OPEN' || status === 'MISSING'
      ? 'warn'
      : status === 'FAILED' || status === 'INACCESSIBLE' || status === 'ERROR'
        ? 'bad'
        : 'neutral';
  return <span className={`asset-badge asset-badge--${tone}`}>{text}</span>;
};

const Metric = ({ label, value, hint }) => (
  <div className="asset-metric">
    <span>{label}</span>
    <strong>{value ?? 0}</strong>
    {hint ? <small>{hint}</small> : null}
  </div>
);

const INVENTORY_FAMILIES = ['LT1', 'S1'];
const ACTIVE_ROOT_ROLES = new Set(['source_product_pool', 'orbit_asset_pool']);

export default function AssetInventoryPanel({ readOnly = false, onTaskStart }) {
  const [status, setStatus] = useState(null);
  const [sources, setSources] = useState({ items: [], total: 0, offset: 0, has_more: false });
  const [orbits, setOrbits] = useState({ items: [], total: 0, offset: 0, has_more: false });
  const [issues, setIssues] = useState({ items: [], total: 0, offset: 0, has_more: false });
  const [activeTab, setActiveTab] = useState('sources');
  const [family, setFamily] = useState('all');
  const [loading, setLoading] = useState(false);
  const [scanLoading, setScanLoading] = useState(false);
  const [auditLoading, setAuditLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  const familyParam = useMemo(() => (family === 'all' ? INVENTORY_FAMILIES.join(',') : family), [family]);

  const refresh = useCallback(async ({ sourceOffset = 0, orbitOffset = 0, issueOffset = 0 } = {}) => {
    setLoading(true);
    setError('');
    try {
      const [nextStatus, nextSources, nextOrbits, nextIssues] = await Promise.all([
        getAssetInventoryStatus(),
        listSourceAssets({ satellite_family: familyParam, limit: PAGE_SIZE, offset: sourceOffset }),
        listOrbitAssets({ satellite_family: familyParam, limit: PAGE_SIZE, offset: orbitOffset }),
        listAssetIssues({ status: 'OPEN', limit: PAGE_SIZE, offset: issueOffset }),
      ]);
      setStatus(nextStatus);
      setSources(nextSources);
      setOrbits(nextOrbits);
      setIssues(nextIssues);
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || '加载资产库存失败');
    } finally {
      setLoading(false);
    }
  }, [familyParam]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const handleScan = async (scanPayload = {}, label = '源数据/精轨资产扫描') => {
    if (readOnly || scanLoading) return;
    setScanLoading(true);
    setMessage('');
    setError('');
    const requestPayload =
      scanPayload && typeof scanPayload === 'object' && scanPayload.nativeEvent
        ? {}
        : scanPayload;
    try {
      const result = await scanAssetInventory({
        inventory_types: [],
        root_ids: [],
        bind_orbits: true,
        families: INVENTORY_FAMILIES,
        ...requestPayload,
      });
      setMessage(`资产扫描任务已入队: ${result.task_id}`);
      onTaskStart?.(result.task_id, '源数据/精轨资产扫描已入队', {
        taskType: 'SCAN_ASSET_INVENTORY',
        nonBlocking: true,
      });
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || '启动资产扫描失败');
    } finally {
      setScanLoading(false);
    }
  };

  const handleArchiveIntegrityAudit = async (auditPayload = {}, label = '压缩包完整性审计') => {
    if (readOnly || auditLoading) return;
    setAuditLoading(true);
    setMessage('');
    setError('');
    try {
      const result = await auditSourceArchiveIntegrity({
        families: family === 'all' ? INVENTORY_FAMILIES : [family],
        source_formats: [],
        asset_ids: [],
        force: false,
        ...auditPayload,
      });
      setMessage(`压缩包完整性审计任务已入队: ${result.task_id}`);
      onTaskStart?.(result.task_id, `${label}已入队`, {
        taskType: 'AUDIT_SOURCE_ARCHIVE_INTEGRITY',
        nonBlocking: true,
      });
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || '启动压缩包完整性审计失败');
    } finally {
      setAuditLoading(false);
    }
  };

  const states = (status?.states || []).filter((item) => (
    item?.enabled !== false &&
    ACTIVE_ROOT_ROLES.has(item?.root_role) &&
    item?.status !== 'NEVER_SCANNED'
  ));
  const sourceRoots = states.filter(item => item.inventory_type === 'source_product');
  const orbitRoots = states.filter(item => item.inventory_type === 'orbit_asset');

  const renderPager = (data, onPage) => (
    <div className="asset-pager">
      <button type="button" disabled={data.offset <= 0 || loading} onClick={() => onPage(Math.max(0, data.offset - PAGE_SIZE))}>
        上一页
      </button>
      <span>{data.offset + 1}-{data.offset + data.items.length} / {data.total}</span>
      <button type="button" disabled={!data.has_more || loading} onClick={() => onPage(data.offset + PAGE_SIZE)}>
        下一页
      </button>
    </div>
  );

  return (
    <div className="asset-inventory-panel">
      <div className="asset-toolbar">
        <div>
          <h3>源数据与精轨资产</h3>
          <p>Sentinel-1 与 LT-1 的源产品、精密轨道和绑定状态</p>
        </div>
        <div className="asset-actions">
          <select value={family} onChange={(e) => setFamily(e.target.value)} disabled={loading}>
            <option value="all">全部卫星族</option>
            <option value="S1">Sentinel-1</option>
            <option value="LT1">LT-1</option>
          </select>
          <button type="button" onClick={() => refresh()} disabled={loading}>刷新</button>
          <button type="button" onClick={() => handleScan({ families: INVENTORY_FAMILIES }, '全部资产扫描')} disabled={readOnly || scanLoading}>全部扫描</button>
          <button type="button" onClick={() => handleScan({ families: ['LT1'] }, 'LT-1资产扫描')} disabled={readOnly || scanLoading}>LT-1扫描</button>
          <button type="button" onClick={() => handleScan({ families: ['S1'] }, 'Sentinel-1资产扫描')} disabled={readOnly || scanLoading}>S1扫描</button>
          <button type="button" onClick={() => handleScan({ inventory_types: ['orbit_asset'], families: INVENTORY_FAMILIES }, '全部精轨扫描')} disabled={readOnly || scanLoading}>全部精轨</button>
          <button type="button" onClick={() => handleScan({ inventory_types: ['orbit_asset'], families: ['LT1'] }, 'LT-1精轨扫描')} disabled={readOnly || scanLoading}>LT-1精轨</button>
          <button type="button" onClick={() => handleScan({ inventory_types: ['orbit_asset'], families: ['S1'] }, 'Sentinel-1精轨扫描')} disabled={readOnly || scanLoading}>S1精轨</button>
          <button type="button" onClick={() => handleArchiveIntegrityAudit()} disabled={readOnly || auditLoading}>
            {auditLoading ? '审计启动中' : '压缩包完整性审计'}
          </button>
        </div>
      </div>

      {error ? <div className="asset-message asset-message--error">{error}</div> : null}
      {message ? <div className="asset-message">{message}</div> : null}

      <div className="asset-metrics">
        <Metric label="源产品" value={status?.source_asset_count} hint={`${sourceRoots.length} 个源数据根`} />
        <Metric label="精轨资产" value={status?.orbit_asset_count} hint={`${orbitRoots.length} 个精轨根`} />
        <Metric label="已绑定场景" value={status?.selected_binding_count} />
        <Metric label="开放问题" value={status?.open_issue_count} />
        <Metric
          label="压缩包完整性"
          value={status?.archive_integrity_counts?.OK || 0}
          hint={`未审计 ${status?.archive_integrity_counts?.NOT_CHECKED || 0} / 失败 ${status?.archive_integrity_counts?.FAILED || 0}`}
        />
      </div>

      <div className="asset-root-strip">
        {states.map((item) => (
          <div className="asset-root-item" key={`${item.inventory_type}-${item.root_ref_id}`}>
            <div>
              <strong>{item.inventory_type === 'source_product' ? '源数据池' : '精轨池'}</strong>
              <span title={item.root_path}>{item.root_path}</span>
            </div>
            <StatusBadge value={item.status} />
          </div>
        ))}
      </div>

      <div className="asset-tabbar">
        <button type="button" className={activeTab === 'sources' ? 'active-tab' : ''} onClick={() => setActiveTab('sources')}>
          源产品 ({sources.total})
        </button>
        <button type="button" className={activeTab === 'orbits' ? 'active-tab' : ''} onClick={() => setActiveTab('orbits')}>
          精轨 ({orbits.total})
        </button>
        <button type="button" className={activeTab === 'issues' ? 'active-tab' : ''} onClick={() => setActiveTab('issues')}>
          问题 ({issues.total})
        </button>
      </div>

      {activeTab === 'sources' && (
        <div className="asset-table-wrap">
          <table className="asset-table">
            <thead>
              <tr>
                <th>卫星</th>
                <th>日期/时间</th>
                <th>产品</th>
                <th>轨道</th>
                <th>状态</th>
                <th>完整性</th>
                <th>动作</th>
                <th>文件</th>
              </tr>
            </thead>
            <tbody>
              {sources.items.map(item => {
                return (
                  <tr key={item.id}>
                    <td><strong>{item.satellite}</strong><small>{item.satellite_family}</small></td>
                    <td>{item.imaging_date}<small>{fmtDateTime(item.acquisition_start_time_utc)}</small></td>
                    <td>{item.source_format}<small>{item.imaging_mode} / {item.polarization}</small></td>
                    <td>{item.relative_orbit || '-'}<small>abs {item.absolute_orbit || '-'}</small></td>
                    <td><StatusBadge value={item.parse_status} /></td>
                    <td title={item.archive_integrity_error || ''}>
                      <StatusBadge value={item.archive_integrity_status || 'NOT_CHECKED'} />
                      <small>{item.archive_integrity_member_count != null ? `${item.archive_integrity_member_count} files` : item.archive_integrity_method || '-'}</small>
                    </td>
                    <td>
                      <span className="asset-action-placeholder">-</span>
                    </td>
                    <td title={item.file_path}>{item.file_name || item.logical_product_uid}<small>{fmtBytes(item.size_bytes)}</small></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {renderPager(sources, (offset) => refresh({ sourceOffset: offset, orbitOffset: orbits.offset, issueOffset: issues.offset }))}
        </div>
      )}

      {activeTab === 'orbits' && (
        <div className="asset-table-wrap">
          <table className="asset-table">
            <thead>
              <tr>
                <th>卫星</th>
                <th>类型</th>
                <th>有效期</th>
                <th>质量</th>
                <th>状态</th>
                <th>文件</th>
              </tr>
            </thead>
            <tbody>
              {orbits.items.map(item => (
                <tr key={item.id}>
                  <td><strong>{item.satellite}</strong><small>{item.satellite_family}</small></td>
                  <td>{item.orbit_type}<small>{item.native_format}</small></td>
                  <td>{fmtDateTime(item.validity_start_time_utc)}<small>{fmtDateTime(item.validity_stop_time_utc)}</small></td>
                  <td>{item.quality_class}</td>
                  <td><StatusBadge value={item.parse_status} /></td>
                  <td title={item.file_path}>{item.file_name}<small>{fmtBytes(item.size_bytes)}</small></td>
                </tr>
              ))}
            </tbody>
          </table>
          {renderPager(orbits, (offset) => refresh({ sourceOffset: sources.offset, orbitOffset: offset, issueOffset: issues.offset }))}
        </div>
      )}

      {activeTab === 'issues' && (
        <div className="asset-table-wrap">
          <table className="asset-table">
            <thead>
              <tr>
                <th>级别</th>
                <th>代码</th>
                <th>对象</th>
                <th>说明</th>
                <th>时间</th>
              </tr>
            </thead>
            <tbody>
              {issues.items.map(item => (
                <tr key={item.id}>
                  <td><StatusBadge value={item.severity} /></td>
                  <td>{item.issue_code}</td>
                  <td>{item.inventory_type}<small>{item.source_path || `radar ${item.radar_data_id || '-'}`}</small></td>
                  <td>{item.issue_message || '-'}</td>
                  <td>{fmtDateTime(item.last_seen_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {renderPager(issues, (offset) => refresh({ sourceOffset: sources.offset, orbitOffset: orbits.offset, issueOffset: offset }))}
        </div>
      )}
    </div>
  );
}
