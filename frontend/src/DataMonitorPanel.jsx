import React, { useEffect, useRef, useState } from 'react';
import './App.css';
import { useI18n } from './i18n/I18nContext';
import { auditSourceArchiveIntegrity, scanAssetInventory } from './api/assets';

const DEFAULT_MONITOR_CONFIG = {
  radar_dirs: [],
  orbit_dir: '',
  orbit_source_dirs: [],
  orbit_production_txt_pool: '',
  dinsar_dirs: [],
  dinsar_product_dir: '',
  sbas_product_root: '',
  gf3_archive_source_dirs: [],
  gf3_source_dirs: [],
  gf3_legacy_gdal_enabled: false,
  gf3_sarscape_native_dirs: [],
  gf3_storage_dirs: [],
  gf3_sarscape_runtime_dir: '',
  gf3_sarscape_wrapper_exe: '',
  gf3_sarscape_idlrt_path: '',
  gf3_sarscape_dem_path: '',
  gf3_sarscape_polarizations: 'HH,HV',
  gf3_sarscape_auto_standardize: true,
  gf3_sarscape_clean_after_success: true,
  s1_source_dirs: [],
  s1_storage_dirs: [],
  s1_orbit_dirs: [],
  task_pool_root: '',
  dinsar_task_pool_root: '',
  sbas_task_pool_root: '',
  gf3_task_pool_root: '',
  data_distribution_root: '',
  storage_roots: [],
};

const toArray = (value) => (Array.isArray(value) ? value : []);

const formatList = (list) => (Array.isArray(list) && list.length ? list.join('; ') : '未配置');
const formatGb = (value) => {
  const number = Number(value);
  return Number.isFinite(number) ? `${number.toFixed(1)} GB` : '未知';
};
const storageStatusText = (status) => ({
  ok: '正常',
  warning: '偏低',
  critical: '严重不足',
  partial: '路径缺失',
  missing: '路径不存在',
  blocked: '禁止 UNC',
  error: '探测失败',
  empty: '未配置',
}[status] || status || '未知');
const storageStatusColor = (status) => ({
  ok: '#15803d',
  warning: '#b45309',
  critical: '#b91c1c',
  partial: '#b45309',
  missing: '#64748b',
  blocked: '#b91c1c',
  error: '#b91c1c',
  empty: '#64748b',
}[status] || '#475569');
const normalizeComparePath = (value) => String(value || '').trim().replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
const uniquePaths = (items) => {
  const seen = new Set();
  return toArray(items).filter((item) => {
    const text = String(item || '').trim();
    if (!text) {
      return false;
    }
    const key = normalizeComparePath(text);
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
};
const SCAN_TASK_TYPES = new Set([
  'SCAN_ASSET_INVENTORY',
  'AUDIT_SOURCE_ARCHIVE_INTEGRITY',
  'SCAN_DATA',
  'SCAN_DINSAR',
  'GF3_SARSCAPE_SYNC',
  'GF3_QUICKLOOK_WEBP',
]);
const MONITOR_REFRESH_MS = 10000;
const ACTIVE_TASK_LOG_REFRESH_MS = 3000;

const TASK_TYPE_LABELS = {
  SCAN_ASSET_INVENTORY: 'LT/S1 资产索引',
  AUDIT_SOURCE_ARCHIVE_INTEGRITY: '压缩包完整性审计',
  SCAN_DATA: '源数据/精轨扫描',
  SCAN_DINSAR: 'D-InSAR 结果扫描',
  GF3_SARSCAPE_SYNC: 'GF3 _geo 登记',
  GF3_QUICKLOOK_WEBP: 'GF3 WebP 生成',
};

const mergeTasks = (...groups) => {
  const map = new Map();
  groups.flatMap((group) => toArray(group)).forEach((task) => {
    if (task?.task_id && !map.has(task.task_id)) {
      map.set(task.task_id, task);
    }
  });
  return Array.from(map.values()).sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')));
};

const clampProgress = (value) => {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return 0;
  }
  return Math.max(0, Math.min(100, Math.round(number)));
};

const statusLabel = (status) => ({
  PENDING: '等待中',
  RUNNING: '运行中',
  COMPLETED: '已完成',
  FAILED: '失败',
  CANCELLED: '已取消',
}[String(status || '').toUpperCase()] || status || '未知');

const statusColor = (status) => ({
  PENDING: '#64748b',
  RUNNING: '#2563eb',
  COMPLETED: '#15803d',
  FAILED: '#b91c1c',
  CANCELLED: '#92400e',
}[String(status || '').toUpperCase()] || '#475569');

const formatTaskTime = (value) => {
  if (!value) {
    return '--:--:--';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value).slice(11, 19) || '--:--:--';
  }
  return date.toLocaleTimeString('zh-CN', { hour12: false });
};

