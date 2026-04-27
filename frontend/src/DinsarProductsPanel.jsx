import React, { useCallback, useEffect, useMemo, useState } from 'react';

import { scanDinsarResults } from './api/dinsar';
import { extractDispResults } from './api/idl';
import { clearTaskLogs, deleteTaskLog, getActiveTasks, getRecentTasks, getTaskLogs } from './api/tasks';
import DinsarCatalogPanel from './components/DinsarCatalogPanel';

const PRODUCT_TASK_TYPES = [
  'SCAN_DINSAR',
  'PUBLISH_DINSAR_PRODUCTS',
  'REBUILD_DINSAR_CATALOG',
];

const TASK_TYPE_LABEL = {
  SCAN_DINSAR: 'D-InSAR 结果扫描',
  PUBLISH_DINSAR_PRODUCTS: 'D-InSAR 产物发布',
  REBUILD_DINSAR_CATALOG: 'D-InSAR 目录重建',
};

const STATUS_LABEL = {
  PENDING: '等待中',
  RUNNING: '运行中',
  COMPLETED: '已完成',
  FAILED: '失败',
  CANCELLED: '已取消',
  CANCELED: '已取消',
};

function formatTaskType(taskType) {
  return TASK_TYPE_LABEL[taskType] || taskType || '-';
}

function formatStatus(status) {
  return STATUS_LABEL[status] || status || '-';
}

function getMessageTone(message, fallbackError = false) {
  if (fallbackError) return 'error';
  return /失败|error|Error|ERROR/.test(String(message || '')) ? 'error' : 'success';
}

function getLogTone(level) {
  const normalized = String(level || '').toUpperCase();
  if (normalized === 'ERROR') return 'error';
  if (normalized === 'WARNING' || normalized === 'WARN') return 'warn';
  return 'info';
}

