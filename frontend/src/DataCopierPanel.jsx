import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { useI18n } from './i18n/I18nContext';

const COPY_STATUS_OPTIONS = [
  { value: 'COMPLETED', label: '已完成' },
  { value: 'IN_PROGRESS', label: '审核中' },
  { value: 'PENDING', label: '未审核' },
  { value: 'FAILED', label: '不宜下发' },
];
const BATCH_API_PAGE_LIMIT = 500;
const BATCH_API_MAX_PAGES = 200;

const DataCopierPanel = ({ apiEndpoint, readOnly = false, onJobQueued }) => {
  const { t } = useI18n();
  const [activeTab, setActiveTab] = useState('dinsar');
  const [destDir, setDestDir] = useState('');
  const [copyStatuses, setCopyStatuses] = useState(['COMPLETED']);
  const [includeDinsarOrbitFiles, setIncludeDinsarOrbitFiles] = useState(false);
  const [dinsarExportZip, setDinsarExportZip] = useState(false);
  const [batches, setBatches] = useState([]);
  const [selectedBatchId, setSelectedBatchId] = useState('');
  const [isUploading, setIsUploading] = useState(false);
  const [taskId, setTaskId] = useState(null);
  const [logs, setLogs] = useState([]);
  const [status, setStatus] = useState('IDLE');
  const logIntervalRef = useRef(null);
  const fetchLogsRef = useRef(null);
  const fetchBatchesRef = useRef(null);

  const normalizeStatus = (value) => {
    const normalized = (value || '').toString().toUpperCase();
    if (normalized === 'PENDING') return 'RUNNING';
    return normalized;
  };

  useEffect(() => {
    return () => {
      if (logIntervalRef.current) {
        clearInterval(logIntervalRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (taskId && status === 'RUNNING') {
      logIntervalRef.current = setInterval(() => {
        fetchLogsRef.current?.();
      }, 1000);
    } else if (logIntervalRef.current) {
      clearInterval(logIntervalRef.current);
      if (taskId) fetchLogsRef.current?.();
    }
  }, [taskId, status]);

  useEffect(() => {
    fetchBatchesRef.current?.();
  }, [activeTab]);

  const fetchBatches = async () => {
    try {
      const endpoint = activeTab === 'ps'
        ? `${apiEndpoint}/task-batches/ps`
        : `${apiEndpoint}/task-batches/dinsar`;
      const allBatches = [];
      for (let page = 0; page < BATCH_API_MAX_PAGES; page += 1) {
        const offset = page * BATCH_API_PAGE_LIMIT;
        const response = await axios.get(endpoint, {
          withCredentials: true,
          params: { limit: BATCH_API_PAGE_LIMIT, offset },
        });
        const items = Array.isArray(response.data) ? response.data : [];
        allBatches.push(...items);
        if (items.length < BATCH_API_PAGE_LIMIT) break;
      }
      setBatches(allBatches);
    } catch (error) {
      console.error('Failed to load batches:', error);
      setBatches([]);
    }
  };
  fetchBatchesRef.current = fetchBatches;

  const fetchLogs = async () => {
    if (!taskId) return;
    try {
      const response = await axios.get(`${apiEndpoint}/tools/copy-status/${taskId}`, { withCredentials: true });
      setLogs(response.data.logs);
      const nextStatus = normalizeStatus(response.data.status);
      if (nextStatus && nextStatus !== 'UNKNOWN') {
        setStatus(nextStatus);
      }
    } catch (error) {
      console.error('Failed to load logs:', error);
    }
  };
  fetchLogsRef.current = fetchLogs;

  const handleStartCopy = async () => {
    if (readOnly) {
      alert('当前账号为只读模式，无法执行复制任务。');
      return;
    }
    if (!selectedBatchId || !destDir) {
      alert('请选择批次并设置目标目录。');
      return;
    }
    if (!copyStatuses.length) {
      alert('请至少选择一个任务状态。');
      return;
    }

    setIsUploading(true);
    setLogs([]);
    setStatus('RUNNING');

    const endpoint = activeTab === 'ps'
      ? `${apiEndpoint}/tools/copy-ps-stack`
      : `${apiEndpoint}/tools/copy-dinsar-pairs`;

    try {
      const payload = {
        batch_id: selectedBatchId,
        dest_dir: destDir,
        copy_statuses: copyStatuses,
      };
      if (activeTab === 'dinsar') {
        payload.include_orbit_files = includeDinsarOrbitFiles;
        payload.export_zip = dinsarExportZip;
      }
      const response = await axios.post(endpoint, payload, { withCredentials: true });
      const taskId = response.data.task_id;
      setTaskId(taskId);

      // 触发全局锁定
      if (onJobQueued) {
        onJobQueued(taskId);
      }
    } catch (error) {
      console.error('Failed to start copy task:', error);
      alert(`启动失败：${error.response?.data?.detail || error.message}`);
      setStatus('FAILED');
    } finally {
      setIsUploading(false);
    }
  };

  const handleReset = () => {
    setTaskId(null);
    setLogs([]);
    setStatus('IDLE');
    setSelectedBatchId('');
  };

  const toggleCopyStatus = (status) => {
    setCopyStatuses((prev) => {
      if (prev.includes(status)) {
        if (prev.length === 1) return prev;
        return prev.filter((item) => item !== status);
      }
      return [...prev, status];
    });
  };

  return (
    <div className="data-copier-panel" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div className="tabs-header">
        <button
          className={activeTab === 'ps' ? 'active-tab' : ''}
          onClick={() => setActiveTab('ps')}
        >
          PS 分发
        </button>
        <button
          className={activeTab === 'dinsar' ? 'active-tab' : ''}
          onClick={() => setActiveTab('dinsar')}
        >
          D-InSAR 分发
        </button>
      </div>

      <div className="panel-content" style={{ flex: 1, display: 'flex', flexDirection: 'column', padding: '15px', gap: '15px' }}>
        {readOnly && (
          <div style={{ fontSize: '12px', color: '#92400e', background: '#fffbeb', border: '1px solid #fde68a', borderRadius: '6px', padding: '8px 10px' }}>
            当前账号为只读模式，无法发起复制任务。
          </div>
        )}
        {activeTab === 'dinsar' && (
          <div
            className="input-group"
            style={{
              border: '1px solid #c7d2fe',
              background: '#eef2ff',
              borderRadius: '8px',
              padding: '10px 12px',
            }}
          >
            <label>D-InSAR 分发设置：</label>
            <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap', marginTop: '8px' }}>
              <label style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                <input
                  type="checkbox"
                  checked={includeDinsarOrbitFiles}
                  onChange={(event) => setIncludeDinsarOrbitFiles(event.target.checked)}
                  disabled={status === 'RUNNING' || readOnly}
                />
                <span>复制精密轨道到 Task/orbit</span>
              </label>
              <label style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                <input
                  type="checkbox"
                  checked={dinsarExportZip}
                  onChange={(event) => setDinsarExportZip(event.target.checked)}
                  disabled={status === 'RUNNING' || readOnly}
                />
                <span>导出为 ZIP 压缩包</span>
              </label>
            </div>
            <div style={{ fontSize: '12px', color: '#475569', marginTop: '6px' }}>
              未勾选 ZIP 时直接导出 Task 文件夹；勾选后每个 Task 输出一个 .zip。
            </div>
          </div>
        )}
        <div className="input-group">
          <label>1. 选择批次：</label>
          <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
            <select
              value={selectedBatchId}
              onChange={(e) => setSelectedBatchId(e.target.value)}
              disabled={status === 'RUNNING' || readOnly}
              style={{ flex: 1, padding: '8px' }}
            >
              <option value="">-- 请选择 --</option>
              {batches.map(batch => (
                <option key={batch.batch_id} value={batch.batch_id}>
                  {batch.name || batch.batch_id} ({batch.completed_items}/{batch.total_items})
                </option>
              ))}
            </select>
            <button onClick={fetchBatches} disabled={status === 'RUNNING'}>刷新</button>
          </div>
        </div>

        <div className="input-group">
          <label>2. 任务状态筛选：</label>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            {COPY_STATUS_OPTIONS.map((item) => (
              <label key={item.value} style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
                <input
                  type="checkbox"
                  checked={copyStatuses.includes(item.value)}
                  onChange={() => toggleCopyStatus(item.value)}
                  disabled={status === 'RUNNING' || readOnly}
                />
                <span>{item.label}</span>
              </label>
            ))}
          </div>
          <div style={{ fontSize: '12px', color: '#6b7280' }}>
            默认仅复制“已完成”项。
          </div>
        </div>

        <div className="input-group">
          <label>3. 目标目录：</label>
          <input
            type="text"
            value={destDir}
            onChange={(e) => setDestDir(e.target.value)}
            placeholder="例如：D:/Data/Project_X/PS_Stack"
            disabled={status === 'RUNNING' || readOnly}
            style={{ width: '100%', padding: '8px' }}
          />
        </div>

        <div className="actions" style={{ display: 'flex', gap: '10px' }}>
          <button
            onClick={handleStartCopy}
            disabled={status === 'RUNNING' || isUploading || !selectedBatchId || !destDir || readOnly}
            className="primary-btn"
            style={{ flex: 1 }}
          >
            {status === 'RUNNING' ? '复制中...' : (readOnly ? '只读模式' : '开始复制')}
          </button>
          {status !== 'IDLE' && status !== 'RUNNING' && (
            <button onClick={handleReset} className="secondary-btn">
              重置
            </button>
          )}
        </div>

        <div className="log-viewer" style={{
          flex: 1,
          background: '#1a202c',
          color: '#a0aec0',
          padding: '10px',
          borderRadius: '4px',
          overflowY: 'auto',
          fontFamily: 'monospace',
          fontSize: '12px',
          border: '1px solid #2d3748'
        }}>
          {logs.length === 0 ? (
            <div style={{ textAlign: 'center', marginTop: '20px', color: '#4a5568' }}>
              等待任务...
            </div>
          ) : (
            logs.map((log, index) => (
              <div key={index}>{t(log)}</div>
            ))
          )}
          {status === 'COMPLETED' && (
            <div style={{ color: '#48bb78', marginTop: '10px', fontWeight: 'bold' }}>
              已完成。
            </div>
          )}
          {status === 'FAILED' && (
            <div style={{ color: '#f56565', marginTop: '10px', fontWeight: 'bold' }}>
              失败。
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default DataCopierPanel;
