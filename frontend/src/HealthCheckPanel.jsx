import React, { useCallback, useEffect, useRef, useState } from 'react';
import './App.css';
import { getHealth } from './api/health';
import { getStatistics } from './api/stats';
import { cleanupSessions } from './api/auth';
import { syncWaterScenesFromDisk } from './api/water';
import { listEngines, runWslCheck } from './api/dinsarProduction';
import { getOrbitStatus, syncOrbitPools } from './api/orbit';
import LogManagementPanel from './LogManagementPanel';

const toNumber = (value) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
};

const asArray = (value) => (Array.isArray(value) ? value : []);

const asObject = (value) => (
  value && typeof value === 'object' && !Array.isArray(value) ? value : {}
);

const countGroupedColumns = (groups) => (
  Object.values(asObject(groups)).reduce((sum, columns) => sum + asArray(columns).length, 0)
);

const formatGroupedColumns = (groups) => (
  Object.entries(asObject(groups))
    .map(([table, columns]) => `${table}(${asArray(columns).join(', ')})`)
    .join('; ')
);

const formatMismatchList = (items, formatter) => (
  asArray(items).map(formatter).join('; ')
);

const formatIntegerText = (value) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toLocaleString() : '0';
};

const isCatalogHealthy = (catalog) => {
  if (!catalog || typeof catalog !== 'object') {
    return false;
  }
  if (catalog.enabled === false) {
    return true;
  }
  return Boolean(catalog.ok) && !catalog.needs_rebuild;
};

const formatCatalogStatusLabel = (catalog, en = false) => {
  if (!catalog || typeof catalog !== 'object') {
    return en ? 'Unknown' : '未知';
  }
  if (catalog.enabled === false) {
    return en ? 'Disabled' : '已禁用';
  }
  if (catalog.needs_rebuild) {
    return en ? 'Rebuild required' : '需要重建';
  }
  return catalog.catalog_status || (en ? 'Unknown' : '未知');
};

const getOrbitSourcePath = (item) => item?.source || item?.source_path || '';

const hasOrbitCorruptionSignal = (item) => Boolean(
  item && (
    item.has_corruption_signal ||
    item.contains_nul_bytes ||
    toNumber(item.nul_byte_count) > 0 ||
    item.tail_has_nul_bytes ||
    Number(item.first_nul_offset) >= 0 ||
    item.read_error
  )
);

const renderOrbitSourceIssueDetails = (item, en, formatPathText) => {
  const sourcePath = getOrbitSourcePath(item);
  const nulCount = toNumber(item?.nul_byte_count);
  const firstNulOffset = Number(item?.first_nul_offset);
  const hasNulDetails =
    item?.contains_nul_bytes ||
    nulCount > 0 ||
    item?.tail_has_nul_bytes ||
    firstNulOffset >= 0;

  return (
    <>
      {sourcePath && (
        <div style={{ color: '#64748b' }}>
          {en ? 'Source: ' : '源文件：'}{formatPathText(sourcePath)}
        </div>
      )}
      {item?.envi_path && (
        <div style={{ color: '#64748b' }}>
          {en ? 'ENVI: ' : 'ENVI：'}{formatPathText(item.envi_path)}
        </div>
      )}
      {hasNulDetails && (
        <div style={{ color: '#64748b' }}>
          {en ? 'NUL bytes: ' : 'NUL 字节：'}{formatIntegerText(nulCount)}
          {firstNulOffset >= 0 ? `${en ? ', first offset: ' : '，首个偏移：'}${formatIntegerText(firstNulOffset)}` : ''}
          {item?.tail_has_nul_bytes ? (en ? ' (tail contains NUL)' : '（文件尾含 NUL）') : ''}
        </div>
      )}
      {item?.read_error && (
        <div style={{ color: '#64748b' }}>
          {en ? 'Health scan error: ' : '文件健康扫描异常：'}{item.read_error}
        </div>
      )}
    </>
  );
};

const formatSourceRootRole = (role, en = false) => {
  switch (role) {
    case 'radar_source':
      return en ? 'Radar source' : '雷达源目录';
    case 'orbit_source':
      return en ? 'Orbit source' : '精轨源目录';
    case 'dinsar_source':
      return en ? 'D-InSAR source' : 'D-InSAR 结果源目录';
    default:
      return role || (en ? 'Unknown' : '未知');
  }
};

const buildConsistencySummary = (stats, en = false) => {
  const dinsar = stats?.dinsar_cache_consistency || {};
  const preview = stats?.source_preview_consistency || {};
  const xml = stats?.source_xml_consistency || {};
  const water = stats?.water_geo_consistency || {};

  const issues = [
    {
      key: 'dinsar_db_cached_missing',
      label: en ? 'D-InSAR: DB cached but file missing' : 'D-InSAR：库标记已缓存但文件缺失',
      count: toNumber(dinsar.db_cached_but_file_missing_count),
      level: 'error',
    },
    {
      key: 'source_ready_missing',
      label: en ? 'Source: DB READY but preview cache missing' : '源影像：DB READY 但预览缓存缺失',
      count: toNumber(preview.db_ready_but_cache_missing_count),
      level: 'error',
    },
    {
      key: 'water_db_missing_file',
      label: en ? 'Water: DB DONE but geo_db file missing' : '水体：DB DONE 但 geo_db 文件缺失',
      count: toNumber(water.registered_but_missing_count),
      level: 'error',
    },
    {
      key: 'dinsar_db_uncached_exists',
      label: en ? 'D-InSAR: DB uncached but file exists' : 'D-InSAR：库未标记缓存但文件已存在',
      count: toNumber(dinsar.db_uncached_but_file_exists_count),
      level: 'warn',
    },
    {
      key: 'manifest_missing_file',
      label: en ? 'D-InSAR: Manifest references missing file' : 'D-InSAR：Manifest 引用缺失文件',
      count: toNumber(dinsar.manifest_missing_file_count),
      level: 'warn',
    },
    {
      key: 'xml_unparsed',
      label: en ? 'Source: XML detected but key fields not imported' : '源影像：检测到 XML 但关键字段未入库',
      count: toNumber(xml.xml_detected_but_unparsed_count),
      level: 'warn',
    },
    {
      key: 'xml_missing',
      label: en ? 'Source: XML not detected' : '源影像：未检测到 XML',
      count: toNumber(xml.xml_missing_count),
      level: 'warn',
    },
    {
      key: 'water_unregistered',
      label: en ? 'Water: geo_db exists but not registered' : '水体：geo_db 存在但未入库',
      count: toNumber(water.unregistered_count),
      level: 'warn',
    },
  ];

  const total = issues.reduce((sum, item) => sum + item.count, 0);
  const critical = issues
    .filter((item) => item.level === 'error')
    .reduce((sum, item) => sum + item.count, 0);
  const warning = total - critical;

  return {
    total,
    critical,
    warning,
    issues,
  };
};

const HEALTH_PANEL_POLL_INTERVAL_MS = 5 * 60 * 1000;

