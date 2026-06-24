import React, { useCallback, useEffect, useMemo, useState } from 'react';

import { scanDinsarResults } from './api/dinsar';
import { listTaskRoots } from './api/dinsarProduction';
import { extractDispResults } from './api/idl';
import { clearTaskLogs, deleteTaskLog, getTaskLogs } from './api/tasks';
import DinsarCatalogPanel from './components/DinsarCatalogPanel';
import useTaskMonitor from './hooks/useTaskMonitor';

const PRODUCT_TASK_TYPES = [
  'SCAN_DINSAR',
];

const TASK_TYPE_LABEL = {
  SCAN_DINSAR: 'D-InSAR 结果扫描',
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
  const [productionRoot, setProductionRoot] = useState('');
  const [productionRootReady, setProductionRootReady] = useState(false);
  const [extractResult, setExtractResult] = useState(null);
  const [syncing, setSyncing] = useState(false);
  const [actionMessage, setActionMessage] = useState('');
  const [actionError, setActionError] = useState(false);

  const [taskLogs, setTaskLogs] = useState([]);
  const [taskLogsLoading, setTaskLogsLoading] = useState(false);
  const [taskLogActionLoading, setTaskLogActionLoading] = useState(false);
  const [taskLogDeletingId, setTaskLogDeletingId] = useState(null);
  const taskMonitor = useTaskMonitor({
    taskTypes: PRODUCT_TASK_TYPES,
    showRecent: false,
  });
  const monitoredTask = taskMonitor.latestTask;
  const logTaskId = monitoredTask?.task_id || '';
  const actionTone = getMessageTone(actionMessage, actionError);
  const activeTaskCount = taskMonitor.activeTasks?.length || 0;
  const catalogSourceState = productionRootReady ? '已配置' : '未配置';
  const taskStateLabel = activeTaskCount > 0
    ? `${activeTaskCount} 个运行中`
    : '空闲';
  const taskStateTone = activeTaskCount > 0 ? 'warn' : 'ready';

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
    const nextTaskId = taskMonitor.activeTasks[0]?.task_id || logTaskId;
    await loadTaskLogs(nextTaskId);
  }, [loadTaskLogs, logTaskId, taskMonitor]);

  useEffect(() => {
    loadTaskLogs(logTaskId);
  }, [loadTaskLogs, logTaskId]);

  useEffect(() => {
    if (!taskMonitor.isBusy || !logTaskId) return undefined;
    const timer = window.setInterval(() => {
      void loadTaskLogs(logTaskId);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [loadTaskLogs, logTaskId, taskMonitor.isBusy]);

  useEffect(() => {
    let canceled = false;
    listTaskRoots()
      .then((data) => {
        if (canceled) return;
        const root = String(data?.root || '').trim();
        setProductionRoot(root);
        setProductionRootReady(Boolean(root && data?.root_exists));
      })
      .catch(() => {
        if (canceled) return;
        setProductionRoot('');
        setProductionRootReady(false);
      });
    return () => {
      canceled = true;
    };
  }, []);

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

  const handleExtractAndScan = async () => {
    if (readOnly || !productionRootReady || !productionRoot.trim()) return;
    setSyncing(true);
    setExtractResult(null);
    setActionMessage('');
    setActionError(false);
    try {
      const result = await extractDispResults(productionRoot.trim(), null);
      setExtractResult(result);
      const scanResult = await scanDinsarResults();
      setActionMessage(scanResult?.message || `D-InSAR 结果登记任务已提交：${scanResult?.task_id || '-'}`);
      if (scanResult?.task_id) {
        onJobQueued?.(scanResult.task_id);
      }
      await refreshMonitor();
    } catch (err) {
      setActionError(true);
      const message = err?.response?.data?.detail || err.message || 'D-InSAR 结果提取与登记失败';
      setActionMessage(message);
      setExtractResult((current) => current || { error: message });
    } finally {
      setSyncing(false);
    }
  };

  const monitorTone = useMemo(() => {
    if (!monitoredTask) return 'neutral';
    return String(monitoredTask.status || '').toUpperCase() === 'RUNNING' ? 'warn' : 'neutral';
  }, [monitoredTask]);

  return (
    <div className="dinsar-products-page">
      <div className="dinsar-products-hero">
        <div>
          <strong>D-InSAR 结果目录</strong>
          <p>
            将生产目录中的位移结果提取为标准成果包，并触发结果登记和目录编目。
            生产参数与运行提交已归入“D-InSAR 运行”，这里专注成果归档与资产登记。
          </p>
        </div>
        <div className="dinsar-products-signals" aria-label="D-InSAR 结果目录状态摘要">
          <div className={`dinsar-production-signal tone-${readOnly ? 'warn' : 'ready'}`}>
            <span>操作模式</span>
            <strong>{readOnly ? '只读' : '可维护'}</strong>
          </div>
          <div className={`dinsar-production-signal tone-${taskStateTone}`}>
            <span>产物任务</span>
            <strong>{taskStateLabel}</strong>
          </div>
          <div className={`dinsar-production-signal tone-${productionRootReady ? 'ready' : 'neutral'}`}>
            <span>提取源</span>
            <strong>{catalogSourceState}</strong>
          </div>
          <div className="dinsar-production-signal tone-info">
            <span>日志</span>
            <strong>手动刷新</strong>
          </div>
        </div>
      </div>

      <div className="dinsar-products-section-head">
        <div>
          <strong>成果提取与任务监控</strong>
          <span>左侧执行受控提取与登记，右侧核对后台任务与日志。</span>
        </div>
      </div>

      <div className="dinsar-products-top-grid">
        <section className="dinsar-products-card">
          <div className="dinsar-products-card-head">
            <div>
              <strong>D-InSAR 结果提取与登记</strong>
              <span>将已完成的生产成果归入标准结果目录</span>
            </div>
          </div>

          <div className="dinsar-products-controlled-source">
            <span>成果来源</span>
            <strong>{productionRootReady ? '生产目录已就绪' : '生产目录待完善'}</strong>
            <p>{productionRootReady ? '可将当前生产成果提取并登记为标准结果包。' : '请先完成 D-InSAR 生产目录配置。'}</p>
          </div>

          <div className="dinsar-products-actions">
            <button
              type="button"
              className="primary"
              onClick={handleExtractAndScan}
              disabled={readOnly || syncing || !productionRootReady}
            >
              {syncing ? '处理中...' : '提取并登记结果'}
            </button>
            {!productionRootReady && <span className="dinsar-products-action-hint">请先在后端配置 D-InSAR 生产根目录。</span>}
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
              <span>任务运行时自动更新，空闲时按需刷新</span>
            </div>
            <button type="button" onClick={refreshMonitor}>刷新</button>
          </div>

          {!monitoredTask ? (
            <div className="dinsar-products-empty">当前没有正在执行的产物处理任务。</div>
          ) : (
            <div className="dinsar-monitor-card">
              <div className="dinsar-monitor-top">
                <div>
                  <strong>当前任务</strong>
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
                <strong>当前任务日志</strong>
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

      <section className="dinsar-products-catalog-section">
        <div className="dinsar-products-section-head">
          <div>
            <strong>标准目录与资产详情</strong>
            <span>核对 AOI、时间范围、资产文件、发布状态和目录一致性。</span>
          </div>
        </div>
        <DinsarCatalogPanel
          readOnly={readOnly}
        />
      </section>
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
