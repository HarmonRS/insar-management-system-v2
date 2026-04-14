import React, { useCallback, useEffect, useState } from 'react';

import { scanDinsarResults } from './api/dinsar';
import { extractDispResults, getActiveTasks, getTaskLogs } from './api/idl';
import DinsarCatalogPanel from './components/DinsarCatalogPanel';

const card = {
  background: '#fff',
  padding: '12px',
  borderRadius: '8px',
  border: '1px solid #e2e8f0',
  marginBottom: '12px',
};

const PRODUCT_TASK_TYPES = [
  'SCAN_DINSAR',
  'PUBLISH_DINSAR_PRODUCTS',
  'REBUILD_DINSAR_CATALOG',
];

const TASK_TYPE_LABEL = {
  SCAN_DINSAR: 'D-InSAR结果扫描任务',
  PUBLISH_DINSAR_PRODUCTS: '结果包发布任务',
  REBUILD_DINSAR_CATALOG: '结果目录重建任务',
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

export default function DinsarProductsPanel({ readOnly = false, onJobQueued }) {
  const [extractRootDir, setExtractRootDir] = useState('');
  const [extractDestDir, setExtractDestDir] = useState('');
  const [extractResult, setExtractResult] = useState(null);
  const [extracting, setExtracting] = useState(false);
  const [actionMessage, setActionMessage] = useState('');
  const [actionError, setActionError] = useState(false);
  const [scanning, setScanning] = useState(false);

  const [activeTask, setActiveTask] = useState(null);
  const [taskLogs, setTaskLogs] = useState([]);
  const [taskLogsLoading, setTaskLogsLoading] = useState(false);

  const loadActiveTask = useCallback(async () => {
    try {
      const data = await getActiveTasks();
      const tasks = Array.isArray(data) ? data : (data?.tasks || []);
      const relevantTask = tasks.find(task => PRODUCT_TASK_TYPES.includes(task.task_type)) || null;
      setActiveTask(relevantTask);
    } catch {
      setActiveTask(null);
    }
  }, []);

  const loadTaskLogs = useCallback(async taskId => {
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

  useEffect(() => {
    loadActiveTask();
  }, [loadActiveTask]);

  useEffect(() => {
    const timer = setInterval(loadActiveTask, 5000);
    return () => clearInterval(timer);
  }, [loadActiveTask]);

  useEffect(() => {
    const taskId = activeTask?.task_id || '';
    loadTaskLogs(taskId);
    if (!taskId) return undefined;
    const timer = setInterval(() => loadTaskLogs(taskId), 5000);
    return () => clearInterval(timer);
  }, [activeTask?.task_id, loadTaskLogs]);

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
      setActionMessage(result?.message || `D-InSAR结果扫描任务已入队：${result?.task_id || '-'}`);
      if (result?.task_id) {
        onJobQueued?.(result.task_id);
      }
      loadActiveTask();
    } catch (err) {
      setActionError(true);
      setActionMessage(err?.response?.data?.detail || err.message || 'D-InSAR结果扫描失败');
    } finally {
      setScanning(false);
    }
  };

  return (
    <div style={{ padding: '16px', maxWidth: 960 }}>
      <div style={card}>
        <strong style={{ fontSize: 14, display: 'block', marginBottom: 10 }}>产物提取与重扫</strong>

        <div
          style={{
            fontSize: 12,
            color: '#475569',
            lineHeight: 1.6,
            marginBottom: 10,
            padding: '8px 10px',
            background: '#f8fafc',
            border: '1px solid #e2e8f0',
            borderRadius: 6,
          }}
        >
          这里负责把生产目录中的位移结果提取为标准结果包，并触发结果重扫、发布和编目。生产运行与参数配置已独立放到“D-InSAR生产”选项卡。
        </div>

        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
          <input
            value={extractRootDir}
            onChange={event => setExtractRootDir(event.target.value)}
            placeholder="结果根目录（提取位移结果）"
            style={{ flex: 2, minWidth: 220, padding: '5px 8px', borderRadius: 4, border: '1px solid #e2e8f0', fontSize: 13 }}
          />
          <input
            value={extractDestDir}
            onChange={event => setExtractDestDir(event.target.value)}
            placeholder="目标目录（留空使用默认）"
            style={{ flex: 1, minWidth: 180, padding: '5px 8px', borderRadius: 4, border: '1px solid #e2e8f0', fontSize: 13 }}
          />
          <button
            onClick={handleExtract}
            disabled={extracting || !extractRootDir.trim()}
            style={{ padding: '5px 14px', borderRadius: 4, border: 'none', background: '#3b82f6', color: '#fff', cursor: 'pointer', fontSize: 13 }}
          >
            {extracting ? '提取中...' : '提取位移结果'}
          </button>
          <button
            onClick={handleScan}
            disabled={readOnly || scanning}
            style={{
              padding: '5px 14px',
              borderRadius: 4,
              border: '1px solid #e2e8f0',
              background: '#f8fafc',
              cursor: readOnly ? 'not-allowed' : 'pointer',
              fontSize: 13,
            }}
          >
            {scanning ? '重扫中...' : '重扫结果'}
          </button>
        </div>

        {actionMessage && (
          <div style={{ marginBottom: 8, fontSize: 12, color: actionError ? '#dc2626' : '#166534' }}>
            {actionMessage}
          </div>
        )}

        {extractResult && (
          <div
            style={{
              fontSize: 12,
              padding: '6px 10px',
              background: extractResult.error ? '#fef2f2' : '#f0fdf4',
              borderRadius: 4,
            }}
          >
            {extractResult.error ? (
              <span style={{ color: '#ef4444' }}>提取失败：{extractResult.error}</span>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4, color: '#16a34a' }}>
                <span>提取完成：复制 {extractResult.copied || 0} 个文件，覆盖 {extractResult.overwritten || 0} 个文件。</span>
                {extractResult.catalog?.attempted && extractResult.catalog?.status === 'ok' && (
                  <span style={{ color: '#166534' }}>
                    已同步标准结果包目录：发布 {extractResult.catalog?.publish?.processed || 0} 项，重建登记 {extractResult.catalog?.rebuild?.registered || 0} 项。
                  </span>
                )}
                {extractResult.catalog?.attempted && extractResult.catalog?.status === 'error' && (
                  <span style={{ color: '#b45309' }}>
                    标准结果包目录同步失败：{extractResult.catalog?.message}
                  </span>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      <div style={card}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
          <strong style={{ fontSize: 14 }}>产物任务监控</strong>
          <button
            onClick={loadActiveTask}
            style={{
              fontSize: 12,
              padding: '3px 10px',
              borderRadius: 4,
              border: '1px solid #e2e8f0',
              cursor: 'pointer',
              background: '#f8fafc',
            }}
          >
            刷新
          </button>
        </div>

        {!activeTask ? (
          <div style={{ fontSize: 12, color: '#94a3b8' }}>当前没有运行中的产物处理任务。</div>
        ) : (
          <div style={{ padding: '8px 10px', background: '#fefce8', borderRadius: 6, border: '1px solid #fde68a' }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: '#92400e', marginBottom: 4 }}>当前任务</div>
            <div style={{ fontSize: 12, color: '#78350f', wordBreak: 'break-all' }}>
              {activeTask.task_id} - {formatTaskType(activeTask.task_type)} - {formatStatus(activeTask.status)} - {activeTask.message}
            </div>
            {activeTask.progress != null && (
              <div style={{ marginTop: 6 }}>
                <div style={{ height: 6, background: '#fde68a', borderRadius: 3, overflow: 'hidden' }}>
                  <div
                    style={{
                      height: '100%',
                      width: `${activeTask.progress}%`,
                      background: '#f59e0b',
                      transition: 'width 0.3s',
                    }}
                  />
                </div>
                <div style={{ fontSize: 11, color: '#92400e', marginTop: 2 }}>{activeTask.progress}%</div>
              </div>
            )}

            <div style={{ marginTop: 8, background: '#fff', border: '1px solid #fde68a', borderRadius: 6, padding: '8px 10px' }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: '#92400e', marginBottom: 6 }}>任务日志</div>
              {taskLogsLoading ? (
                <div style={{ fontSize: 11, color: '#a16207' }}>加载中...</div>
              ) : taskLogs.length === 0 ? (
                <div style={{ fontSize: 11, color: '#a16207' }}>暂无日志。</div>
              ) : (
                <div style={{ maxHeight: 220, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {taskLogs.map((log, index) => (
                    <div
                      key={`${log.timestamp || 'log'}-${index}`}
                      style={{ fontSize: 11, lineHeight: 1.45, color: log.level === 'WARNING' ? '#b45309' : '#334155' }}
                    >
                      <div style={{ color: '#64748b' }}>
                        {(log.timestamp || '').replace('T', ' ').replace('Z', '')} [{log.level}]
                      </div>
                      <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{log.message}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      <DinsarCatalogPanel
        readOnly={readOnly}
        initialSourceDir={extractRootDir}
        onTaskQueued={onJobQueued}
      />
    </div>
  );
}