const HealthCheckPanel = ({ currentUser }) => {
  const en = false;
  const isAdmin = currentUser?.role === 'admin';
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [lastChecked, setLastChecked] = useState(null);
  const [consistencySummary, setConsistencySummary] = useState(null);
  const [consistencyError, setConsistencyError] = useState('');
  const [syncLoading, setSyncLoading] = useState(false);
  const [syncResult, setSyncResult] = useState(null);
  const [cleanupLoading, setCleanupLoading] = useState(false);
  const [cleanupResult, setCleanupResult] = useState(null);
  const [dinsarEngines, setDinsarEngines] = useState([]);
  const [enginesLoading, setEnginesLoading] = useState(false);
  const [wslReport, setWslReport] = useState(null);
  const [wslChecking, setWslChecking] = useState(false);
  const [orbitStatus, setOrbitStatus] = useState(null);
  const [orbitSyncing, setOrbitSyncing] = useState(false);
  const [orbitSyncResult, setOrbitSyncResult] = useState(null);
  const statusFetchInFlightRef = useRef(false);

  const refreshOrbitStatus = useCallback(async () => {
    try {
      const data = await getOrbitStatus();
      setOrbitStatus(data);
    } catch (err) {
      setOrbitStatus(null);
      setOrbitSyncResult({ error: err.response?.data?.detail || err.message || '精轨状态获取失败' });
    }
  }, [en]);

  const refreshEngineStatus = useCallback(async () => {
    setEnginesLoading(true);
    try {
      const data = await listEngines();
      setDinsarEngines(Array.isArray(data.engines) ? data.engines : []);
    } catch {
      setDinsarEngines([]);
    } finally {
      setEnginesLoading(false);
    }
  }, []);

  const fetchStatus = useCallback(async (options = {}) => {
    const { force = false } = options;
    if (statusFetchInFlightRef.current) {
      return;
    }

    statusFetchInFlightRef.current = true;
    setLoading(true);
    setError('');
    setConsistencyError('');

    try {
      try {
        const healthData = await getHealth(force ? { full: true, refresh: true } : { full: true });
        setStatus(healthData);
        setLastChecked(new Date());
      } catch (err) {
      setError(err.response?.data?.detail || err.message || '运维自检失败');
        setStatus(null);
      }

      try {
        const statsData = await getStatistics(force);
        setConsistencySummary(buildConsistencySummary(statsData, en));
        setConsistencyError('');
      } catch (err) {
        setConsistencySummary(null);
      setConsistencyError(err.response?.data?.detail || err.message || '一致性统计获取失败');
      }

      // 引擎状态独立加载，不影响主健康检查。
      await refreshEngineStatus();

    } finally {
      setLoading(false);
      statusFetchInFlightRef.current = false;
    }
  }, [en, refreshEngineStatus]);

  useEffect(() => {
    void fetchStatus();
    const timer = setInterval(() => {
      if (
        syncLoading ||
        cleanupLoading ||
        wslChecking ||
        orbitSyncing
      ) {
        return;
      }
      void fetchStatus();
    }, HEALTH_PANEL_POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [
    cleanupLoading,
    fetchStatus,
    orbitSyncing,
    syncLoading,
    wslChecking,
  ]);

  const renderBadge = (ok, label = '') => (
    <span className={`health-badge ${ok ? 'ok' : 'fail'}`}>
      {ok ? (en ? 'OK' : '正常') : (en ? 'Error' : '异常')}{label ? ` · ${label}` : ''}
    </span>
  );

  const renderSignal = (ok, label, detail = '') => (
    <div className={`health-signal ${ok ? 'ok' : 'fail'}`}>
      <span className="health-signal-label">{label}</span>
      <strong>{ok ? '正常' : '异常'}</strong>
      {detail && <span className="health-signal-detail">{detail}</span>}
    </div>
  );

  const formatIso = (iso) => {
    if (!iso) return en ? 'Unknown' : '未知';
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  };

  const formatPathText = (value) => value || (en ? 'Not configured' : '未配置');
  const databaseStatus = status?.database || {};
  const dinsarBridge = asObject(status?.dinsar_bridge);
  const dinsarResultCatalog = asObject(status?.dinsar_result_catalog || status?.result_catalog);
  const timeseriesResultCatalog = asObject(status?.timeseries_result_catalog || status?.psinsar_result_catalog);
  const dinsarCatalogNeedsRebuild =
    typeof dinsarResultCatalog.needs_rebuild === 'boolean' ? dinsarResultCatalog.needs_rebuild : null;
  const timeseriesCatalogNeedsRebuild =
    typeof timeseriesResultCatalog.needs_rebuild === 'boolean' ? timeseriesResultCatalog.needs_rebuild : null;
  const sourceRoots = asObject(status?.source_roots);
  const productPackages = asObject(status?.product_packages);
  const assetInventory = asObject(status?.asset_inventory);
  const hasAssetInventory = Boolean(status?.asset_inventory);
  const assetSourceRoots = asObject(assetInventory.source_roots);
  const assetOrbitRoots = asObject(assetInventory.orbit_roots);
  const sourceAssets = asObject(assetInventory.source_assets);
  const orbitAssets = asObject(assetInventory.orbit_assets);
  const orbitBindings = asObject(assetInventory.bindings);
  const assetIssues = asObject(assetInventory.issues);
  const wslRuntime = asObject(status?.wsl_runtime);
  const wslRuntimeItems = asArray(wslRuntime.runtimes);
  const pairingSystem = asObject(status?.pairing_system);
  const sourceRootItems = asArray(sourceRoots.items);
  const bridgeDiagnosisIssueCount =
    toNumber(dinsarBridge.diagnosis_missing_product_row_count) +
    toNumber(dinsarBridge.diagnosis_product_identity_mismatch_count) +
    toNumber(dinsarBridge.diagnosis_result_product_mismatch_count);
  const bridgeIssueCount =
    toNumber(dinsarBridge.missing_compat_count) +
    toNumber(dinsarBridge.orphan_compat_count) +
    toNumber(dinsarBridge.duplicate_compat_product_count) +
    toNumber(dinsarBridge.annotation_drift_count) +
    bridgeDiagnosisIssueCount;
  const pairingIssueCount =
    toNumber(pairingSystem.duplicate_reverse_pair_count) +
    toNumber(pairingSystem.orphan_edge_count);
  const dbRequiredTables = asArray(databaseStatus.required_tables);
  const dbMissingTables = asArray(databaseStatus.missing_tables);
  const dbExtraTables = asArray(databaseStatus.extra_tables);
  const dbMissingColumns = asObject(databaseStatus.missing_columns);
  const dbExtraColumns = asObject(databaseStatus.extra_columns);
  const dbTypeMismatches = asArray(databaseStatus.type_mismatches);
  const dbNullableMismatches = asArray(databaseStatus.nullable_mismatches);
  const dbSchemaReasons = asArray(databaseStatus.schema_reasons);
  const dbMissingColumnCount = countGroupedColumns(dbMissingColumns);
  const dbExtraColumnCount = countGroupedColumns(dbExtraColumns);
  const dbHasStructureDetails = dbRequiredTables.length > 0 || dbSchemaReasons.length > 0;
  const orbitSource = orbitStatus?.source || {};
  const orbitPools = orbitStatus?.pools || {};
  const orbitConsistency = orbitStatus?.consistency || {};
  const orbitDatabase = orbitStatus?.database || {};
  const orbitMismatchCount = toNumber(orbitConsistency.mismatch_count);
  const orbitDbMissingEnviCount = toNumber(orbitDatabase.stems_missing_in_envi_count);
  const orbitDbMissingPathCount = toNumber(orbitDatabase.db_missing_path_count);
  const orbitDbFlagIssueCount =
    toNumber(orbitDatabase.has_orbit_but_missing_path_count) +
    toNumber(orbitDatabase.without_orbit_but_path_present_count);
  const orbitScanErrorCount =
    (orbitSource.errors?.length || 0) +
    (orbitPools.envi?.errors?.length || 0);
  const orbitDuplicateCount =
    toNumber(orbitSource.duplicate_count) +
    toNumber(orbitPools.envi?.duplicate_count);
  const orbitSuspectBadCount = toNumber(orbitSource.suspect_bad_count);
  const orbitSourceWithoutEnviCount = toNumber(orbitSource.source_without_envi_count);
  const orbitEnviWithoutSourceCount = toNumber(orbitSource.envi_without_source_count);
  const orbitBadSourceSamples = asArray(orbitSource.bad_source_samples).filter(hasOrbitCorruptionSignal);
  const orbitBadSourceSampleCount = toNumber(orbitSource.bad_source_sample_count || orbitBadSourceSamples.length);
  const orbitOverallHealthy = Boolean(
    orbitStatus &&
    orbitMismatchCount === 0 &&
    orbitDbMissingEnviCount === 0 &&
    orbitDbMissingPathCount === 0 &&
    orbitDbFlagIssueCount === 0 &&
    orbitScanErrorCount === 0 &&
    orbitSuspectBadCount === 0 &&
    orbitSourceWithoutEnviCount === 0 &&
    orbitEnviWithoutSourceCount === 0
  );
  const assetInventoryHealthy = Boolean(hasAssetInventory && assetInventory.ok);
  const orbitAssetRiskCount =
    toNumber(orbitAssets.parse_failed_count) +
    toNumber(orbitBindings.missing_count) +
    toNumber(orbitBindings.ambiguous_count);

  const productionBlockingCount = [
    !status?.ok,
    !status?.database?.ok,
    !status?.database?.schema_ok,
    !status?.worker?.ok,
    consistencySummary && consistencySummary.critical > 0,
    hasAssetInventory && !assetInventoryHealthy,
  ].filter(Boolean).length;

  return (
    <div className="health-panel">
      <div className="health-header">
        <div>
          <div className="health-title">运行维护</div>
          <div className="health-subtitle">
            面向生产环境的系统健康、数据一致性和维护操作入口
          </div>
        </div>
        <button className="health-refresh" onClick={() => fetchStatus({ force: true })} disabled={loading}>
          {loading ? '检查中...' : '刷新自检'}
        </button>
      </div>

      {error && <div className="health-error">{en ? 'Check failed: ' : '自检失败：'}{error}</div>}

      {!status && !error && (
        <div className="health-empty">{en ? 'Fetching status...' : '正在获取状态...'}</div>
      )}

      {status && (
        <>
          <div className="health-summary">
            <div className="health-summary-item">
              <span>生产就绪</span>
              {renderBadge(status.ok && productionBlockingCount === 0, productionBlockingCount > 0 ? `${productionBlockingCount} 个阻断项` : '可运行')}
            </div>
            <div className="health-summary-item">
              <span>最近检查</span>
              <span>{lastChecked ? lastChecked.toLocaleString() : formatIso(status.timestamp)}</span>
            </div>
            <div className="health-summary-item">
              <span>一致性异常</span>
              {renderBadge(
                !consistencySummary || consistencySummary.total === 0,
                consistencySummary ? `${consistencySummary.total} 项` : '未知'
              )}
            </div>
            <div className="health-signal-row">
              {renderSignal(!!status.database?.ok && !!status.database?.schema_ok, '数据库')}
              {renderSignal(!!status.worker?.ok, 'Worker', `${status.worker?.worker_count ?? 0} 个`)}
              {renderSignal(!!status.idl?.ok, 'IDL/ENVI')}
              {renderSignal(!!status.nginx?.ok, 'Nginx')}
            </div>
          </div>

          <div className="health-sections">
            <section className="health-section">
              <div className="health-section-header">
                <div>
                  <h3>核心服务</h3>
                  <p>判断系统是否具备基础生产能力：数据库、PostGIS、schema 和任务执行器。</p>
                </div>
              </div>
              <div className="health-grid">
            <div className="health-card">
              <div className="health-card-title">{en ? 'Database' : '数据库'}</div>
              <div className="health-card-row">
                <span>{en ? 'Connection' : '连接'}</span>
                {renderBadge(status.database?.ok)}
              </div>
              <div className="health-card-row">
                <span>PostGIS</span>
                {renderBadge(status.database?.postgis_ok)}
              </div>
              <div className="health-card-row">
                <span>{en ? 'Schema OK' : '结构一致'}</span>
                {renderBadge(status.database?.schema_ok)}
              </div>
              {dbHasStructureDetails && (
                <>
                  <div className="health-card-row">
                    <span>{en ? 'Schema Baseline' : '结构基线'}</span>
                    <span>{en ? 'ORM + migrations' : 'ORM + 迁移'}</span>
                  </div>
                  <div className="health-card-row">
                    <span>{en ? 'Required Tables' : '基线表数'}</span>
                    <span>{toNumber(databaseStatus.required_table_count || dbRequiredTables.length)}</span>
                  </div>
                  <div className="health-card-row">
                    <span>{en ? 'Current Tables' : '当前表数'}</span>
                    <span>{toNumber(databaseStatus.current_table_count)}</span>
                  </div>
                  <div className="health-card-row">
                    <span>{en ? 'Structure Issues' : '结构异常项'}</span>
                    <span>{toNumber(databaseStatus.schema_issue_count || dbSchemaReasons.length)}</span>
                  </div>
                  <details style={{ marginTop: 4 }} open={!databaseStatus.schema_ok}>
                    <summary style={{ cursor: 'pointer', fontSize: '0.8em', color: '#475569' }}>
                      {en ? 'Schema details' : '结构详情'}
                    </summary>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 8 }}>
                      <div className="health-card-note">
                        {en ? 'Required table list: ' : '基线表清单：'}{dbRequiredTables.join(', ')}
                      </div>
                      {dbMissingTables.length > 0 && (
                        <div className="health-card-note warn">
                          {en ? 'Missing tables: ' : '缺失表：'}{dbMissingTables.join(', ')}
                        </div>
                      )}
                      {dbExtraTables.length > 0 && (
                        <div className="health-card-note warn">
                          {en ? 'Extra tables: ' : '额外表：'}{dbExtraTables.join(', ')}
                        </div>
                      )}
                      {dbMissingColumnCount > 0 && (
                        <div className="health-card-note warn">
                          {en ? 'Missing columns: ' : '缺失列：'}{formatGroupedColumns(dbMissingColumns)}
                        </div>
                      )}
                      {dbExtraColumnCount > 0 && (
                        <div className="health-card-note warn">
                          {en ? 'Extra columns: ' : '额外列：'}{formatGroupedColumns(dbExtraColumns)}
                        </div>
                      )}
                      {dbTypeMismatches.length > 0 && (
                        <div className="health-card-note warn">
                          {en ? 'Type mismatches: ' : '字段类型不一致：'}
                          {formatMismatchList(
                            dbTypeMismatches,
                            (item) => `${item.table}.${item.column}(${item.expected} -> ${item.actual})`
                          )}
                        </div>
                      )}
                      {dbNullableMismatches.length > 0 && (
                        <div className="health-card-note warn">
                          {en ? 'Nullable mismatches: ' : '可空性不一致：'}
                          {formatMismatchList(
                            dbNullableMismatches,
                            (item) => `${item.table}.${item.column}(${String(item.expected)} -> ${String(item.actual)})`
                          )}
                        </div>
                      )}
                      {dbSchemaReasons.length > 0 ? (
                        <div className="health-card-note warn">
                          {en ? 'Detected issues:' : '检测到的问题：'}
                          {dbSchemaReasons.map((reason, index) => (
                            <div key={`${reason}-${index}`}>{`${index + 1}. ${reason}`}</div>
                          ))}
                        </div>
                      ) : (
                        <div className="health-card-note ok">
                          {en ? 'Schema structure matches the application baseline.' : '数据库结构与应用基线一致。'}
                        </div>
                      )}
                    </div>
                  </details>
                </>
              )}
              {!dbHasStructureDetails && !isAdmin && (
                <div className="health-card-note">
                  {en ? 'Log in as admin to view schema structure details.' : '管理员登录后可查看数据库结构详情。'}
                </div>
              )}
              {status.database?.error && (
                <div className="health-card-note error">
                  {status.database.error}
                </div>
              )}
            </div>

            <div className="health-card">
              <div className="health-card-title">{en ? 'Task Worker' : '任务执行器'}</div>
              <div className="health-card-row">
                <span>{en ? 'Online' : '在线'}</span>
                {renderBadge(status.worker?.ok)}
              </div>
              <div className="health-card-row">
                <span>{en ? 'Count' : '数量'}</span>
                <span>{status.worker?.worker_count ?? 0}</span>
              </div>
              <div className="health-card-row">
                <span>{en ? 'Timeout' : '超时阈值'}</span>
                <span>{status.worker?.timeout_seconds ?? 0}s</span>
              </div>
              {status.worker?.workers?.length > 0 && (
                <div className="health-card-note">
                  {en ? 'Last heartbeat: ' : '最近心跳：'}{formatIso(status.worker.workers[0].last_seen)}
                </div>
              )}
              {status.worker?.error && (
                <div className="health-card-note error">
                  {status.worker.error}
                </div>
              )}
            </div>
              </div>
            </section>

            <section className="health-section">
              <div className="health-section-header">
                <div>
                  <h3>结果目录与生产索引</h3>
                  <p>检查 D-InSAR、时序 InSAR、配对基础表和兼容视图是否能支撑结果查询与生产流转。</p>
                </div>
              </div>
              <div className="health-grid">
            <div className="health-card">
              <div className="health-card-title">{en ? 'D-InSAR Result Catalog' : 'D-InSAR 结果目录'}</div>
              <div className="health-card-row">
                <span>{en ? 'Catalog status' : '目录状态'}</span>
                {renderBadge(
                  isCatalogHealthy(dinsarResultCatalog),
                  formatCatalogStatusLabel(dinsarResultCatalog, en)
                )}
              </div>
              <div className="health-card-row">
                <span>{en ? 'Needs rebuild' : '需要重建'}</span>
                {renderBadge(
                  dinsarCatalogNeedsRebuild === false,
                  dinsarCatalogNeedsRebuild === null
                    ? (en ? 'Unknown' : '未知')
                    : (dinsarCatalogNeedsRebuild ? (en ? 'Yes' : '是') : (en ? 'No' : '否'))
                )}
              </div>
              <div className="health-card-row">
                <span>{en ? 'Manifest / DB' : 'Manifest / 数据库'}</span>
                <span>
                  {toNumber(dinsarResultCatalog.manifest_count)} / {toNumber(dinsarResultCatalog.db_count)}
                </span>
              </div>
              <div className="health-card-row">
                <span>{en ? 'Issue count' : '问题数量'}</span>
                <span>{toNumber(dinsarResultCatalog.issue_count)}</span>
              </div>
              <div className="health-card-note">
                {en
                  ? 'Full directory details and maintenance actions remain in the D-InSAR Products workspace.'
                  : '完整目录详情和维护操作已保留在 “D-InSAR 产物工作台”，不再在自检页展开。'}
              </div>
            </div>

            <div className="health-card">
              <div className="health-card-title">{en ? 'Timeseries Result Catalog' : '时序 InSAR 结果目录'}</div>
              <div className="health-card-row">
                <span>{en ? 'Catalog status' : '目录状态'}</span>
                {renderBadge(
                  isCatalogHealthy(timeseriesResultCatalog),
                  formatCatalogStatusLabel(timeseriesResultCatalog, en)
                )}
              </div>
              <div className="health-card-row">
                <span>{en ? 'Needs rebuild' : '需要重建'}</span>
                {renderBadge(
                  timeseriesCatalogNeedsRebuild === false,
                  timeseriesCatalogNeedsRebuild === null
                    ? (en ? 'Unknown' : '未知')
                    : (timeseriesCatalogNeedsRebuild ? (en ? 'Yes' : '是') : (en ? 'No' : '否'))
                )}
              </div>
              <div className="health-card-row">
                <span>{en ? 'Manifest / DB' : 'Manifest / 数据库'}</span>
                <span>
                  {toNumber(timeseriesResultCatalog.manifest_count)} / {toNumber(timeseriesResultCatalog.db_count)}
                </span>
              </div>
              <div className="health-card-row">
                <span>{en ? 'Issue count' : '问题数量'}</span>
                <span>{toNumber(timeseriesResultCatalog.issue_count)}</span>
              </div>
              <div className="health-card-note">
                {en
                  ? 'Managed timeseries products are indexed from canonical publish manifests.'
                  : '时序 InSAR 产物已按标准发布包 manifest 进行索引和自检。'}
              </div>
            </div>

            <div className="health-card">
              <div className="health-card-title">{en ? 'Pairing System' : '配对系统'}</div>
              <div className="health-card-row">
                <span>{en ? 'Overall' : '总体状态'}</span>
                {renderBadge(pairingSystem.ok, pairingSystem.status || (en ? 'Unknown' : '未知'))}
              </div>
              <div className="health-card-row">
                <span>{en ? 'Cache ready' : '缓存就绪'}</span>
                {renderBadge(
                  pairingSystem.cache_ready,
                  pairingSystem.needs_rebuild ? (en ? 'Rebuild required' : '需要重建') : ''
                )}
              </div>
              <div className="health-card-row">
                <span>{en ? 'Scenes / pairs' : '场景 / 候选对'}</span>
                <span>{toNumber(pairingSystem.scene_count)} / {toNumber(pairingSystem.pair_count)}</span>
              </div>
              <div className="health-card-row">
                <span>{en ? 'Dirty scenes' : '脏场景数'}</span>
                <span>{toNumber(pairingSystem.dirty_scene_count)}</span>
              </div>
              <div className="health-card-row">
                <span>{en ? 'Runs / edges' : '网络运行 / 边'}</span>
                <span>{toNumber(pairingSystem.network_run_count)} / {toNumber(pairingSystem.network_edge_count)}</span>
              </div>
              <div className="health-card-row">
                <span>{en ? 'Metric version' : '指标版本'}</span>
                <span>{pairingSystem.metric_version || (en ? 'Unknown' : '未知')}</span>
              </div>
              {!pairingSystem.state_present ? (
                <div className="health-card-note warn">
                  {en ? 'Pairing state row has not been initialized yet.' : '配对状态行尚未初始化。'}
                </div>
              ) : pairingIssueCount > 0 ? (
                <>
                  {toNumber(pairingSystem.duplicate_reverse_pair_count) > 0 && (
                    <div className="health-card-note error">
                      {en
                        ? `Reverse duplicate candidate pairs: ${toNumber(pairingSystem.duplicate_reverse_pair_count)}`
                        : `存在反向重复候选对：${toNumber(pairingSystem.duplicate_reverse_pair_count)}`}
                    </div>
                  )}
                  {toNumber(pairingSystem.orphan_edge_count) > 0 && (
                    <div className="health-card-note error">
                      {en
                        ? `Orphan network edges: ${toNumber(pairingSystem.orphan_edge_count)}`
                        : `存在孤儿网络边：${toNumber(pairingSystem.orphan_edge_count)}`}
                    </div>
                  )}
                </>
              ) : pairingSystem.needs_rebuild ? (
                <div className="health-card-note warn">
                  {en ? 'Pairing cache is marked dirty and needs rebuild or reconcile.' : '配对缓存已标脏，需要重建或增量修复。'}
                </div>
              ) : (
                <div className="health-card-note ok">
                  {en ? 'Pairing foundation state is healthy.' : '配对基础状态正常。'}
                </div>
              )}
              {pairingSystem.error && (
                <div className="health-card-note error">
                  {pairingSystem.error}
                </div>
              )}
              {pairingSystem.needs_rebuild && (
                <div className="health-card-note">
                  {en
                    ? 'Repair entry has moved to Pair Planning. Use the production planning page to repair or rebuild the pairing foundation.'
                    : '配对缓存修复入口已移到“生产规划 -> 配对规划”。请在生产规划页执行配对基础修复或强制全量重建。'}
                </div>
              )}
            </div>

            <div className="health-card">
              <div className="health-card-title">{en ? 'D-InSAR Bridge' : 'D-InSAR 桥接一致性'}</div>
              <div className="health-card-row">
                <span>{en ? 'Overall' : '总体状态'}</span>
                {renderBadge(
                  dinsarBridge.ok,
                  `${toNumber(dinsarBridge.matched_count)} / ${toNumber(dinsarBridge.catalog_count)}`
                )}
              </div>
              <div className="health-card-row">
                <span>{en ? 'Catalog / compat' : '目录 / 兼容表'}</span>
                <span>{toNumber(dinsarBridge.catalog_count)} / {toNumber(dinsarBridge.compat_count)}</span>
              </div>
              <div className="health-card-row">
                <span>{en ? 'Missing / orphan' : '缺失 / 孤儿记录'}</span>
                <span>{toNumber(dinsarBridge.missing_compat_count)} / {toNumber(dinsarBridge.orphan_compat_count)}</span>
              </div>
              <div className="health-card-row">
                <span>{en ? 'Duplicate / drift' : '重复 / 标注漂移'}</span>
                <span>{toNumber(dinsarBridge.duplicate_compat_product_count)} / {toNumber(dinsarBridge.annotation_drift_count)}</span>
              </div>
              <div className="health-card-row">
                <span>{en ? 'Diagnosis issues' : '诊断链路异常'}</span>
                <span>{bridgeDiagnosisIssueCount}</span>
              </div>
              {bridgeIssueCount === 0 ? (
                <div className="health-card-note ok">
                  {en ? 'Catalog reads and compatibility rows are aligned.' : '目录事实源与兼容视图当前一致。'}
                </div>
              ) : (
                <>
                  {toNumber(dinsarBridge.missing_compat_count) > 0 && (
                    <div className="health-card-note error">
                      {en
                        ? `Catalog products missing compat rows: ${toNumber(dinsarBridge.missing_compat_count)}`
                        : `目录产品缺少兼容行：${toNumber(dinsarBridge.missing_compat_count)}`}
                    </div>
                  )}
                  {toNumber(dinsarBridge.orphan_compat_count) > 0 && (
                    <div className="health-card-note error">
                      {en
                        ? `Compat rows pointing to missing products: ${toNumber(dinsarBridge.orphan_compat_count)}`
                        : `兼容行指向不存在产品：${toNumber(dinsarBridge.orphan_compat_count)}`}
                    </div>
                  )}
                  {toNumber(dinsarBridge.duplicate_compat_product_count) > 0 && (
                    <div className="health-card-note warn">
                      {en
                        ? `Duplicate compat product bindings: ${toNumber(dinsarBridge.duplicate_compat_product_count)}`
                        : `兼容产品绑定重复：${toNumber(dinsarBridge.duplicate_compat_product_count)}`}
                    </div>
                  )}
                  {toNumber(dinsarBridge.annotation_drift_count) > 0 && (
                    <div className="health-card-note warn">
                      {en
                        ? `Annotation drift between catalog and compat rows: ${toNumber(dinsarBridge.annotation_drift_count)}`
                        : `目录与兼容行的标注出现漂移：${toNumber(dinsarBridge.annotation_drift_count)}`}
                    </div>
                  )}
                  {bridgeDiagnosisIssueCount > 0 && (
                    <div className="health-card-note warn">
                      {en
                        ? `Diagnosis linkage anomalies: ${bridgeDiagnosisIssueCount}`
                        : `AI 诊断关联异常：${bridgeDiagnosisIssueCount}`}
                    </div>
                  )}
                </>
              )}
              {dinsarBridge.error && (
                <div className="health-card-note error">
                  {dinsarBridge.error}
                </div>
              )}
            </div>
              </div>
            </section>

            <section className="health-section">
              <div className="health-section-header">
                <div>
                  <h3>数据资产与运行时</h3>
                  <p>检查受管源路径、标准结果包、WSL runtime、IDL/ENVI、D-InSAR 引擎、Ollama 和 Nginx。</p>
                </div>
              </div>
              <div className="health-grid">
            <div className="health-card">
              <div className="health-card-title">{en ? 'Managed Source Roots' : '受管源路径'}</div>
              <div className="health-card-row">
                <span>{en ? 'Overall' : '总体状态'}</span>
                {renderBadge(
                  sourceRoots.ok,
                  `${toNumber(sourceRoots.accessible_count)} / ${toNumber(sourceRoots.configured_count)}`
                )}
              </div>
              <div className="health-card-row">
                <span>{en ? 'Configured' : '已配置'}</span>
                <span>{toNumber(sourceRoots.configured_count)}</span>
              </div>
              <div className="health-card-row">
                <span>{en ? 'Accessible / blocked' : '可访问 / 阻塞'}</span>
                <span>{toNumber(sourceRoots.accessible_count)} / {toNumber(sourceRoots.inaccessible_count)}</span>
              </div>
              {toNumber(sourceRoots.inaccessible_count) > 0 && (
                <div className="health-card-note warn">
                  {en
                    ? 'Unified scan will remain empty until all configured source roots become accessible.'
                    : '只要受管源路径不可访问，统一扫描就会保持空目录状态。'}
                </div>
              )}
              {sourceRootItems.length > 0 ? (
                sourceRootItems.map((item, index) => (
                  <div
                    key={`${item.role || 'root'}-${index}`}
                    className={`health-card-note ${item.accessible ? 'ok' : 'error'}`}
                  >
                    <div style={{ fontWeight: 600 }}>
                      {formatSourceRootRole(item.role, en)}: {item.accessible ? (en ? 'OK' : '正常') : (en ? 'Blocked' : '不可访问')}
                    </div>
                    <div style={{ color: '#64748b', wordBreak: 'break-all' }}>{formatPathText(item.path)}</div>
                    {!item.accessible && item.error && (
                      <div style={{ color: '#991b1b', marginTop: 2 }}>{item.error}</div>
                    )}
                  </div>
                ))
              ) : toNumber(sourceRoots.configured_count) > 0 && !isAdmin ? (
                <div className="health-card-note">
                  {en ? 'Log in as admin to view path-level diagnostics.' : '管理员登录后可查看路径级诊断信息。'}
                </div>
              ) : null}
            </div>

            <div className="health-card">
              <div className="health-card-title">{en ? 'Product Packages' : '标准结果包'}</div>
              <div className="health-card-row">
                <span>{en ? 'Overall' : '总体状态'}</span>
                {renderBadge(
                  productPackages.ok,
                  `${toNumber(productPackages.valid_schema_count ?? productPackages.canonical_count)} / ${toNumber(productPackages.total_count)}`
                )}
              </div>
              <div className="health-card-row">
                <span>{en ? 'Manifest / publish dir' : 'Manifest / 发布目录'}</span>
                <span>
                  {toNumber(productPackages.missing_manifest_count)} / {toNumber(productPackages.missing_publish_dir_count)}
                </span>
              </div>
              <div className="health-card-row">
                <span>{en ? 'Processor / runtime' : '处理器 / 运行时'}</span>
                <span>
                  {toNumber(productPackages.missing_processor_count)} / {toNumber(productPackages.missing_runtime_count)}
                </span>
              </div>
              <div className="health-card-row">
                <span>{en ? 'Native output trace' : '原生输出追踪'}</span>
                <span>{toNumber(productPackages.missing_native_output_count)}</span>
              </div>
              <div className="health-card-note">
                {en
                  ? 'Catalog registration now checks canonical package metadata instead of guessing engine-native directories.'
                  : '目录登记现在直接校验标准包元数据，不再依赖猜测引擎原生目录结构。'}
              </div>
            </div>

            <div className="health-card">
              <div className="health-card-title">{en ? 'WSL Runtime' : 'WSL 运行时'}</div>
              <div className="health-card-row">
                <span>{en ? 'Overall' : '总体状态'}</span>
                {renderBadge(
                  wslRuntime.ok,
                  `${toNumber(wslRuntime.healthy_runtime_count)} / ${toNumber(wslRuntime.required_runtime_count)}`
                )}
              </div>
              <div className="health-card-row">
                <span>{en ? 'Broker root' : 'Broker 根目录'}</span>
                {renderBadge(wslRuntime.broker_job_root_exists)}
              </div>
              <div className="health-card-row">
                <span>{en ? 'Shared distro' : '共享发行版'}</span>
                <span>{wslRuntime.shared_distro || '-'}</span>
              </div>
              <div className="health-card-row">
                <span>{en ? 'Shared conda env' : '共享 conda 环境'}</span>
                <span>{wslRuntime.shared_conda_env_name || '-'}</span>
              </div>
              {wslRuntimeItems.length > 0 && (
                wslRuntimeItems.map((item) => (
                  <div
                    key={item.runtime_id || item.engine_code}
                    className={`health-card-note ${item.ok ? 'ok' : (item.required ? 'error' : 'warn')}`}
                  >
                    <div style={{ fontWeight: 600 }}>
                      {item.display_name || item.runtime_id || item.engine_code}
                    </div>
                    <div>
                      {(item.required ? (en ? 'Required' : '必需') : (en ? 'Reserved' : '预留'))}
                      {' · '}
                      {item.runner_exists ? (en ? 'Runner OK' : 'Runner 正常') : (en ? 'Runner missing' : 'Runner 缺失')}
                    </div>
                  </div>
                ))
              )}
            </div>

            <div className="health-card">
              <div className="health-card-title">IDL/ENVI</div>
              <div className="health-card-row">
                <span>{en ? 'Installed' : '已安装'}</span>
                {renderBadge(status.idl?.ok)}
              </div>
              <div className="health-card-row">
                <span>{en ? 'Status' : '状态'}</span>
                <span style={{ color: status.idl?.status?.is_running ? '#16a34a' : '#64748b', fontWeight: 500 }}>
                  {status.idl?.status?.is_running ? (en ? 'Open' : '已打开') : (en ? 'Closed' : '未打开')}
                </span>
              </div>
            </div>

            {/* D-InSAR 引擎专项 */}
            <div className="health-card">
              <div className="health-card-title" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span>{en ? 'D-InSAR Engines' : 'D-InSAR 引擎'}</span>
                {isAdmin && (
                  <button
                    onClick={async () => {
                      setWslChecking(true);
                      setWslReport(null);
                      try {
                        const r = await runWslCheck({ smoke_test: false });
                        setWslReport(r);
                      } catch (e) {
                        setWslReport({ overall_ok: false, distro: '?', checks: [], message: e?.response?.data?.detail || e.message });
                      } finally {
                        setWslChecking(false);
                        await fetchStatus({ force: true });
                      }
                    }}
                    disabled={wslChecking}
                    style={{ fontSize: 11, padding: '2px 8px', borderRadius: 3, border: '1px solid #e2e8f0', cursor: 'pointer', background: '#f8fafc', fontWeight: 400 }}
                  >
                    {wslChecking ? (en ? 'Checking...' : '检查中...') : (en ? 'WSL Check' : 'WSL 详细检查')}
                  </button>
                )}
              </div>
              {enginesLoading ? (
                <div className="health-card-note">{en ? 'Loading...' : '加载中...'}</div>
              ) : dinsarEngines.length === 0 ? (
                <div className="health-card-note">{en ? 'No engines registered' : '未注册引擎'}</div>
              ) : (
                dinsarEngines.map(engine => {
                  const colorMap = { ok: '#22c55e', degraded: '#f59e0b', unavailable: '#ef4444', not_implemented: '#94a3b8', error: '#ef4444' };
                  const labelMap = { ok: en ? 'Available' : '可用', degraded: en ? 'Degraded' : '部分可用', unavailable: en ? 'Unavailable' : '不可用', not_implemented: en ? 'Reserved' : '预留', error: en ? 'Error' : '错误' };
                  const color = colorMap[engine.status] || '#94a3b8';
                  return (
                    <div key={engine.engine_code} className="health-card-row" style={{ alignItems: 'flex-start' }}>
                      <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                        <span style={{ width: 7, height: 7, borderRadius: '50%', background: color, display: 'inline-block', flexShrink: 0 }} />
                        {engine.engine_label}
                      </span>
                      <span style={{ color, fontSize: 12 }}>{labelMap[engine.status] || engine.status}</span>
                    </div>
                  );
                })
              )}
              {wslReport && (
                <div style={{ marginTop: 8, padding: '6px 8px', background: wslReport.overall_ok ? '#f0fdf4' : '#fef2f2', borderRadius: 4, fontSize: 11 }}>
                  <div style={{ fontWeight: 600, color: wslReport.overall_ok ? '#16a34a' : '#dc2626', marginBottom: 4 }}>
                    {wslReport.overall_ok ? (en ? '✓ WSL OK' : '✓ WSL 正常') : (en ? '✗ WSL Issues' : '✗ WSL 存在问题')}
                    <span style={{ color: '#94a3b8', fontWeight: 400, marginLeft: 6 }}>({wslReport.distro})</span>
                  </div>
                  {(wslReport.checks || []).map((c, i) => (
                    <div key={i} style={{ display: 'flex', gap: 6, padding: '2px 0', color: c.skipped ? '#94a3b8' : c.ok ? '#16a34a' : '#dc2626' }}>
                      <span style={{ flexShrink: 0 }}>{c.skipped ? '—' : c.ok ? '✓' : '✗'}</span>
                      <span style={{ flex: 1, color: '#374151' }}>{c.name}</span>
                      <span style={{ color: '#94a3b8', maxWidth: 160, textAlign: 'right', wordBreak: 'break-all' }}>{c.detail}</span>
                    </div>
                  ))}
                  <div style={{ color: '#64748b', marginTop: 4 }}>{wslReport.message}</div>
                </div>
              )}
            </div>

            <div className="health-card">
              <div className="health-card-title">Ollama</div>
              <div className="health-card-row">
                <span>{en ? 'Reachable' : '连通'}</span>
                {renderBadge(status.ollama?.ok)}
              </div>
              {status.ollama?.models?.length > 0 && (
                <div className="health-card-note">
                  {en ? 'Models: ' : '模型：'}{status.ollama.models.join(', ')}
                </div>
              )}
              {status.ollama?.error && (
                <div className="health-card-note error">
                  {status.ollama.error}
                </div>
              )}
            </div>

            <div className="health-card">
              <div className="health-card-title">Nginx</div>
              <div className="health-card-row">
                <span>{en ? 'Reachable' : '连通'}</span>
                {renderBadge(status.nginx?.ok)}
              </div>
              {status.nginx?.status_code && (
                <div className="health-card-note">
                  {en ? 'Status code: ' : '状态码：'}{status.nginx.status_code}
                </div>
              )}
              {status.nginx?.error && (
                <div className="health-card-note error">
                  {status.nginx.error}
                </div>
              )}
            </div>
              </div>
            </section>

            <section className="health-section">
              <div className="health-section-header">
                <div>
                  <h3>数据资产与精轨</h3>
                  <p>当前生产以 XML 抽取后的源产品、精轨资产和场景绑定为准；旧精轨池核对仅作为过渡诊断。</p>
                </div>
              </div>
              <div className="health-grid health-grid--ops">
            <div className="health-card">
              <div className="health-card-title">源产品资产</div>
              <div className="health-card-row">
                <span>总体状态</span>
                {renderBadge(
                  assetInventoryHealthy,
                  assetInventory ? `${toNumber(sourceAssets.total_count)} 项` : '未知'
                )}
              </div>
              <div className="health-card-row">
                <span>LT-1 / Sentinel-1</span>
                <span>{toNumber(sourceAssets.lt1_count)} / {toNumber(sourceAssets.s1_count)}</span>
              </div>
              <div className="health-card-row">
                <span>解析异常</span>
                <span>{toNumber(sourceAssets.parse_failed_count)}</span>
              </div>
              <div className="health-card-row">
                <span>源数据根</span>
                <span>{toNumber(assetSourceRoots.accessible_count)} / {toNumber(assetSourceRoots.configured_count)}</span>
              </div>
              <div className="health-card-row">
                <span>需复扫</span>
                <span>{toNumber(assetSourceRoots.needs_rescan_count)}</span>
              </div>
              {toNumber(sourceAssets.parse_failed_count) > 0 ? (
                <div className="health-card-note error">
                  存在源产品解析异常，请在“数据资产”中查看开放问题并复扫相关目录。
                </div>
              ) : (
                <div className="health-card-note ok">
                  源产品资产已按 XML/元数据登记。
                </div>
              )}
            </div>

            <div className="health-card">
              <div className="health-card-title">精轨资产状态</div>
              <div className="health-card-row">
                <span>总体状态</span>
                {renderBadge(
                  orbitAssetRiskCount === 0 && assetInventoryHealthy,
                  orbitAssetRiskCount > 0 ? `${orbitAssetRiskCount} 个风险项` : `${toNumber(orbitAssets.total_count)} 项`
                )}
              </div>
              <div className="health-card-row">
                <span>LT-1 / Sentinel-1</span>
                <span>{toNumber(orbitAssets.lt1_count)} / {toNumber(orbitAssets.s1_count)}</span>
              </div>
              <div className="health-card-row">
                <span>解析异常</span>
                <span>{toNumber(orbitAssets.parse_failed_count)}</span>
              </div>
              <div className="health-card-row">
                <span>精轨根</span>
                <span>{toNumber(assetOrbitRoots.accessible_count)} / {toNumber(assetOrbitRoots.configured_count)}</span>
              </div>
              <div className="health-card-row">
                <span>需复扫</span>
                <span>{toNumber(assetOrbitRoots.needs_rescan_count)}</span>
              </div>
              <div className="health-card-note">
                精轨可用性以资产登记和时间窗绑定结果为准，不再以 ISCE2 XML 池作为生产判断。
              </div>
            </div>

            <div className="health-card">
              <div className="health-card-title">场景精轨绑定</div>
              <div className="health-card-row">
                <span>已绑定场景</span>
                <span>{toNumber(orbitBindings.matched_count)} / {toNumber(orbitBindings.scene_count)}</span>
              </div>
              <div className="health-card-row">
                <span>缺失精轨</span>
                <span>{toNumber(orbitBindings.missing_count)}</span>
              </div>
              <div className="health-card-row">
                <span>候选歧义</span>
                <span>{toNumber(orbitBindings.ambiguous_count)}</span>
              </div>
              <div className="health-card-row">
                <span>开放问题</span>
                <span>{toNumber(assetIssues.open_count)}</span>
              </div>
              <div className="health-card-row">
                <span>错误 / 警告</span>
                <span>{toNumber(assetIssues.error_count)} / {toNumber(assetIssues.warning_count)}</span>
              </div>
              {orbitAssetRiskCount > 0 ? (
                <div className="health-card-note warn">
                  请在“数据资产”中复核缺失或歧义精轨；生产配对会优先使用已选定的精轨资产。
                </div>
              ) : (
                <div className="health-card-note ok">
                  当前开放问题未显示精轨绑定风险。
                </div>
              )}
            </div>
            <div className="health-card">
              <div className="health-card-title">{en ? 'Consistency Check' : '一致性检测'}</div>
              <div className="health-card-row">
                <span>{en ? 'Total Issues' : '异常总数'}</span>
                {renderBadge(
                  !consistencySummary || consistencySummary.total === 0,
                  consistencySummary ? `${consistencySummary.total} ${en ? 'items' : '项'}` : (en ? 'Unknown' : '未知')
                )}
              </div>
              {consistencySummary && (
                <>
                  <div className="health-card-row">
                    <span>{en ? 'Critical' : '严重异常'}</span>
                    <span>{consistencySummary.critical}</span>
                  </div>
                  <div className="health-card-row">
                    <span>{en ? 'Warning' : '一般异常'}</span>
                    <span>{consistencySummary.warning}</span>
                  </div>
                  {consistencySummary.issues
                    .filter((item) => item.count > 0)
                    .map((item) => (
                      <div key={item.key} className={`health-card-note ${item.level === 'error' ? 'error' : 'warn'}`}>
                        {item.label}：{item.count} 条
                      </div>
                    ))}
                  {consistencySummary.issues.find(i => i.key === 'water_unregistered' && i.count > 0) && (
                    <div style={{ marginTop: 8 }}>
                      <button
                        onClick={async () => {
                          setSyncLoading(true);
                          setSyncResult(null);
                          try {
                            const res = await syncWaterScenesFromDisk();
                            setSyncResult(en
                              ? `Sync done: inserted ${res.data.inserted}, skipped ${res.data.skipped_already_done}`
                              : `补录完成：新增 ${res.data.inserted} 条，已跳过 ${res.data.skipped_already_done} 条`);
                            await fetchStatus({ force: true });
                          } catch (e) {
                            setSyncResult(en
                              ? `Sync failed: ${e.response?.data?.detail || e.message}`
                              : `补录失败：${e.response?.data?.detail || e.message}`);
                          } finally {
                            setSyncLoading(false);
                          }
                        }}
                        disabled={syncLoading}
                        style={{ padding: '4px 12px', background: '#1890ff', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer', fontSize: 12 }}
                      >
                        {syncLoading ? (en ? 'Syncing...' : '补录中...') : (en ? 'Auto Sync' : '一键补录')}
                      </button>
                      {syncResult && <div className="health-card-note" style={{ marginTop: 4 }}>{syncResult}</div>}
                    </div>
                  )}
                  {consistencySummary.total === 0 && (
                    <div className="health-card-note ok">{en ? 'No consistency issues found.' : '未发现一致性异常。'}</div>
                  )}
                </>
              )}
              {consistencyError && (
                <div className="health-card-note error">
                  {en ? 'Failed to fetch consistency stats: ' : '一致性统计获取失败：'}{consistencyError}
                </div>
              )}
            </div>

          {isAdmin && (
            <details className="health-card health-legacy-diagnostic">
              <summary>
                <span>旧精轨池核对</span>
                <button
                  type="button"
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    void refreshOrbitStatus();
                  }}
                  disabled={orbitSyncing}
                  className="health-inline-button"
                >
                  {orbitStatus ? '重新核对' : '加载诊断'}
                </button>
              </summary>
              <div className="health-card-note">
                该诊断仅核对源精轨目录与 ENVI/Gamma 生产 TXT 池，用于排查历史目录；生产判断以资产登记与场景绑定为准。
              </div>
              {orbitStatus ? (
                <>
              <div className="health-card-title">{en ? 'Precise Orbit Management' : '文件池状态'}</div>
              <div className="health-card-row">
                <span>{en ? 'Overall' : '总体状态'}</span>
                {renderBadge(
                  orbitOverallHealthy,
                  orbitStatus
                    ? (en ? `${orbitMismatchCount} mismatches` : `${orbitMismatchCount} 项不一致`)
                    : (en ? 'Unknown' : '未知')
                )}
              </div>
              <div className="health-card-row">
                <span>
                  {en ? 'Source / production TXT' : '源目录 / 生产 TXT'}
                </span>
                <span>
                  {`${toNumber(orbitSource.total_source)} / ${toNumber(orbitPools.envi?.total)}`}
                </span>
              </div>
              <div className="health-card-row">
                <span>{en ? 'DB expected / missing path' : '数据库期望 / 路径缺失'}</span>
                <span>
                  {toNumber(orbitDatabase.db_expected_stem_count)} / {orbitDbMissingPathCount}
                </span>
              </div>
              <div className="health-card-row">
                <span>{en ? 'Pool mismatches' : '池不一致'}</span>
                <span>{orbitMismatchCount}</span>
              </div>
              <div className="health-card-row">
                <span>{en ? 'Source-only' : '仅源存在'}</span>
                <span>{orbitSourceWithoutEnviCount}</span>
              </div>
              <div className="health-card-row">
                <span>{en ? 'Production TXT only' : '仅生产 TXT 存在'}</span>
                <span>{orbitEnviWithoutSourceCount}</span>
              </div>
              <div className="health-card-row">
                <span>{en ? 'DB missing in production TXT' : '数据库在生产 TXT 缺失'}</span>
                <span>{orbitDbMissingEnviCount}</span>
              </div>
              <div className="health-card-row">
                <span>{en ? 'Duplicate stems / scan errors' : '重复 stem / 扫描异常'}</span>
                <span>{orbitDuplicateCount} / {orbitScanErrorCount}</span>
              </div>

              <div className="health-card-note" style={{ marginTop: 4 }}>
                {en
                  ? 'LT-1 orbit scans synchronize the production TXT pool for ENVI/SARscape and Gamma. S1 EOF files remain registered as source orbit assets.'
                  : 'LT-1 精轨扫描会同步 ENVI/SARscape 与 Gamma 共用的生产 TXT 池；S1 EOF 只登记为源精轨资产。'}
              </div>
              <div className="health-card-note">{en ? 'Source path: ' : '源目录路径：'}{formatPathText(orbitSource.path)}</div>
              <div className="health-card-note">{en ? 'Production TXT pool: ' : '生产 TXT 池：'}{formatPathText(orbitPools.envi?.path)}</div>

              {orbitDuplicateCount > 0 && (
                <div className="health-card-note warn">
                  {en
                    ? `Duplicate stems detected during scan: ${orbitDuplicateCount}`
                    : `扫描时发现重复 stem：${orbitDuplicateCount}`}
                </div>
              )}
              {orbitDbFlagIssueCount > 0 && (
                <div className="health-card-note warn">
                  {en
                    ? `DB flag/path anomalies: has_orbit=true but path missing ${toNumber(orbitDatabase.has_orbit_but_missing_path_count)}, has_orbit=false but path present ${toNumber(orbitDatabase.without_orbit_but_path_present_count)}`
                    : `数据库标记异常：has_orbit_data=true 但路径缺失 ${toNumber(orbitDatabase.has_orbit_but_missing_path_count)} 条，has_orbit_data=false 但路径存在 ${toNumber(orbitDatabase.without_orbit_but_path_present_count)} 条`}
                </div>
              )}
              {orbitDbMissingPathCount > 0 && (
                <div className="health-card-note error">
                  {en
                    ? `DB orbit_file_path missing on disk: ${orbitDbMissingPathCount} / ${toNumber(orbitDatabase.distinct_orbit_path_count)}`
                    : `数据库 orbit_file_path 指向不存在文件：${orbitDbMissingPathCount} / ${toNumber(orbitDatabase.distinct_orbit_path_count)}`}
                </div>
              )}
              {orbitBadSourceSampleCount > 0 && (
                <div className="health-card-note error">
                  {en
                    ? `Sampled source TXT with corruption signals: ${orbitBadSourceSampleCount}`
                    : `抽样检测到损坏信号的源 TXT：${orbitBadSourceSampleCount}`}
                </div>
              )}
              {orbitDatabase.sample_missing_in_envi?.length > 0 && (
                <div className="health-card-note error">
                  {en ? 'DB expected but ENVI pool missing: ' : '数据库期望但 ENVI 池缺失：'}
                  {orbitDatabase.sample_missing_in_envi.slice(0, 5).join(', ')}
                </div>
              )}
              {orbitBadSourceSamples.slice(0, 5).map((item) => (
                <div key={`orbit-bad-source-${item.name}`} className="health-card-note error">
                  {item.name} - {item.error || (en ? 'Corruption signal detected' : '检测到损坏信号')}
                  {renderOrbitSourceIssueDetails(item, en, formatPathText)}
                </div>
              ))}
              {(orbitConsistency.mismatches || []).slice(0, 5).map((item) => (
                <div key={item.name} className="health-card-note error">
                  {item.name} - {item.issue}
                  {item.envi_path && (
                    <div style={{ color: '#64748b' }}>
                      {en ? 'ENVI: ' : 'ENVI：'}{formatPathText(item.envi_path)}
                    </div>
                  )}
                </div>
              ))}
              {orbitConsistency.mismatch_count > 5 && (
                <div className="health-card-note warn">
                  {en
                    ? `...and ${orbitConsistency.mismatch_count - 5} more mismatches`
                    : `...还有 ${orbitConsistency.mismatch_count - 5} 项不一致`}
                </div>
              )}
              {(orbitDatabase.path_errors || []).slice(0, 3).map((item, index) => (
                <div key={`db-path-error-${index}`} className="health-card-note error">
                  {item}
                </div>
              ))}
              {(orbitSource.errors || []).slice(0, 3).map((item, index) => (
                <div key={`orbit-source-error-${index}`} className="health-card-note error">
                  {en ? 'Source scan error: ' : '源目录扫描异常：'}{item}
                </div>
              ))}
              {(orbitPools.envi?.errors || []).slice(0, 3).map((item, index) => (
                <div key={`orbit-envi-error-${index}`} className="health-card-note error">
                  {en ? 'ENVI pool scan error: ' : 'ENVI 池扫描异常：'}{item}
                </div>
              ))}

              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
                <button
                  onClick={async () => {
                    setOrbitSyncing(true);
                    setOrbitSyncResult(null);
                    try {
                      const result = await syncOrbitPools();
                      setOrbitSyncResult(result);
                      await refreshOrbitStatus();
                    } catch (e) {
                      setOrbitSyncResult({ error: e.response?.data?.detail || e.message });
                    } finally {
                      setOrbitSyncing(false);
                    }
                  }}
                  disabled={orbitSyncing}
                  style={{ padding: '4px 12px', background: '#1890ff', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer', fontSize: 12 }}
                >
                  {orbitSyncing ? (en ? 'Checking...' : '检查中...') : (en ? 'Check Consistency' : '精轨一致性检查')}
                </button>
              </div>

              {orbitSyncResult && (
                <div style={{ marginTop: 8, fontSize: 11, color: '#475569', padding: '8px 10px', background: '#f8fafc', borderRadius: 6 }}>
                  {orbitSyncResult.error ? (
                    <div className="health-card-note error">{orbitSyncResult.error}</div>
                  ) : (
                    <>
                      <div className={`health-card-note ${orbitSyncResult.healthy ? 'ok' : 'error'}`}>
                        {orbitSyncResult.healthy
                          ? (en ? 'Pools are consistent.' : '本地池一致。')
                          : (en ? `Detected ${toNumber(orbitSyncResult.mismatch_count)} mismatches.` : `检测到 ${toNumber(orbitSyncResult.mismatch_count)} 项不一致。`)}
                      </div>
                      <div className="health-card-note">
                        {en
                          ? `TXT ${toNumber(orbitSyncResult.envi?.total)}, scan errors ${toNumber(orbitSyncResult.error_count)}`
                          : `TXT ${toNumber(orbitSyncResult.envi?.total)} 项，扫描异常 ${toNumber(orbitSyncResult.error_count)} 项`}
                      </div>
                      {(orbitSyncResult.mismatches || []).slice(0, 5).map((item, index) => (
                        <div key={`orbit-check-mismatch-${index}`} className="health-card-note error">
                          {item.name} - {item.issue}
                          {item.envi_path && (
                            <div style={{ color: '#64748b' }}>
                              {en ? 'ENVI: ' : 'ENVI：'}{formatPathText(item.envi_path)}
                            </div>
                          )}
                        </div>
                      ))}
                      {(orbitSyncResult.errors || []).slice(0, 3).map((item, index) => (
                        <div key={`orbit-check-error-${index}`} className="health-card-note error">
                          {item}
                        </div>
                      ))}
                    </>
                  )}
                </div>
              )}
                </>
              ) : (
                <div className="health-card-note">展开后点击“加载诊断”获取旧精轨池状态。</div>
              )}
            </details>
          )}
              </div>
            </section>

          {/* 系统维护 */}
            <section className="health-section">
              <div className="health-section-header">
                <div>
                  <h3>维护与审计</h3>
                  <p>低频维护动作与日志审计集中管理，避免与实时健康状态混杂。</p>
                </div>
              </div>
              <div className="health-maintenance-stack">
                {isAdmin && (
                  <div className="health-action-card">
                    <div>
                      <h4>会话记录维护</h4>
                      <p>清理已过期或已撤销的登录会话，不影响当前有效登录。</p>
                      {cleanupResult && (
                        <div className={`health-card-note ${cleanupResult.ok ? 'ok' : 'error'}`}>
                          {cleanupResult.message}
                        </div>
                      )}
                    </div>
                    <button
                      className="health-action-button"
                      onClick={async () => {
                        setCleanupLoading(true);
                        setCleanupResult(null);
                        try {
                          const data = await cleanupSessions();
                          setCleanupResult({ ok: true, message: data.message || `已清理 ${data.deleted_count} 条` });
                        } catch (e) {
                          setCleanupResult({ ok: false, message: e.message || '清理失败' });
                        } finally {
                          setCleanupLoading(false);
                        }
                      }}
                      disabled={cleanupLoading}
                    >
                      {cleanupLoading ? '清理中...' : '执行清理'}
                    </button>
                  </div>
                )}

                {/* 日志管理 */}
                <LogManagementPanel isAdmin={isAdmin} />
              </div>
            </section>
          </div>
        </>
      )}
    </div>
  );
};

export default HealthCheckPanel;
