import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  cleanupMaintenanceTask,
  getMaintenanceTaskDiagnosis,
  listMaintenanceTasks,
  previewMaintenanceCleanup,
} from './api/opsMaintenance';

const TASK_TYPES = [
  ['', '全部类型'],
  ['LANDSAR_RUN', 'LandSAR D-InSAR'],
  ['LANDSAR_CLUSTER_RUN', 'LandSAR 集群'],
  ['PYINT_RUN', 'PyINT/Gamma'],
  ['IDL_RUN_DINSAR', 'SARscape D-InSAR'],
  ['COPY_DATA', '数据准备'],
  ['PAIRING_CACHE_REBUILD', '配对缓存'],
];

const STATUSES = [
  ['', '全部状态'],
  ['FAILED', '失败'],
  ['PARTIAL_SUCCESS', '部分成功'],
  ['CANCELLED', '已取消'],
  ['RUNNING', '运行中'],
  ['PENDING', '等待中'],
];

const formatDate = (value) => {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { hour12: false });
};

const formatCount = (value) => Number(value || 0).toLocaleString('zh-CN');

const toneForStatus = (status) => {
  const value = String(status || '').toUpperCase();
  if (value === 'FAILED') return 'danger';
  if (value === 'PARTIAL_SUCCESS' || value === 'CANCELLED') return 'warn';
  if (value === 'COMPLETED') return 'ok';
  if (value === 'RUNNING' || value === 'PENDING') return 'info';
  return 'neutral';
};

const StatusBadge = ({ status }) => (
  <span className={`ops-task-badge tone-${toneForStatus(status)}`}>{status || '-'}</span>
);

const CountLine = ({ counts = {} }) => (
  <span>
    成功 {formatCount(counts.completed_items)} / 失败 {formatCount(counts.failed_items)} / 等待 {formatCount(counts.pending_items)} / 运行 {formatCount(counts.running_items)}
  </span>
);

const DatabasePreview = ({ counts = {} }) => {
  const entries = Object.entries(counts).filter(([, value]) => Number(value || 0) > 0);
  if (!entries.length) return <div className="ops-task-muted">没有将删除的数据库记录。</div>;
  return (
    <div className="ops-task-preview-grid">
      {entries.map(([key, value]) => (
        <div key={key} className="ops-task-preview-cell">
          <span>{key}</span>
          <strong>{formatCount(value)}</strong>
        </div>
      ))}
    </div>
  );
};

const DiskPreview = ({ paths = [] }) => {
  if (!paths.length) return <div className="ops-task-muted">没有将删除的磁盘路径。</div>;
  return (
    <div className="ops-task-path-list">
      {paths.map(item => (
        <div key={`${item.kind}-${item.path}`} className={`ops-task-path ${item.allowed ? '' : 'blocked'}`}>
          <span>{item.kind || 'path'}</span>
          <code>{item.path}</code>
          <em>{item.exists ? '存在' : '不存在'} / {item.allowed ? '允许' : '禁止'}</em>
        </div>
      ))}
    </div>
  );
};