export default function DinsarProductsPanel({ readOnly = false, onJobQueued }) {
  const [extractRootDir, setExtractRootDir] = useState('');
  const [extractDestDir, setExtractDestDir] = useState('');
  const [extractResult, setExtractResult] = useState(null);
  const [extracting, setExtracting] = useState(false);
  const [actionMessage, setActionMessage] = useState('');
  const [actionError, setActionError] = useState(false);
  const [scanning, setScanning] = useState(false);

  const [activeTask, setActiveTask] = useState(null);
  const [recentTask, setRecentTask] = useState(null);
  const [taskLogs, setTaskLogs] = useState([]);
  const [taskLogsLoading, setTaskLogsLoading] = useState(false);
  const [taskLogActionLoading, setTaskLogActionLoading] = useState(false);
  const [taskLogDeletingId, setTaskLogDeletingId] = useState(null);
  const monitoredTask = activeTask || recentTask;
  const logTaskId = monitoredTask?.task_id || '';
  const showingRecentTask = !activeTask && !!recentTask;
  const actionTone = getMessageTone(actionMessage, actionError);

  const loadActiveTask = useCallback(async () => {
    try {
      const data = await getActiveTasks();
      const tasks = Array.isArray(data) ? data : (data?.tasks || []);
      const relevantTask = tasks.find((task) => PRODUCT_TASK_TYPES.includes(task.task_type)) || null;
      setActiveTask(relevantTask);
      return relevantTask;
    } catch {
      setActiveTask(null);
      return null;
    }
  }, []);

  const loadRecentTask = useCallback(async () => {
    try {
      const tasks = await getRecentTasks(PRODUCT_TASK_TYPES, [], 1, 0);
      const nextTask = Array.isArray(tasks) ? (tasks[0] || null) : null;
      setRecentTask(nextTask);
      return nextTask;
    } catch {
      setRecentTask(null);
      return null;
    }
  }, []);

  const loadTaskLogs = useCallback(async (taskId) => {
    if (!taskId) {
      setTaskLogs([]);
      return;
    }
    setTaskLogsLoading(true);
    try {
      const data = await getTaskLogs(taskId, 50, 0);
      setTaskLogs(data?.logs || []);
    } catch {
      setTaskLogs([]);
    } finally {
      setTaskLogsLoading(false);
    }
  }, []);

  const refreshMonitor = useCallback(async () => {
    const [nextActiveTask, nextRecentTask] = await Promise.all([
      loadActiveTask(),
      loadRecentTask(),
    ]);
    const nextTaskId = nextActiveTask?.task_id || nextRecentTask?.task_id || '';
    await loadTaskLogs(nextTaskId);
  }, [loadActiveTask, loadRecentTask, loadTaskLogs]);

  useEffect(() => {
    refreshMonitor();
  }, [refreshMonitor]);

  const handleDeleteTaskLog = useCallback(async (logId) => {
    const taskId = logTaskId;
    if (!taskId || !logId || taskLogActionLoading) return;
    if (!window.confirm('确定要删除这条任务日志吗？')) return;

    setTaskLogDeletingId(logId);
    setTaskLogActionLoading(true);
    try {
      await deleteTaskLog(taskId, logId);
      await loadTaskLogs(taskId);
    } catch (error) {
      setActionMessage(`删除日志失败：${error?.response?.data?.detail || error.message}`);
      setActionError(true);
    } finally {
      setTaskLogDeletingId(null);
      setTaskLogActionLoading(false);
    }
  }, [logTaskId, loadTaskLogs, taskLogActionLoading]);

  const handleClearTaskLogs = useCallback(async () => {
    const taskId = logTaskId;
    if (!taskId || taskLogActionLoading || taskLogs.length === 0) return;
    if (!window.confirm(`确定要清空任务 ${taskId} 的全部日志吗？`)) return;

    setTaskLogActionLoading(true);
    try {
      await clearTaskLogs(taskId);
      await loadTaskLogs(taskId);
    } catch (error) {
      setActionMessage(`清空日志失败：${error?.response?.data?.detail || error.message}`);
      setActionError(true);
    } finally {
      setTaskLogActionLoading(false);
    }
  }, [logTaskId, loadTaskLogs, taskLogActionLoading, taskLogs.length]);

  const handleExtract = async () => {
    if (!extractRootDir.trim()) return;
    setExtracting(true);
    setExtractResult(null);
    setActionMessage('');
    setActionError(false);
    try {
      const result = await extractDispResults(extractRootDir.trim(), extractDestDir.trim() || null);
      setExtractResult(result);
    } catch (err) {
      setExtractResult({ error: err?.response?.data?.detail || err.message });
    } finally {
      setExtracting(false);
    }
  };

  const handleScan = async () => {
    if (readOnly) return;
    setScanning(true);
    setActionMessage('');
    setActionError(false);
    try {
      const result = await scanDinsarResults();
      setActionMessage(result?.message || `D-InSAR 结果扫描任务已入队：${result?.task_id || '-'}`);
      if (result?.task_id) {
        onJobQueued?.(result.task_id);
      }
      await refreshMonitor();
    } catch (err) {
      setActionError(true);
      setActionMessage(err?.response?.data?.detail || err.message || 'D-InSAR 结果扫描失败');
    } finally {
      setScanning(false);
    }
  };

  const monitorTone = useMemo(() => {
    if (!monitoredTask) return 'neutral';
    if (showingRecentTask) return 'info';
    return String(monitoredTask.status || '').toUpperCase() === 'RUNNING' ? 'warn' : 'neutral';
  }, [monitoredTask, showingRecentTask]);

  return (
    <div className="dinsar-products-page">
      <div className="dinsar-products-hero">
        <div>
          <strong>D-InSAR 结果提取与标准目录</strong>
          <p>
            这里负责把生产目录中的位移结果提取为标准成果包，并触发统一扫描、发布和编目。
            生产运行与参数配置现已收口到“生产管理”工作台中的 “D-InSAR 运行” 子视图。
          </p>
        </div>
        <div className="dinsar-products-hero-badges">
          <span className={`dinsar-status-pill tone-${readOnly ? 'warn' : 'ready'}`}>
            {readOnly ? '只读模式' : '可执行写操作'}
          </span>
          <span className="dinsar-status-pill tone-info">日志改为手动刷新</span>
        </div>
      </div>

      <div className="dinsar-products-top-grid">
        <section className="dinsar-products-card">
          <div className="dinsar-products-card-head">
            <div>
              <strong>结果提取与重扫</strong>
              <span>先提取标准结果包，再按统一目录登记</span>
            </div>
          </div>

          <div className="dinsar-products-form-grid">
            <label className="dinsar-products-field dinsar-products-field-wide">
              <span>结果根目录</span>
              <input
                value={extractRootDir}
                onChange={(event) => setExtractRootDir(event.target.value)}
                placeholder="例如：D:\\Task_Pool\\DInSAR"
              />
            </label>
            <label className="dinsar-products-field">
              <span>目标目录（可选）</span>
              <input
                value={extractDestDir}
                onChange={(event) => setExtractDestDir(event.target.value)}
                placeholder="留空则使用系统默认"
              />
            </label>
          </div>

          <div className="dinsar-products-actions">
            <button
              type="button"
              className="primary"
              onClick={handleExtract}
              disabled={extracting || !extractRootDir.trim()}
            >
              {extracting ? '提取中...' : '提取位移结果'}
            </button>
            <button
              type="button"
              onClick={handleScan}
              disabled={readOnly || scanning}
            >
              {scanning ? '重扫中...' : '重扫结果'}
            </button>
          </div>

          {actionMessage && (
            <div className={`dinsar-products-message tone-${actionTone}`}>
              {actionMessage}
            </div>
          )}

          {extractResult && (
            <div className={`dinsar-products-result-card ${extractResult.error ? 'error' : 'success'}`}>
              {extractResult.error ? (
                <span>提取失败：{extractResult.error}</span>
              ) : (
                <>
                  <div>提取完成：复制 {extractResult.copied || 0} 个文件，覆盖 {extractResult.overwritten || 0} 个文件。</div>
                  {extractResult.catalog?.attempted && extractResult.catalog?.status === 'ok' && (
                    <div>
                      标准结果目录已同步：发布 {extractResult.catalog?.publish?.processed || 0} 项，
                      重建登记 {extractResult.catalog?.rebuild?.registered || 0} 项。
                    </div>
                  )}
                  {extractResult.catalog?.attempted && extractResult.catalog?.status === 'error' && (
                    <div>标准结果目录同步失败：{extractResult.catalog?.message}</div>
                  )}
                </>
              )}
            </div>
          )}
        </section>

        <section className={`dinsar-products-card monitor tone-${monitorTone}`}>
          <div className="dinsar-products-card-head">
            <div>
              <strong>产物任务监控</strong>
              <span>当前不轮询，按需手动刷新</span>
            </div>
            <button type="button" onClick={refreshMonitor}>刷新</button>
          </div>

          {!monitoredTask ? (
            <div className="dinsar-products-empty">当前没有正在执行的产物处理任务。</div>
          ) : (
            <div className="dinsar-monitor-card">
              <div className="dinsar-monitor-top">
                <div>
                  <strong>{showingRecentTask ? '最近一次任务' : '当前任务'}</strong>
                  <span>{formatTaskType(monitoredTask.task_type)}</span>
                </div>
                <StatusSummary status={monitoredTask.status} />
              </div>

              <div className="dinsar-monitor-task-id">{monitoredTask.task_id}</div>
              <div className="dinsar-monitor-message">{monitoredTask.message || '-'}</div>

              {monitoredTask.progress != null && (
                <div className="dinsar-monitor-progress">
                  <div className="dinsar-monitor-progress-track">
                    <div
                      className="dinsar-monitor-progress-bar"
                      style={{ width: `${monitoredTask.progress}%` }}
                    />
                  </div>
                  <span>{monitoredTask.progress}%</span>
                </div>
              )}

              <div className="dinsar-monitor-log-head">
                <strong>{showingRecentTask ? '最近一次任务日志' : '当前任务日志'}</strong>
                {!readOnly && (
                  <button
                    type="button"
                    onClick={handleClearTaskLogs}
                    disabled={taskLogActionLoading || taskLogs.length === 0}
                  >
                    {taskLogActionLoading && taskLogDeletingId == null ? '清空中...' : '清空日志'}
                  </button>
                )}
              </div>

              {taskLogsLoading ? (
                <div className="dinsar-products-empty">日志加载中...</div>
              ) : taskLogs.length === 0 ? (
                <div className="dinsar-products-empty">暂无日志。</div>
              ) : (
                <div className="dinsar-monitor-log-list">
                  {taskLogs.map((log, index) => {
                    const tone = getLogTone(log.level);
                    return (
                      <div
                        key={log.id || `${log.timestamp || 'log'}-${index}`}
                        className={`dinsar-monitor-log-item tone-${tone}`}
                      >
                        <div className="dinsar-monitor-log-main">
                          <div className="dinsar-monitor-log-time">
                            {(log.timestamp || '').replace('T', ' ').replace('Z', '')} [{log.level}]
                          </div>
                          <div className="dinsar-monitor-log-message">{log.message}</div>
                        </div>
                        {!readOnly && (
                          <button
                            type="button"
                            className="danger"
                            onClick={() => handleDeleteTaskLog(log.id)}
                            disabled={taskLogActionLoading || !log.id}
                          >
                            {taskLogDeletingId === log.id ? '删除中...' : '删除'}
                          </button>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </section>
      </div>

      <DinsarCatalogPanel
        readOnly={readOnly}
        initialSourceDir={extractRootDir}
        onTaskQueued={onJobQueued}
      />
    </div>
  );
}

function StatusSummary({ status }) {
  const normalized = String(status || '').toUpperCase();
  const tone = normalized === 'RUNNING'
    ? 'warn'
    : normalized === 'FAILED'
      ? 'error'
      : normalized === 'COMPLETED'
        ? 'ready'
        : 'neutral';

  return (
    <span className={`dinsar-status-pill tone-${tone}`}>
      {formatStatus(status)}
    </span>
  );
}