const taskTitle = (task) => {
  const type = String(task?.task_type || '');
  return TASK_TYPE_LABELS[type] || task?.task_name || type || '任务';
};

const DataMonitorPanel = ({ apiEndpoint, onTaskStart, readOnly = false, enabled = true }) => {
  const { t } = useI18n();
  const [config, setConfig] = useState(DEFAULT_MONITOR_CONFIG);
  const [configLoaded, setConfigLoaded] = useState(false);
  const [logs, setLogs] = useState([]);
  const [activeTasks, setActiveTasks] = useState([]);
  const [recentScanTasks, setRecentScanTasks] = useState([]);
  const [selectedTaskId, setSelectedTaskId] = useState('');
  const [selectedTaskLogs, setSelectedTaskLogs] = useState([]);
  const [clearScanHistoryLoading, setClearScanHistoryLoading] = useState(false);
  const [clearScanHistoryMessage, setClearScanHistoryMessage] = useState('');
  const [s1ScanLoading, setS1ScanLoading] = useState(false);
  const [archiveAuditLoading, setArchiveAuditLoading] = useState(false);
  const [s1Message, setS1Message] = useState('');
  const [gf3SarscapeProduceLoading, setGf3SarscapeProduceLoading] = useState(false);
  const [gf3QuicklookWebpLoading, setGf3QuicklookWebpLoading] = useState(false);
  const [gf3Message, setGf3Message] = useState('');
  const logEndRef = useRef(null);

  const parseJsonSafe = async (response, fallback) => {
    try {
      return await response.json();
    } catch {
      return fallback;
    }
  };

  const displayLogs = toArray(logs);
  const displayActiveTasks = toArray(activeTasks);
  const sourceInventoryActiveTask = displayActiveTasks.find((task) => task.task_type === 'SCAN_ASSET_INVENTORY');
  const archiveAuditActiveTask = displayActiveTasks.find((task) => task.task_type === 'AUDIT_SOURCE_ARCHIVE_INTEGRITY');
  const gf3ActiveTask = displayActiveTasks.find((task) =>
    task.task_type === 'GF3_SARSCAPE_SYNC' || task.task_type === 'GF3_QUICKLOOK_WEBP'
  );

  useEffect(() => {
    if (!enabled) {
      setConfig(DEFAULT_MONITOR_CONFIG);
      setConfigLoaded(false);
      return;
    }

    let canceled = false;
    fetch(`${apiEndpoint}/monitor/status`, { credentials: 'include' })
      .then(async (res) => {
        const data = await parseJsonSafe(res, {});
        if (!res.ok) {
          throw new Error(data?.detail || `HTTP ${res.status}`);
        }
        return data;
      })
      .then((data) => {
        if (canceled) {
          return;
        }
        setConfig({
          ...DEFAULT_MONITOR_CONFIG,
          ...data,
          radar_dirs: toArray(data?.radar_dirs),
          orbit_source_dirs: toArray(data?.orbit_source_dirs),
          orbit_production_txt_pool: data?.orbit_production_txt_pool || '',
          dinsar_dirs: toArray(data?.dinsar_dirs),
          dinsar_product_dir: data?.dinsar_product_dir || '',
          sbas_product_root: data?.sbas_product_root || '',
          s1_source_dirs: toArray(data?.s1_source_dirs),
          s1_storage_dirs: toArray(data?.s1_storage_dirs),
          s1_orbit_dirs: toArray(data?.s1_orbit_dirs),
          storage_roots: toArray(data?.storage_roots),
          gf3_archive_source_dirs: toArray(data?.gf3_archive_source_dirs),
          gf3_source_dirs: toArray(data?.gf3_source_dirs),
          gf3_sarscape_native_dirs: toArray(data?.gf3_sarscape_native_dirs),
          gf3_storage_dirs: toArray(data?.gf3_storage_dirs),
        });
        setConfigLoaded(true);
      })
      .catch((err) => {
        if (canceled) {
          return;
        }
        console.error('Failed to fetch monitor status:', err);
        setConfigLoaded(false);
      });

    return () => {
      canceled = true;
    };
  }, [apiEndpoint, enabled]);

  useEffect(() => {
    if (!enabled) {
      setLogs([]);
      setActiveTasks([]);
      return;
    }

    let canceled = false;
    const fetchLogsAndTasks = async () => {
      try {
        const scanTaskTypes = Array.from(SCAN_TASK_TYPES).join(',');
        const [logsRes, tasksRes, recentRes] = await Promise.all([
          fetch(`${apiEndpoint}/monitor/logs`, { credentials: 'include' }),
          fetch(`${apiEndpoint}/tasks/active`, { credentials: 'include' }),
          fetch(`${apiEndpoint}/tasks/recent?task_types=${encodeURIComponent(scanTaskTypes)}&limit=8`, { credentials: 'include' }),
        ]);
        const [logsData, tasksData, recentData] = await Promise.all([
          parseJsonSafe(logsRes, {}),
          parseJsonSafe(tasksRes, []),
          parseJsonSafe(recentRes, []),
        ]);

        if (canceled) {
          return;
        }
        setLogs(logsRes.ok ? toArray(logsData?.logs) : []);
        setActiveTasks(tasksRes.ok ? toArray(tasksData) : []);
        setRecentScanTasks(recentRes.ok ? toArray(recentData) : []);
      } catch (err) {
        if (canceled) {
          return;
        }
        console.error('Failed to fetch logs or tasks:', err);
      }
    };

    fetchLogsAndTasks();
    const intervalId = setInterval(fetchLogsAndTasks, MONITOR_REFRESH_MS);

    return () => {
      canceled = true;
      clearInterval(intervalId);
    };
  }, [apiEndpoint, enabled]);

  const displayedScanTasks = mergeTasks(
    displayActiveTasks.filter((task) => SCAN_TASK_TYPES.has(task.task_type)),
    recentScanTasks
  ).slice(0, 8);
  const selectedTask = displayedScanTasks.find((task) => task.task_id === selectedTaskId) || displayedScanTasks[0] || null;
  const selectedTaskActive = ['PENDING', 'RUNNING'].includes(String(selectedTask?.status || '').toUpperCase());

  useEffect(() => {
    if (!enabled) {
      setSelectedTaskId('');
      setSelectedTaskLogs([]);
      return;
    }
    if (displayedScanTasks.length && !displayedScanTasks.some((task) => task.task_id === selectedTaskId)) {
      setSelectedTaskId(displayedScanTasks[0].task_id);
    }
  }, [displayedScanTasks, enabled, selectedTaskId]);

  useEffect(() => {
    if (!enabled || !selectedTaskId) {
      setSelectedTaskLogs([]);
      return;
    }
    let canceled = false;
    const fetchTaskLogs = async () => {
      try {
        const res = await fetch(`${apiEndpoint}/tasks/${selectedTaskId}/logs?limit=120`, { credentials: 'include' });
        const data = await parseJsonSafe(res, {});
        if (!canceled) {
          setSelectedTaskLogs(res.ok ? toArray(data?.logs) : []);
        }
      } catch (err) {
        if (!canceled) {
          console.error('Failed to fetch task logs:', err);
          setSelectedTaskLogs([]);
        }
      }
    };
    fetchTaskLogs();
    if (!selectedTaskActive) {
      return () => {
        canceled = true;
      };
    }
    const intervalId = setInterval(fetchTaskLogs, ACTIVE_TASK_LOG_REFRESH_MS);
    return () => {
      canceled = true;
      clearInterval(intervalId);
    };
  }, [apiEndpoint, enabled, selectedTaskActive, selectedTaskId]);

  useEffect(() => {
    if (logEndRef.current) {
      logEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs, selectedTaskLogs]);

  const sourceInventoryDirs = uniquePaths([...config.s1_source_dirs, ...config.s1_storage_dirs]);
  const orbitInventoryDirs = uniquePaths([
    ...config.orbit_source_dirs,
    ...config.s1_orbit_dirs,
    config.orbit_dir,
  ]);
  const hasSourceProductDirs = sourceInventoryDirs.length > 0;
  const hasOrbitAssetDirs = orbitInventoryDirs.length > 0;
  const hasGf3SarscapeNativeDirs = config.gf3_sarscape_native_dirs.length > 0;
  const hasGf3StorageDirs = config.gf3_storage_dirs.length > 0;

  const canRunSourceProductScan = !readOnly && configLoaded && hasSourceProductDirs;
  const canRunOrbitAssetScan = !readOnly && configLoaded && hasOrbitAssetDirs;
  const canRunGf3SarscapeProduce = !readOnly && configLoaded && hasGf3SarscapeNativeDirs && hasGf3StorageDirs;
  const activeIngestTaskCount = displayedScanTasks.filter((task) =>
    ['PENDING', 'RUNNING'].includes(String(task.status || '').toUpperCase())
  ).length;
  const availableStorageCount = config.storage_roots.filter((item) => ['ok', 'warning'].includes(String(item.status || '').toLowerCase())).length;

  const handleClearScanTaskHistory = async () => {
    if (readOnly) {
      setClearScanHistoryMessage('当前账户为只读模式，无法清空扫描日志。');
      return;
    }
    const hasRunningScan = displayedScanTasks.some((task) => ['PENDING', 'RUNNING'].includes(String(task.status || '').toUpperCase()));
    const confirmText = hasRunningScan
      ? '将清空已结束扫描任务的历史和日志，正在运行的任务会保留。确认继续？'
      : '将清空扫描任务历史和日志。确认继续？';
    if (!window.confirm(confirmText)) {
      return;
    }
    setClearScanHistoryLoading(true);
    setClearScanHistoryMessage('正在清空扫描任务历史...');
    try {
      const res = await fetch(`${apiEndpoint}/monitor/scan-task-history`, {
        method: 'DELETE',
        credentials: 'include',
      });
      const data = await parseJsonSafe(res, {});
      if (!res.ok) {
        throw new Error(data?.detail || `HTTP ${res.status}`);
      }
      setRecentScanTasks([]);
      setSelectedTaskId('');
      setSelectedTaskLogs([]);
      setLogs([]);
      setClearScanHistoryMessage(`已清空 ${data.deleted_task_count || 0} 个扫描任务、${data.deleted_log_count || 0} 条日志。`);
    } catch (err) {
      setClearScanHistoryMessage(`清空失败：${err?.message || '未知错误'}`);
    } finally {
      setClearScanHistoryLoading(false);
    }
  };

  const handleAssetInventoryScan = async (scanPayload, label) => {
    if (readOnly) {
      setS1Message('当前账户为只读模式，无法触发资产扫描。');
      return;
    }
    setS1ScanLoading(true);
    setS1Message(`${label}启动中...`);
    try {
      const res = await scanAssetInventory({
        root_ids: [],
        bind_orbits: true,
        ...scanPayload,
      });
      setS1Message(res.message || `${label}任务已启动`);
      onTaskStart?.(res.task_id, `${label}任务已启动`, {
        nonBlocking: true,
        taskType: 'SCAN_ASSET_INVENTORY',
      });
    } catch (err) {
      setS1Message(`失败：${err?.response?.data?.detail || err.message || '未知错误'}`);
    } finally {
      setS1ScanLoading(false);
    }
  };

  const handleArchiveIntegrityAudit = async () => {
    if (readOnly) {
      setS1Message('当前账户为只读模式，无法触发压缩包完整性审计。');
      return;
    }
    setArchiveAuditLoading(true);
    setS1Message('压缩包完整性审计启动中...');
    try {
      const res = await auditSourceArchiveIntegrity({
        families: ['LT1', 'S1'],
        source_formats: ['LT1_ARCHIVE', 'S1_ZIP'],
        force: false,
      });
      setS1Message(res.message || '压缩包完整性审计任务已启动');
      onTaskStart?.(res.task_id, '压缩包完整性审计任务已启动', {
        nonBlocking: true,
        taskType: 'AUDIT_SOURCE_ARCHIVE_INTEGRITY',
      });
    } catch (err) {
      setS1Message(`失败：${err?.response?.data?.detail || err.message || '未知错误'}`);
    } finally {
      setArchiveAuditLoading(false);
    }
  };

  const handleGf3SarscapeProduce = async () => {
    if (readOnly) {
      setGf3Message('当前账户为只读模式，无法登记 GF3 _geo 原生结果。');
      return;
    }
    if (!hasGf3SarscapeNativeDirs) {
      setGf3Message('GF3_SARSCAPE_NATIVE_DIRS 未配置，无法登记 _geo 原生结果。');
      return;
    }
    setGf3SarscapeProduceLoading(true);
    setGf3Message('正在按 GF3 日期/场景命名规则登记本机 _geo 原生结果...');
    try {
      const payload = {
        quicklook_only: true,
        register: true,
      };
      const res = await fetch(`${apiEndpoint}/monitor/gf3-sarscape-sync`, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      });
      const data = await parseJsonSafe(res, {});
      if (res.ok) {
        setGf3Message(data.message || 'GF3 _geo 原生结果登记任务已启动');
        if (onTaskStart) {
          onTaskStart(data.task_id, 'GF3 _geo 原生结果登记已启动', {
            nonBlocking: true,
            taskType: 'GF3_SARSCAPE_SYNC',
          });
        }
      } else {
        setGf3Message(`失败：${data.detail || '未知错误'}`);
      }
    } catch (err) {
      setGf3Message(`失败：${err.message || '未知错误'}`);
    } finally {
      setGf3SarscapeProduceLoading(false);
    }
  };

  const handleGf3QuicklookWebp = async () => {
    if (readOnly) {
      setGf3Message('当前账户为只读模式，无法生成 GF3 _geo WebP。');
      return;
    }
    setGf3QuicklookWebpLoading(true);
    setGf3Message('GF3 _geo WebP 生成任务启动中...');
    try {
      const res = await fetch(`${apiEndpoint}/monitor/gf3-quicklook-webp`, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ force: false }),
      });
      const data = await parseJsonSafe(res, {});
      if (res.ok) {
        setGf3Message(data.message || 'GF3 _geo WebP 生成任务已启动');
        if (onTaskStart) {
          onTaskStart(data.task_id, 'GF3 _geo WebP 生成任务已启动', {
            nonBlocking: true,
            taskType: 'GF3_QUICKLOOK_WEBP',
          });
        }
      } else {
        setGf3Message(`失败：${data.detail || '未知错误'}`);
      }
    } catch (err) {
      setGf3Message(`失败：${err.message || '未知错误'}`);
    } finally {
      setGf3QuicklookWebpLoading(false);
    }
  };

  const sectionStyle = {
    marginBottom: '12px',
    padding: '10px 12px',
    borderRadius: '8px',
    background: 'var(--color-panel-bg)',
    border: '1px solid var(--color-border)',
  };
  const labelStyle = { minWidth: '100px', color: 'var(--color-text-muted)', flexShrink: 0 };
  const rowStyle = { display: 'flex', gap: '8px' };
  const gridStyle = { display: 'grid', rowGap: '6px', fontSize: '0.9em', color: 'var(--color-text-secondary)' };

  const actionBtnStyle = (isLoading, isDisabled) => ({
    padding: '6px 10px',
    backgroundColor: 'var(--color-accent)',
    color: 'white',
    border: 'none',
    borderRadius: '4px',
    cursor: isLoading || isDisabled ? 'not-allowed' : 'pointer',
    fontSize: '0.85em',
  });

  return (
    <div
      className="monitor-panel data-ingest-panel"
    >
      <div className="data-ingest-header">
        <div>
          <h3>数据接入</h3>
          <p>集中管理源数据、精密轨道、GF3 回传成果和接入任务记录，确保生产前数据资产可追溯。</p>
        </div>
        <div className="data-ingest-signals" aria-label="数据接入状态摘要">
          <div className={`dinsar-production-signal tone-${configLoaded ? 'ready' : 'warn'}`}>
            <span>配置状态</span>
            <strong>{configLoaded ? '已加载' : '未加载'}</strong>
          </div>
          <div className={`dinsar-production-signal tone-${activeIngestTaskCount > 0 ? 'warn' : 'ready'}`}>
            <span>接入任务</span>
            <strong>{activeIngestTaskCount > 0 ? `${activeIngestTaskCount} 个运行中` : '空闲'}</strong>
          </div>
          <div className={`dinsar-production-signal tone-${hasSourceProductDirs ? 'ready' : 'neutral'}`}>
            <span>源数据池</span>
            <strong>{sourceInventoryDirs.length}</strong>
          </div>
          <div className={`dinsar-production-signal tone-${availableStorageCount > 0 ? 'ready' : 'neutral'}`}>
            <span>可用存储</span>
            <strong>{availableStorageCount}/{config.storage_roots.length || 0}</strong>
          </div>
        </div>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', minHeight: 0, paddingRight: '4px' }}>
        <div className={`data-ingest-notice ${configLoaded ? 'ok' : 'warn'}`}>
          {configLoaded
            ? '接入路径由环境配置统一管理；本页仅触发登记、审计和任务复核，不直接修改生产目录。'
            : '未加载到接入状态，请检查后端运行维护接口。'}
        </div>

        <details className="data-ingest-directory" style={sectionStyle}>
          <summary>接入路径与生产目录</summary>
          <p>以下为服务器部署目录，仅用于核对环境配置；日常接入操作不需要展开。</p>
          <div style={gridStyle}>
            <div style={rowStyle}><span style={labelStyle}>精轨源资产</span><span style={{ wordBreak: 'break-all' }}>{formatList(orbitInventoryDirs)}</span></div>
            <div style={rowStyle}><span style={labelStyle}>LT-1 生产 TXT 池</span><span style={{ wordBreak: 'break-all' }}>{config.orbit_production_txt_pool || '未配置'}</span></div>
            <div style={rowStyle}><span style={labelStyle}>LT/S1 压缩包源池</span><span style={{ wordBreak: 'break-all' }}>{formatList(sourceInventoryDirs)}</span></div>
            <div style={rowStyle}><span style={labelStyle}>GF3 独立 _geo 池</span><span style={{ wordBreak: 'break-all' }}>{formatList(config.gf3_sarscape_native_dirs)}</span></div>
            <div style={rowStyle}><span style={labelStyle}>GF3 标准/索引池</span><span style={{ wordBreak: 'break-all' }}>{formatList(config.gf3_storage_dirs)}</span></div>
            <div style={rowStyle}><span style={labelStyle}>GF3 Task_Pool</span><span style={{ wordBreak: 'break-all' }}>{config.gf3_task_pool_root || '未配置'}</span></div>
            <div style={rowStyle}><span style={labelStyle}>GF3 运行池</span><span style={{ wordBreak: 'break-all' }}>{config.gf3_sarscape_runtime_dir || '未配置'}</span></div>
            <div style={rowStyle}><span style={labelStyle}>数据分发根</span><span style={{ wordBreak: 'break-all' }}>{config.data_distribution_root || '未配置'}</span></div>
            <div style={rowStyle}><span style={labelStyle}>D-InSAR 结果</span><span style={{ wordBreak: 'break-all' }}>{config.dinsar_product_dir || '未配置'}</span></div>
            <div style={rowStyle}><span style={labelStyle}>SBAS 结果</span><span style={{ wordBreak: 'break-all' }}>{config.sbas_product_root || '未配置'}</span></div>
          </div>
        </details>

        <div style={sectionStyle}>
          <div style={{ fontWeight: 'bold', marginBottom: '8px', color: 'var(--color-text-primary)' }}>本机存储状态</div>
          <div style={{ ...gridStyle, rowGap: '8px' }}>
            {config.storage_roots.length ? config.storage_roots.map((item, index) => (
              <div
                key={`${item.path || item.label}-${index}`}
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'minmax(92px, 140px) minmax(110px, 150px) 1fr',
                  gap: '8px',
                  alignItems: 'start',
                }}
              >
                <span style={{ color: 'var(--color-text-muted)' }}>{item.label || item.role || '路径'}</span>
                <span style={{ color: storageStatusColor(item.status), fontWeight: 700 }}>
                  {storageStatusText(item.status)} · {formatGb(item.free_gb)} 可用
                </span>
                <span style={{ wordBreak: 'break-all' }}>
                  {item.path || '未配置'}
                  {item.total_gb != null ? `（总量 ${formatGb(item.total_gb)}）` : ''}
                  {item.message ? ` ${item.message}` : ''}
                </span>
              </div>
            )) : (
              <div style={{ color: 'var(--color-text-muted)' }}>未返回本机存储状态。</div>
            )}
          </div>
        </div>

        <div style={sectionStyle}>
          <div style={{ fontWeight: 'bold', marginBottom: '8px', color: 'var(--color-text-primary)' }}>LT-1 / Sentinel-1 源数据与精轨登记</div>
          <div style={{ ...gridStyle, marginBottom: '8px' }}>
            <div style={rowStyle}><span style={labelStyle}>压缩包源池</span><span style={{ wordBreak: 'break-all' }}>{formatList(sourceInventoryDirs)}</span></div>
            <div style={rowStyle}><span style={labelStyle}>精轨源资产</span><span style={{ wordBreak: 'break-all' }}>{formatList(orbitInventoryDirs)}</span></div>
            <div style={rowStyle}><span style={labelStyle}>LT-1 生产 TXT 池</span><span style={{ wordBreak: 'break-all' }}>{config.orbit_production_txt_pool || '未配置'}</span></div>
            <div style={rowStyle}><span style={labelStyle}>管理规则</span><span>压缩包只抽取 XML/manifest 和预览图；LT-1 精轨扫描后同步到生产 TXT 池，S1 精轨只登记 EOF 资产。</span></div>
          </div>
          <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
            <button
              onClick={() => handleAssetInventoryScan(
                { inventory_types: ['source_product'], families: ['LT1', 'S1'] },
                'LT-1 / Sentinel-1 源压缩包登记'
              )}
              disabled={s1ScanLoading || !canRunSourceProductScan}
              style={actionBtnStyle(s1ScanLoading, !canRunSourceProductScan)}
            >
              {s1ScanLoading ? '运行中...' : (readOnly ? '只读模式' : '登记源压缩包')}
            </button>
            <button
              onClick={() => handleAssetInventoryScan(
                { inventory_types: ['orbit_asset'], families: ['LT1', 'S1'] },
                'LT-1 / Sentinel-1 精轨登记'
              )}
              disabled={s1ScanLoading || !canRunOrbitAssetScan}
              style={actionBtnStyle(s1ScanLoading, !canRunOrbitAssetScan)}
            >
              {s1ScanLoading ? '运行中...' : (readOnly ? '只读模式' : '登记全部精轨')}
            </button>
            <button
              onClick={() => handleAssetInventoryScan(
                { inventory_types: ['orbit_asset'], families: ['S1'] },
                'Sentinel-1 精轨登记'
              )}
              disabled={s1ScanLoading || !canRunOrbitAssetScan}
              style={actionBtnStyle(s1ScanLoading, !canRunOrbitAssetScan)}
            >
              {s1ScanLoading ? '运行中...' : (readOnly ? '只读模式' : '登记 S1 EOF')}
            </button>
            <button
              onClick={() => handleAssetInventoryScan(
                { inventory_types: ['orbit_asset'], families: ['LT1'] },
                'LT-1 精轨登记'
              )}
              disabled={s1ScanLoading || !canRunOrbitAssetScan}
              style={actionBtnStyle(s1ScanLoading, !canRunOrbitAssetScan)}
            >
              {readOnly ? '只读模式' : '登记 LT-1 TXT'}
            </button>
            <button
              onClick={handleArchiveIntegrityAudit}
              disabled={archiveAuditLoading || !canRunSourceProductScan}
              style={actionBtnStyle(archiveAuditLoading, !canRunSourceProductScan)}
            >
              {archiveAuditLoading ? '运行中...' : (readOnly ? '只读模式' : '压缩包完整性审计')}
            </button>
            <div style={{ fontSize: '0.85em', color: s1Message.includes('失败') ? 'var(--color-danger)' : 'var(--color-text-muted)', alignSelf: 'center' }}>
              {archiveAuditActiveTask
                ? (archiveAuditActiveTask.message || '完整性审计运行中...')
                : sourceInventoryActiveTask
                  ? (sourceInventoryActiveTask.message || '资产索引运行中...')
                  : s1Message}
            </div>
          </div>
        </div>

        <div style={sectionStyle}>
          <div style={{ fontWeight: 'bold', marginBottom: '8px', color: 'var(--color-text-primary)' }}>GF3 _geo 原生结果登记</div>
          <div style={{ ...gridStyle, marginBottom: '8px' }}>
            <div style={rowStyle}><span style={labelStyle}>GF3 _geo 结果根目录</span><span style={{ wordBreak: 'break-all' }}>{formatList(config.gf3_sarscape_native_dirs)}</span></div>
            <div style={rowStyle}><span style={labelStyle}>GF3 标准/索引池</span><span style={{ wordBreak: 'break-all' }}>{formatList(config.gf3_storage_dirs)}</span></div>
            <div style={rowStyle}><span style={labelStyle}>登记内容</span><span>按 YYYYMMDD_geo/场景目录扫描 *_geo ENVI 二进制，WebP 从 _geo 主数据生成</span></div>
          </div>
          <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
            <button
              onClick={handleGf3SarscapeProduce}
              disabled={gf3SarscapeProduceLoading || gf3QuicklookWebpLoading || readOnly || !canRunGf3SarscapeProduce}
              style={actionBtnStyle(gf3SarscapeProduceLoading, readOnly || !canRunGf3SarscapeProduce)}
            >
              {gf3SarscapeProduceLoading ? '运行中...' : (readOnly ? '只读模式' : '登记 _geo 结果')}
            </button>
            <button
              onClick={handleGf3QuicklookWebp}
              disabled={gf3QuicklookWebpLoading || gf3SarscapeProduceLoading || readOnly || !hasGf3StorageDirs}
              style={actionBtnStyle(gf3QuicklookWebpLoading, readOnly || !hasGf3StorageDirs)}
            >
              {gf3QuicklookWebpLoading ? '运行中...' : (readOnly ? '只读模式' : '生成 WebP')}
            </button>
            <div style={{ fontSize: '0.85em', color: gf3Message.includes('失败') ? 'var(--color-danger)' : 'var(--color-text-muted)', alignSelf: 'center' }}>
              {gf3ActiveTask ? (gf3ActiveTask.message || 'GF3 任务运行中...') : gf3Message}
            </div>
          </div>
        </div>

        <div style={sectionStyle}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: '10px', alignItems: 'center', marginBottom: '8px' }}>
            <div style={{ fontWeight: 'bold', color: 'var(--color-text-primary)' }}>接入任务记录</div>
            <button
              type="button"
              onClick={handleClearScanTaskHistory}
              disabled={readOnly || clearScanHistoryLoading || displayedScanTasks.length === 0}
              style={{
                padding: '4px 10px',
                backgroundColor: readOnly || clearScanHistoryLoading || displayedScanTasks.length === 0 ? '#94a3b8' : '#b91c1c',
                color: 'white',
                border: 'none',
                borderRadius: '4px',
                cursor: readOnly || clearScanHistoryLoading || displayedScanTasks.length === 0 ? 'not-allowed' : 'pointer',
                fontSize: '0.8em',
              }}
            >
              {clearScanHistoryLoading ? '清空中...' : '清空记录'}
            </button>
          </div>
          {clearScanHistoryMessage && (
            <div
              style={{
                color: clearScanHistoryMessage.includes('失败') ? 'var(--color-danger)' : 'var(--color-text-muted)',
                fontSize: '0.85em',
                marginBottom: '8px',
              }}
            >
              {clearScanHistoryMessage}
            </div>
          )}
          {displayedScanTasks.length ? (
            <div style={{ display: 'grid', gap: '10px' }}>
              {displayedScanTasks.map((task) => {
                const progress = clampProgress(task.progress);
                const isSelected = task.task_id === selectedTask?.task_id;
                return (
                  <button
                    key={task.task_id}
                    type="button"
                    onClick={() => setSelectedTaskId(task.task_id)}
                    style={{
                      textAlign: 'left',
                      border: `1px solid ${isSelected ? 'var(--color-accent)' : 'var(--color-border)'}`,
                      background: isSelected ? 'var(--color-accent-soft)' : '#fff',
                      borderRadius: '8px',
                      padding: '10px 12px',
                      cursor: 'pointer',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: '10px', marginBottom: '6px' }}>
                      <strong style={{ color: 'var(--color-text-primary)' }}>{taskTitle(task)}</strong>
                      <span style={{ color: statusColor(task.status), fontWeight: 700 }}>{statusLabel(task.status)} · {progress}%</span>
                    </div>
                    <div style={{ height: '8px', background: '#e5e7eb', borderRadius: '999px', overflow: 'hidden', marginBottom: '6px' }}>
                      <div
                        style={{
                          width: `${progress}%`,
                          height: '100%',
                          background: statusColor(task.status),
                        }}
                      />
                    </div>
                    <div style={{ color: 'var(--color-text-secondary)', fontSize: '0.85em', wordBreak: 'break-word' }}>
                      {task.message || task.task_name || task.task_id}
                    </div>
                    <div style={{ color: 'var(--color-text-muted)', fontSize: '0.78em', marginTop: '4px' }}>
                      {formatTaskTime(task.created_at)} · {task.task_id}
                    </div>
                  </button>
                );
              })}
            </div>
          ) : (
            <div style={{ color: 'var(--color-text-muted)', fontSize: '0.9em' }}>暂无扫描任务。</div>
          )}
        </div>

      </div>

      <div style={{ flexShrink: 0, borderTop: '1px solid var(--color-border)', paddingTop: '10px', marginTop: '6px' }}>
        <div style={{ fontWeight: 'bold', marginBottom: '6px', color: 'var(--color-text-primary)' }}>
          {selectedTask ? `${taskTitle(selectedTask)} 任务日志` : '实时执行日志'}
          <span style={{ marginLeft: '8px', color: 'var(--color-text-muted)', fontWeight: 400, fontSize: '0.82em' }}>
            日志用于追踪执行步骤，issue 为本次解析已发现的问题计数。
          </span>
        </div>
        <div
          style={{
            height: '160px',
            overflowY: 'auto',
            backgroundColor: '#0f172a',
            color: '#22c55e',
            padding: '10px',
            fontFamily: 'monospace',
            fontSize: '0.85em',
            borderRadius: '4px',
          }}
        >
          {selectedTask ? (
            selectedTaskLogs.length ? (
              selectedTaskLogs.map((log) => (
                <div key={log.id || `${log.timestamp}-${log.message}`} style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                  [{formatTaskTime(log.timestamp)}] [{log.level || 'INFO'}] {log.message}
                </div>
              ))
            ) : (
              <div style={{ color: 'var(--color-text-muted)' }}>暂无任务日志...</div>
            )
          ) : displayLogs.length === 0 ? (
            <div style={{ color: 'var(--color-text-muted)' }}>暂无日志...</div>
          ) : (
            displayLogs.map((log, index) => (
              <div key={index} style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{t(log)}</div>
            ))
          )}
          <div ref={logEndRef} />
        </div>
      </div>

    </div>
  );
};

export default DataMonitorPanel;