const OpsTaskMaintenancePanel = ({ isAdmin }) => {
  const [filters, setFilters] = useState({ task_type: '', status: '' });
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [selectedTaskId, setSelectedTaskId] = useState('');
  const [diagnosis, setDiagnosis] = useState(null);
  const [diagnosisLoading, setDiagnosisLoading] = useState(false);
  const [preview, setPreview] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [cleanupLoading, setCleanupLoading] = useState(false);
  const [cleanupResult, setCleanupResult] = useState(null);
  const [panelMessage, setPanelMessage] = useState(null);

  const loadTasks = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await listMaintenanceTasks({
        task_type: filters.task_type || undefined,
        status: filters.status || undefined,
        limit: 80,
      });
      setTasks(Array.isArray(data?.items) ? data.items : []);
    } catch (err) {
      setError(err.response?.data?.detail || err.message || '任务维护列表加载失败');
    } finally {
      setLoading(false);
    }
  }, [filters]);

  useEffect(() => {
    loadTasks();
  }, [loadTasks]);

  const selectedTask = useMemo(
    () => tasks.find(item => item.task_id === selectedTaskId) || null,
    [tasks, selectedTaskId],
  );

  const loadDiagnosis = useCallback(async (taskId) => {
    setSelectedTaskId(taskId);
    setDiagnosis(null);
    setPreview(null);
    setCleanupResult(null);
    setPanelMessage(null);
    setDiagnosisLoading(true);
    try {
      const data = await getMaintenanceTaskDiagnosis(taskId);
      setDiagnosis(data);
    } catch (err) {
      setCleanupResult({ ok: false, message: err.response?.data?.detail || err.message || '诊断加载失败' });
    } finally {
      setDiagnosisLoading(false);
    }
  }, []);

  const loadPreview = useCallback(async () => {
    if (!selectedTaskId) return;
    setPreviewLoading(true);
    setCleanupResult(null);
    try {
      const data = await previewMaintenanceCleanup(selectedTaskId);
      setPreview(data);
    } catch (err) {
      setCleanupResult({ ok: false, message: err.response?.data?.detail || err.message || '清理预览失败' });
    } finally {
      setPreviewLoading(false);
    }
  }, [selectedTaskId]);

  const executeCleanup = useCallback(async () => {
    if (!selectedTaskId || !preview || preview.blocked || !isAdmin) return;
    setCleanupLoading(true);
    setCleanupResult(null);
    try {
      const data = await cleanupMaintenanceTask(selectedTaskId, {
        confirm: true,
        delete_task_records: true,
        delete_logs: true,
        delete_production_records: true,
        delete_result_products: true,
        delete_production_dirs: true,
        delete_task_pool_dir: true,
      });
      setCleanupResult({
        ok: true,
        message: `清理完成：数据库 ${Object.values(data.deleted_database || {}).reduce((sum, value) => sum + Number(value || 0), 0)} 条，目录 ${(data.deleted_disk?.deleted || []).length} 个。`,
      });
      setPanelMessage({
        ok: true,
        message: `清理完成：数据库 ${Object.values(data.deleted_database || {}).reduce((sum, value) => sum + Number(value || 0), 0)} 条，目录 ${(data.deleted_disk?.deleted || []).length} 个。`,
      });
      setPreview(null);
      setDiagnosis(null);
      setSelectedTaskId('');
      await loadTasks();
    } catch (err) {
      setCleanupResult({ ok: false, message: err.response?.data?.detail || err.message || '清理失败' });
      setPanelMessage({ ok: false, message: err.response?.data?.detail || err.message || '清理失败' });
    } finally {
      setCleanupLoading(false);
    }
  }, [isAdmin, loadTasks, preview, selectedTaskId]);

  const abnormalCount = tasks.length;
  const cleanableCount = tasks.filter(item => item.cleanup_supported).length;
  const blockedCount = tasks.filter(item => !item.cleanup_supported).length;

  return (
    <div className="ops-task-panel">
      <div className="ops-task-header">
        <div>
          <h4>任务维护</h4>
          <p>查看失败、部分成功、取消和残留运行的任务；清理后请回到业务页面重新提交。</p>
        </div>
        <button className="health-action-button" onClick={loadTasks} disabled={loading}>
          {loading ? '刷新中...' : '刷新任务'}
        </button>
      </div>

      <div className="ops-task-summary">
        <div><span>异常任务</span><strong>{formatCount(abnormalCount)}</strong></div>
        <div><span>可清理</span><strong>{formatCount(cleanableCount)}</strong></div>
        <div><span>需复核</span><strong>{formatCount(blockedCount)}</strong></div>
      </div>

      <div className="ops-task-filters">
        <label>
          <span>任务类型</span>
          <select value={filters.task_type} onChange={event => setFilters(prev => ({ ...prev, task_type: event.target.value }))}>
            {TASK_TYPES.map(([value, label]) => <option key={value || 'all'} value={value}>{label}</option>)}
          </select>
        </label>
        <label>
          <span>状态</span>
          <select value={filters.status} onChange={event => setFilters(prev => ({ ...prev, status: event.target.value }))}>
            {STATUSES.map(([value, label]) => <option key={value || 'all'} value={value}>{label}</option>)}
          </select>
        </label>
      </div>

      {error && <div className="health-card-note error">{error}</div>}
      {panelMessage && (
        <div className={`health-card-note ${panelMessage.ok ? 'ok' : 'error'}`}>
          {panelMessage.message}
        </div>
      )}

      <div className="ops-task-table-wrap">
        <table className="ops-task-table">
          <thead>
            <tr>
              <th>状态</th>
              <th>任务</th>
              <th>统计</th>
              <th>问题摘要</th>
              <th>更新时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {tasks.map(task => (
              <tr key={task.task_id} className={task.task_id === selectedTaskId ? 'selected' : ''}>
                <td><StatusBadge status={task.status} /></td>
                <td>
                  <div className="ops-task-name">{task.task_name || task.task_id}</div>
                  <div className="ops-task-muted">{task.task_type} / {task.task_id}</div>
                </td>
                <td><CountLine counts={task.counts} /></td>
                <td>{task.issue_summary || '-'}</td>
                <td>{formatDate(task.updated_at)}</td>
                <td>
                  <button className="health-inline-button" onClick={() => loadDiagnosis(task.task_id)}>
                    诊断
                  </button>
                </td>
              </tr>
            ))}
            {!loading && !tasks.length && (
              <tr>
                <td colSpan={6} className="ops-task-empty">当前没有匹配的异常任务。</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {selectedTaskId && (
        <div className="ops-task-detail">
          <div className="ops-task-detail-header">
            <div>
              <h4>{selectedTask?.task_name || selectedTaskId}</h4>
              <p>{selectedTaskId}</p>
            </div>
            <button className="health-inline-button" onClick={loadPreview} disabled={previewLoading || diagnosisLoading}>
              {previewLoading ? '生成中...' : '生成清理预览'}
            </button>
          </div>

          {diagnosisLoading ? (
            <div className="ops-task-muted">正在加载诊断...</div>
          ) : diagnosis ? (
            <>
              <div className="ops-task-diagnosis">
                {(diagnosis.diagnosis?.findings || []).map(item => (
                  <div key={item} className="health-card-note warn">{item}</div>
                ))}
                {!(diagnosis.diagnosis?.findings || []).length && (
                  <div className="health-card-note ok">未发现明显异常。</div>
                )}
                {(diagnosis.diagnosis?.cleanup_blockers || []).map(item => (
                  <div key={item} className="health-card-note error">{item}</div>
                ))}
              </div>

              <div className="ops-task-two-col">
                <div>
                  <div className="ops-task-section-title">生产统计</div>
                  <DatabasePreview counts={{
                    ...diagnosis.production_item_counts,
                    ...Object.fromEntries(Object.entries(diagnosis.production_execution_counts || {}).map(([key, value]) => [`execution_${key}`, value])),
                  }} />
                </div>
                <div>
                  <div className="ops-task-section-title">最近日志</div>
                  <div className="ops-task-log-list">
                    {(diagnosis.recent_logs || []).slice(0, 8).map(log => (
                      <div key={log.id}>
                        <span>[{formatDate(log.timestamp)}] {log.level}</span>
                        <p>{log.message}</p>
                      </div>
                    ))}
                    {!(diagnosis.recent_logs || []).length && <div className="ops-task-muted">无日志。</div>}
                  </div>
                </div>
              </div>
            </>
          ) : null}

          {preview && (
            <div className="ops-task-preview">
              <div className="ops-task-section-title">清理预览</div>
              {preview.blocked && (
                <div className="health-card-note error">
                  {preview.blockers?.join('；') || '当前任务不允许清理。'}
                </div>
              )}
              <div className="ops-task-two-col">
                <div>
                  <div className="ops-task-section-title">数据库记录</div>
                  <DatabasePreview counts={preview.database_deletes} />
                </div>
                <div>
                  <div className="ops-task-section-title">磁盘路径</div>
                  <DiskPreview paths={preview.disk_deletes} />
                </div>
              </div>
              <div className="ops-task-actions">
                {!isAdmin && <span className="ops-task-muted">仅管理员可执行清理。</span>}
                <button
                  className="health-action-button danger"
                  onClick={executeCleanup}
                  disabled={!isAdmin || cleanupLoading || preview.blocked}
                >
                  {cleanupLoading ? '清理中...' : '确认清理'}
                </button>
              </div>
            </div>
          )}

          {cleanupResult && (
            <div className={`health-card-note ${cleanupResult.ok ? 'ok' : 'error'}`}>
              {cleanupResult.message}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default OpsTaskMaintenancePanel;
