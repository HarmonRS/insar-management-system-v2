import React, { useCallback, useEffect, useRef, useState } from 'react';
import './App.css';
import { getHealth } from './api/health';
import { getStatistics } from './api/stats';
import { cleanupSessions } from './api/auth';
import { syncWaterScenesFromDisk } from './api/water';
import { listEngines, runWslCheck } from './api/dinsarProduction';
import { getOrbitStatus, syncOrbitPools } from './api/orbit';
import LogManagementPanel from './LogManagementPanel';
import DinsarCatalogPanel from './components/DinsarCatalogPanel';

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

const HEALTH_PANEL_POLL_INTERVAL_MS = 30000;

const HealthCheckPanel = ({ language = 'zh', currentUser }) => {
  const en = language === 'en';
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
  const [orbitRepairing, setOrbitRepairing] = useState(false);
  const [orbitQuarantining, setOrbitQuarantining] = useState(false);
  const [orbitSyncResult, setOrbitSyncResult] = useState(null);
  const statusFetchInFlightRef = useRef(false);

  const refreshOrbitStatus = useCallback(async () => {
    try {
      const data = await getOrbitStatus();
      setOrbitStatus(data);
    } catch (err) {
      setOrbitStatus(null);
      setOrbitSyncResult({ error: err.response?.data?.detail || err.message || (en ? 'Failed to fetch orbit status' : '精轨状态获取失败') });
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
        setError(err.response?.data?.detail || err.message || (en ? 'Health check failed' : '运维自检失败'));
        setStatus(null);
      }

      try {
        const statsData = await getStatistics(force);
        setConsistencySummary(buildConsistencySummary(statsData, en));
        setConsistencyError('');
      } catch (err) {
        setConsistencySummary(null);
        setConsistencyError(err.response?.data?.detail || err.message || (en ? 'Failed to fetch consistency stats' : '一致性统计获取失败'));
      }

      // 引擎状态独立加载，不影响主健康检查。
      await refreshEngineStatus();

      // 轨道目录状态由专门接口维护，失败时由 refreshOrbitStatus 自己写回 UI。
      await refreshOrbitStatus();
    } finally {
      setLoading(false);
      statusFetchInFlightRef.current = false;
    }
  }, [en, refreshEngineStatus, refreshOrbitStatus]);

  useEffect(() => {
    void fetchStatus();
    const timer = setInterval(() => {
      if (syncLoading || cleanupLoading || wslChecking || orbitSyncing || orbitRepairing || orbitQuarantining) {
        return;
      }
      void fetchStatus();
    }, HEALTH_PANEL_POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [cleanupLoading, fetchStatus, orbitQuarantining, orbitRepairing, orbitSyncing, syncLoading, wslChecking]);

  const renderBadge = (ok, label = '') => (
    <span className={`health-badge ${ok ? 'ok' : 'fail'}`}>
      {ok ? (en ? 'OK' : '正常') : (en ? 'Error' : '异常')}{label ? ` · ${label}` : ''}
    </span>
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
  const sourceRoots = asObject(status?.source_roots);
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
  const orbitDbMissingIsce2Count = toNumber(orbitDatabase.stems_missing_in_isce2_count);
  const orbitDbMissingPathCount = toNumber(orbitDatabase.db_missing_path_count);
  const orbitDbFlagIssueCount =
    toNumber(orbitDatabase.has_orbit_but_missing_path_count) +
    toNumber(orbitDatabase.without_orbit_but_path_present_count);
  const orbitScanErrorCount =
    (orbitSource.errors?.length || 0) +
    (orbitPools.envi?.errors?.length || 0) +
    (orbitPools.isce2?.errors?.length || 0);
  const orbitDuplicateCount =
    toNumber(orbitSource.duplicate_count) +
    toNumber(orbitPools.envi?.duplicate_count) +
    toNumber(orbitPools.isce2?.duplicate_count);
  const orbitSuspectBadCount = toNumber(orbitSource.suspect_bad_count);
  const orbitSourceWithoutEnviCount = toNumber(orbitSource.source_without_envi_count);
  const orbitEnviWithoutSourceCount = toNumber(orbitSource.envi_without_source_count);
  const orbitIsce2WithoutSourceCount = toNumber(orbitSource.isce2_without_source_count);
  const orbitQuarantinePath = orbitSource.quarantine_path || orbitStatus?.source_gaps?.quarantine_path;
  const orbitOverallHealthy = Boolean(
    orbitStatus &&
    orbitMismatchCount === 0 &&
    orbitDbMissingEnviCount === 0 &&
    orbitDbMissingIsce2Count === 0 &&
    orbitDbMissingPathCount === 0 &&
    orbitDbFlagIssueCount === 0 &&
    orbitScanErrorCount === 0 &&
    orbitSuspectBadCount === 0 &&
    orbitSourceWithoutEnviCount === 0 &&
    orbitEnviWithoutSourceCount === 0 &&
    orbitIsce2WithoutSourceCount === 0
  );

  return (
    <div className="health-panel">
      <div className="health-header">
        <div>
          <div className="health-title">{en ? 'System Health' : '运维自检'}</div>
          <div className="health-subtitle">
            {lastChecked ? `${en ? 'Last check: ' : '上次检查：'}${lastChecked.toLocaleString()}` : (en ? 'Not checked yet' : '尚未检查')}
          </div>
        </div>
        <button className="health-refresh" onClick={() => fetchStatus({ force: true })} disabled={loading}>
          {loading ? (en ? 'Checking...' : '检查中...') : (en ? 'Refresh' : '刷新')}
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
              <span>{en ? 'Overall Status' : '总体状态'}</span>
              {renderBadge(status.ok)}
            </div>
            <div className="health-summary-item">
              <span>{en ? 'Timestamp' : '时间戳'}</span>
              <span>{formatIso(status.timestamp)}</span>
            </div>
            <div className="health-summary-item">
              <span>{en ? 'Consistency Issues' : '一致性异常'}</span>
              {renderBadge(
                !consistencySummary || consistencySummary.total === 0,
                consistencySummary ? `${consistencySummary.total} ${en ? 'items' : '项'}` : (en ? 'Unknown' : '未知')
              )}
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

            <DinsarCatalogPanel compact readOnly={!isAdmin} />

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
                  {en ? 'Catalog-first reads and legacy compat rows are aligned.' : '目录事实源与旧兼容视图当前一致。'}
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
          </div>

          {/* 精轨管理 */}
          {isAdmin && (
            <div className="health-card">
              <div className="health-card-title">{en ? 'Precise Orbit Management' : '精轨管理'}</div>
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
                <span>{en ? 'Source / ENVI / ISCE2' : '源目录 / ENVI / ISCE2'}</span>
                <span>
                  {toNumber(orbitSource.total_source)} / {toNumber(orbitPools.envi?.total)} / {toNumber(orbitPools.isce2?.total)}
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
                <span>{en ? 'Suspect bad TXT / source-only' : '疑似坏 TXT / 仅源存在'}</span>
                <span>{orbitSuspectBadCount} / {orbitSourceWithoutEnviCount}</span>
              </div>
              <div className="health-card-row">
                <span>{en ? 'ENVI-only / ISCE2-only' : '仅 ENVI / 仅 ISCE2'}</span>
                <span>{orbitEnviWithoutSourceCount} / {orbitIsce2WithoutSourceCount}</span>
              </div>
              <div className="health-card-row">
                <span>{en ? 'DB missing in ENVI / ISCE2' : '数据库在 ENVI / ISCE2 缺失'}</span>
                <span>{orbitDbMissingEnviCount} / {orbitDbMissingIsce2Count}</span>
              </div>
              <div className="health-card-row">
                <span>{en ? 'Duplicate stems / scan errors' : '重复 stem / 扫描异常'}</span>
                <span>{orbitDuplicateCount} / {orbitScanErrorCount}</span>
              </div>

              <div className="health-card-note" style={{ marginTop: 4 }}>
                {en
                  ? 'Orbit scan writes ENVI TXT and ISCE2 XML pools automatically. This panel now shows source, pool, and database consistency together.'
                  : '“扫描精轨”会自动同步 ENVI TXT 和 ISCE2 XML。本卡片同时展示源目录、本地池和数据库三侧的一致性。'}
              </div>
              <div className="health-card-note">{en ? 'Source path: ' : '源目录路径：'}{formatPathText(orbitSource.path)}</div>
              <div className="health-card-note">{en ? 'ENVI pool: ' : 'ENVI 池：'}{formatPathText(orbitPools.envi?.path)}</div>
              <div className="health-card-note">{en ? 'ISCE2 pool: ' : 'ISCE2 池：'}{formatPathText(orbitPools.isce2?.path)}</div>
              <div className="health-card-note">{en ? 'LANDSAR pool: ' : 'LANDSAR 池：'}{formatPathText(orbitPools.landsar?.path)}</div>
              <div className="health-card-note">{en ? 'Quarantine path: ' : '隔离目录：'}{formatPathText(orbitQuarantinePath)}</div>

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
              {orbitSuspectBadCount > 0 && (
                <div className="health-card-note warn">
                  {en
                    ? `Suspect bad source TXT (source exists but ISCE2 XML missing): ${orbitSuspectBadCount}`
                    : `疑似坏源 TXT（源文件存在但 ISCE2 XML 缺失）：${orbitSuspectBadCount}`}
                </div>
              )}
              {orbitDatabase.sample_missing_in_envi?.length > 0 && (
                <div className="health-card-note error">
                  {en ? 'DB expected but ENVI pool missing: ' : '数据库期望但 ENVI 池缺失：'}
                  {orbitDatabase.sample_missing_in_envi.slice(0, 5).join(', ')}
                </div>
              )}
              {orbitDatabase.sample_missing_in_isce2?.length > 0 && (
                <div className="health-card-note error">
                  {en ? 'DB expected but ISCE2 pool missing: ' : '数据库期望但 ISCE2 池缺失：'}
                  {orbitDatabase.sample_missing_in_isce2.slice(0, 5).join(', ')}
                </div>
              )}
              {(orbitSource.suspect_bad_samples || []).slice(0, 5).map((item) => (
                <div key={`orbit-suspect-bad-${item.name}`} className="health-card-note warn">
                  {item.name}
                  {item.source_path && (
                    <div style={{ color: '#64748b' }}>
                      {en ? 'Source: ' : '源文件：'}{formatPathText(item.source_path)}
                    </div>
                  )}
                  {item.envi_path && (
                    <div style={{ color: '#64748b' }}>
                      {en ? 'ENVI: ' : 'ENVI：'}{formatPathText(item.envi_path)}
                    </div>
                  )}
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
                  {item.isce2_path && (
                    <div style={{ color: '#64748b' }}>
                      {en ? 'ISCE2: ' : 'ISCE2：'}{formatPathText(item.isce2_path)}
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
              {(orbitPools.isce2?.errors || []).slice(0, 3).map((item, index) => (
                <div key={`orbit-isce2-error-${index}`} className="health-card-note error">
                  {en ? 'ISCE2 pool scan error: ' : 'ISCE2 池扫描异常：'}{item}
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
                  disabled={orbitSyncing || orbitRepairing || orbitQuarantining}
                  style={{ padding: '4px 12px', background: '#1890ff', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer', fontSize: 12 }}
                >
                  {orbitSyncing ? (en ? 'Checking...' : '检查中...') : (en ? 'Check Consistency' : '精轨一致性检查')}
                </button>
                <button
                  onClick={async () => {
                    setOrbitRepairing(true);
                    setOrbitSyncResult(null);
                    try {
                      const result = await syncOrbitPools({ repair: true });
                      setOrbitSyncResult(result);
                      await refreshOrbitStatus();
                    } catch (e) {
                      setOrbitSyncResult({ error: e.response?.data?.detail || e.message });
                    } finally {
                      setOrbitRepairing(false);
                    }
                  }}
                  disabled={orbitSyncing || orbitRepairing || orbitQuarantining}
                  style={{ padding: '4px 12px', background: '#0f766e', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer', fontSize: 12 }}
                >
                  {orbitRepairing ? (en ? 'Repairing...' : '修复中...') : (en ? 'Repair Missing XML' : '修复缺失 XML')}
                </button>
                <button
                  onClick={async () => {
                    setOrbitQuarantining(true);
                    setOrbitSyncResult(null);
                    try {
                      const result = await syncOrbitPools({ quarantine_bad: true });
                      setOrbitSyncResult(result);
                      await refreshOrbitStatus();
                    } catch (e) {
                      setOrbitSyncResult({ error: e.response?.data?.detail || e.message });
                    } finally {
                      setOrbitQuarantining(false);
                    }
                  }}
                  disabled={orbitSyncing || orbitRepairing || orbitQuarantining}
                  style={{ padding: '4px 12px', background: '#b45309', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer', fontSize: 12 }}
                >
                  {orbitQuarantining ? (en ? 'Quarantining...' : '隔离中...') : (en ? 'Quarantine Bad TXT' : '隔离坏精轨')}
                </button>
              </div>

              {orbitSyncResult && (
                <div style={{ marginTop: 8, fontSize: 11, color: '#475569', padding: '8px 10px', background: '#f8fafc', borderRadius: 6 }}>
                  {orbitSyncResult.error ? (
                    <div className="health-card-note error">{orbitSyncResult.error}</div>
                  ) : (
                    <>
                      {'confirmed_bad_count' in orbitSyncResult ? (
                        <>
                          <div className={`health-card-note ${toNumber(orbitSyncResult.confirmed_bad_count) === 0 ? 'ok' : 'warn'}`}>
                            {en
                              ? `Quarantine finished. Confirmed bad TXT: ${toNumber(orbitSyncResult.confirmed_bad_count)} / validated ${toNumber(orbitSyncResult.validated_count)}`
                              : `隔离完成。已确认坏 TXT ${toNumber(orbitSyncResult.confirmed_bad_count)} 项 / 已校验 ${toNumber(orbitSyncResult.validated_count)} 项`}
                          </div>
                          <div className="health-card-note">
                            {en
                              ? `Quarantine root: ${formatPathText(orbitSyncResult.quarantine_root)}`
                              : `隔离目录：${formatPathText(orbitSyncResult.quarantine_root)}`}
                          </div>
                          {(orbitSyncResult.confirmed_bad || []).slice(0, 5).map((item, index) => (
                            <div key={`orbit-quarantine-bad-${index}`} className="health-card-note error">
                              {item.name} - {item.error}
                              <div style={{ color: '#64748b' }}>
                                {en ? 'Source: ' : '源文件：'}{formatPathText(item.source)}
                              </div>
                              <div style={{ color: '#64748b' }}>
                                {en ? 'NUL bytes: ' : 'NUL 字节：'}{toNumber(item.nul_byte_count)}
                                {item.tail_has_nul_bytes ? (en ? ' (tail contains NUL)' : '（文件尾含 NUL）') : ''}
                              </div>
                              {item.quarantined_source && (
                                <div style={{ color: '#64748b' }}>
                                  {en ? 'Moved source to: ' : '源文件已移至：'}{formatPathText(item.quarantined_source)}
                                </div>
                              )}
                              {item.quarantined_envi && (
                                <div style={{ color: '#64748b' }}>
                                  {en ? 'Moved ENVI to: ' : 'ENVI 已移至：'}{formatPathText(item.quarantined_envi)}
                                </div>
                              )}
                              {item.quarantined_isce2 && (
                                <div style={{ color: '#64748b' }}>
                                  {en ? 'Moved ISCE2 to: ' : 'ISCE2 已移至：'}{formatPathText(item.quarantined_isce2)}
                                </div>
                              )}
                            </div>
                          ))}
                          {(orbitSyncResult.skipped_valid || []).slice(0, 3).map((item, index) => (
                            <div key={`orbit-quarantine-skip-${index}`} className="health-card-note ok">
                              {item.name} - {item.reason}
                            </div>
                          ))}
                          {(orbitSyncResult.errors || []).slice(0, 5).map((item, index) => (
                            <div key={`orbit-quarantine-error-${index}`} className="health-card-note error">
                              {item.name} - {item.scope} - {item.error}
                            </div>
                          ))}
                        </>
                      ) : 'before' in orbitSyncResult ? (
                        <>
                          <div className={`health-card-note ${orbitSyncResult.healthy ? 'ok' : 'warn'}`}>
                            {en
                              ? `Repair finished. Before mismatches: ${toNumber(orbitSyncResult.before?.mismatch_count)}, after mismatches: ${toNumber(orbitSyncResult.after?.mismatch_count)}`
                              : `修复完成。修复前不一致 ${toNumber(orbitSyncResult.before?.mismatch_count)} 项，修复后不一致 ${toNumber(orbitSyncResult.after?.mismatch_count)} 项`}
                          </div>
                          <div className="health-card-note">
                            {en
                              ? `Recovered from ENVI TXT: ${(orbitSyncResult.repaired_from_envi || []).length}, repair errors: ${toNumber(orbitSyncResult.repair_error_count)}`
                              : `从 ENVI TXT 补转成功 ${(orbitSyncResult.repaired_from_envi || []).length} 项，修复失败 ${toNumber(orbitSyncResult.repair_error_count)} 项`}
                          </div>
                          <div className="health-card-note">
                            {en
                              ? `Source scan ${toNumber(orbitSyncResult.sync_result?.total_source)}, ENVI copied ${(orbitSyncResult.sync_result?.envi?.copied || []).length}, ENVI refreshed ${(orbitSyncResult.sync_result?.envi?.updated || []).length}, ISCE2 converted ${(orbitSyncResult.sync_result?.isce2?.converted || []).length}, ISCE2 refreshed ${(orbitSyncResult.sync_result?.isce2?.reconverted || []).length}`
                              : `源目录扫描 ${toNumber(orbitSyncResult.sync_result?.total_source)} 项，ENVI 新增 ${(orbitSyncResult.sync_result?.envi?.copied || []).length} 项、刷新 ${(orbitSyncResult.sync_result?.envi?.updated || []).length} 项，ISCE2 新增转换 ${(orbitSyncResult.sync_result?.isce2?.converted || []).length} 项、重转 ${(orbitSyncResult.sync_result?.isce2?.reconverted || []).length} 项`}
                          </div>
                          {(orbitSyncResult.repaired_from_envi || []).slice(0, 5).length > 0 && (
                            <div className="health-card-note ok">
                              {en ? 'Recovered stems: ' : '已补转 stem：'}
                              {(orbitSyncResult.repaired_from_envi || []).slice(0, 5).join(', ')}
                            </div>
                          )}
                          {(orbitSyncResult.repair_errors || []).slice(0, 3).map((item, index) => (
                            <div key={`orbit-repair-error-${index}`} className="health-card-note error">
                              {item.name} - {item.error}
                              <div style={{ color: '#64748b' }}>
                                {en ? 'Source: ' : '源文件：'}{formatPathText(item.source)}
                              </div>
                            </div>
                          ))}
                          {(orbitSyncResult.sync_result?.source?.errors || []).slice(0, 3).map((item, index) => (
                            <div key={`orbit-repair-source-error-${index}`} className="health-card-note error">
                              {en ? 'Source scan error: ' : '源目录扫描异常：'}{item}
                            </div>
                          ))}
                          {(orbitSyncResult.sync_result?.invalid_sources || []).slice(0, 5).map((item, index) => (
                            <div key={`orbit-repair-invalid-${index}`} className="health-card-note error">
                              {item.name} - {item.error}
                              <div style={{ color: '#64748b' }}>
                                {en ? 'Source: ' : '源文件：'}{formatPathText(item.source)}
                              </div>
                            </div>
                          ))}
                          {(orbitSyncResult.sync_result?.isce2?.errors || []).slice(0, 3).map((item, index) => (
                            <div key={`orbit-repair-isce2-error-${index}`} className="health-card-note error">
                              {item.file} - {item.error}
                            </div>
                          ))}
                        </>
                      ) : (
                        <>
                          <div className={`health-card-note ${orbitSyncResult.healthy ? 'ok' : 'error'}`}>
                            {orbitSyncResult.healthy
                              ? (en ? 'Pools are consistent.' : '本地池一致。')
                              : (en ? `Detected ${toNumber(orbitSyncResult.mismatch_count)} mismatches.` : `检测到 ${toNumber(orbitSyncResult.mismatch_count)} 项不一致。`)}
                          </div>
                          <div className="health-card-note">
                            {en
                              ? `ENVI ${toNumber(orbitSyncResult.envi?.total)}, ISCE2 ${toNumber(orbitSyncResult.isce2?.total)}, scan errors ${toNumber(orbitSyncResult.error_count)}`
                              : `ENVI ${toNumber(orbitSyncResult.envi?.total)} 项，ISCE2 ${toNumber(orbitSyncResult.isce2?.total)} 项，扫描异常 ${toNumber(orbitSyncResult.error_count)} 项`}
                          </div>
                          {(orbitSyncResult.mismatches || []).slice(0, 5).map((item, index) => (
                            <div key={`orbit-check-mismatch-${index}`} className="health-card-note error">
                              {item.name} - {item.issue}
                              {item.envi_path && (
                                <div style={{ color: '#64748b' }}>
                                  {en ? 'ENVI: ' : 'ENVI：'}{formatPathText(item.envi_path)}
                                </div>
                              )}
                              {item.isce2_path && (
                                <div style={{ color: '#64748b' }}>
                                  {en ? 'ISCE2: ' : 'ISCE2：'}{formatPathText(item.isce2_path)}
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
                    </>
                  )}
                </div>
              )}
            </div>
          )}

          {/* 系统维护 */}
          {isAdmin && (
            <div className="health-card">
              <div className="health-card-title">{en ? 'System Maintenance' : '系统维护'}</div>
              <div className="health-card-row" style={{ alignItems: 'center' }}>
                <span>{en ? 'Expired Sessions' : '过期会话清理'}</span>
                <button
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
                  style={{ padding: '4px 12px', background: '#1890ff', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer', fontSize: 12 }}
                >
                  {cleanupLoading ? (en ? 'Cleaning...' : '清理中...') : (en ? 'Clean Up' : '清理')}
                </button>
              </div>
              {cleanupResult && (
                <div className={`health-card-note ${cleanupResult.ok ? 'ok' : 'error'}`} style={{ marginTop: 4 }}>
                  {cleanupResult.message}
                </div>
              )}
              <div className="health-card-note" style={{ marginTop: 4, color: '#94a3b8' }}>
                {en ? 'Delete expired and revoked session records from the database.' : '删除数据库中已过期和已撤销的会话记录，释放空间。'}
              </div>
            </div>
          )}

          {/* 日志管理 */}
          <div className="health-card">
            <LogManagementPanel isAdmin={isAdmin} />
          </div>
        </>
      )}
    </div>
  );
};

export default HealthCheckPanel;
