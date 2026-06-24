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
const DINSAR_PURPOSE_PRODUCTION = 'production_prepare';
const DINSAR_PURPOSE_DISTRIBUTION = 'source_distribution';
const FALLBACK_DINSAR_TASK_POOL_ROOT = 'D:\\Task_Pool\\DInSAR';
const FALLBACK_DATA_DISTRIBUTION_ROOT = 'D:\\Task_Pool\\Data_Distribution';

const DataCopierPanel = ({ apiEndpoint, readOnly = false, onJobQueued }) => {
  const { t } = useI18n();
  const [targetName, setTargetName] = useState('');
  const [dinsarTaskPoolRoot, setDinsarTaskPoolRoot] = useState('');
  const [dataDistributionRoot, setDataDistributionRoot] = useState('');
  const [dinsarPurpose, setDinsarPurpose] = useState(DINSAR_PURPOSE_PRODUCTION);
  const [copyStatuses, setCopyStatuses] = useState(['COMPLETED']);
  const [includeDinsarOrbitFiles, setIncludeDinsarOrbitFiles] = useState(true);
  const [skipExistingDinsarTasks, setSkipExistingDinsarTasks] = useState(true);
  const [dinsarMaxItems, setDinsarMaxItems] = useState('200');
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
      }, 3000);
    } else if (logIntervalRef.current) {
      clearInterval(logIntervalRef.current);
      if (taskId) fetchLogsRef.current?.();
    }
  }, [taskId, status]);

  useEffect(() => {
    fetchBatchesRef.current?.();
  }, []);

  useEffect(() => {
    axios.get(`${apiEndpoint}/monitor/status`, { withCredentials: true })
      .then((response) => {
        const taskRoot = (response.data?.dinsar_task_pool_root || '').toString().trim();
        const distributionRoot = (response.data?.data_distribution_root || '').toString().trim();
        if (taskRoot) {
          setDinsarTaskPoolRoot(taskRoot);
        }
        if (distributionRoot) {
          setDataDistributionRoot(distributionRoot);
        }
      })
      .catch((error) => {
        console.error('Failed to load monitor status:', error);
      });
  }, [apiEndpoint]);

  const fetchBatches = async () => {
    try {
      const endpoint = `${apiEndpoint}/task-batches/dinsar`;
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
      const response = await axios.get(`${apiEndpoint}/tools/copy-status/${taskId}`, {
        withCredentials: true,
        params: { limit: 1000 },
      });
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

  const handleDinsarPurposeChange = (nextPurpose) => {
    setDinsarPurpose(nextPurpose);
    setTaskId(null);
    setLogs([]);
    setStatus('IDLE');
    setTargetName('');
  };

  const handleStartCopy = async () => {
    if (readOnly) {
      alert('当前账号为只读模式，无法执行复制任务。');
      return;
    }
    if (!selectedBatchId || !targetName.trim()) {
      alert('请选择批次并填写任务名。');
      return;
    }
    if (!copyStatuses.length) {
      alert('请至少选择一个任务状态。');
      return;
    }

    setIsUploading(true);
    setLogs([]);
    setStatus('RUNNING');

    const endpoint = `${apiEndpoint}/tools/copy-dinsar-pairs`;

    try {
      const payload = {
        batch_id: selectedBatchId,
        target_name: targetName.trim(),
        copy_statuses: copyStatuses,
      };
      payload.include_orbit_files = includeDinsarOrbitFiles;
      payload.package_mode = dinsarPurpose === DINSAR_PURPOSE_PRODUCTION ? 'task_folder' : 'source_bundle';
      payload.export_zip = false;
      payload.skip_existing = skipExistingDinsarTasks;
      const parsedMaxItems = Number.parseInt(dinsarMaxItems, 10);
      if (Number.isFinite(parsedMaxItems) && parsedMaxItems > 0) {
        payload.max_items = parsedMaxItems;
      }
      const response = await axios.post(endpoint, payload, { withCredentials: true });
      const taskId = response.data.task_id;
      setTaskId(taskId);

      // 通知全局任务状态；COPY_DATA 在全局控制里按非阻塞处理。
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
      <div className="panel-content" style={{ flex: 1, display: 'flex', flexDirection: 'column', padding: '15px', gap: '15px' }}>
        {readOnly && (
          <div style={{ fontSize: '12px', color: '#92400e', background: '#fffbeb', border: '1px solid #fde68a', borderRadius: '6px', padding: '8px 10px' }}>
            当前账号为只读模式，无法发起复制任务。
          </div>
        )}
        <div
          className="input-group"
          style={{
            border: '1px solid #c7d2fe',
            background: '#eef2ff',
            borderRadius: '8px',
            padding: '10px 12px',
          }}
        >
          <label>D-InSAR 任务用途：</label>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginTop: '8px' }}>
            <button
              type="button"
              className={dinsarPurpose === DINSAR_PURPOSE_PRODUCTION ? 'primary-btn' : 'secondary-btn'}
              onClick={() => handleDinsarPurposeChange(DINSAR_PURPOSE_PRODUCTION)}
              disabled={status === 'RUNNING' || readOnly}
            >
              生产数据准备
            </button>
            <button
              type="button"
              className={dinsarPurpose === DINSAR_PURPOSE_DISTRIBUTION ? 'primary-btn' : 'secondary-btn'}
              onClick={() => handleDinsarPurposeChange(DINSAR_PURPOSE_DISTRIBUTION)}
              disabled={status === 'RUNNING' || readOnly}
            >
              数据分发
            </button>
          </div>
          <div style={{ fontSize: '13px', color: '#1e3a8a', marginTop: '8px', fontWeight: 600 }}>
            {dinsarPurpose === DINSAR_PURPOSE_PRODUCTION
              ? '生成可直接运行的 Task_Pool 任务目录（Task_YYYYMMDD_YYYYMMDD / master / slave / orbit）'
              : '导出源压缩包去重包（data / orbit / pairs.json / manifest.json）'}
          </div>
          <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap', marginTop: '8px' }}>
            <label style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
              <input
                type="checkbox"
                checked={includeDinsarOrbitFiles}
                onChange={(event) => setIncludeDinsarOrbitFiles(event.target.checked)}
                disabled={status === 'RUNNING' || readOnly}
              />
              <span>{dinsarPurpose === DINSAR_PURPOSE_PRODUCTION ? '复制精密轨道到 Task/orbit' : '复制精密轨道到 orbit/'}</span>
            </label>
            <label style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
              <input
                type="checkbox"
                checked={skipExistingDinsarTasks}
                onChange={(event) => setSkipExistingDinsarTasks(event.target.checked)}
                disabled={status === 'RUNNING' || readOnly}
              />
              <span>{dinsarPurpose === DINSAR_PURPOSE_PRODUCTION ? '跳过已存在的完整 Task' : '复用已存在的 data/orbit'}</span>
            </label>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap', marginTop: '8px' }}>
            <label style={{ fontSize: '13px' }}>
              {dinsarPurpose === DINSAR_PURPOSE_PRODUCTION ? '每次最多准备新 Task:' : '每次最多追加新配对:'}
            </label>
            <input
              type="number"
              min="0"
              value={dinsarMaxItems}
              onChange={(event) => setDinsarMaxItems(event.target.value)}
              disabled={status === 'RUNNING' || readOnly}
              style={{ width: '110px', padding: '5px 7px' }}
            />
            <span style={{ fontSize: '12px', color: '#64748b' }}>0 或留空表示不限制</span>
          </div>
          <div style={{ fontSize: '12px', color: '#475569', marginTop: '6px' }}>
            {dinsarPurpose === DINSAR_PURPOSE_PRODUCTION
              ? '源池仍管理压缩包；这里按任务解包到本机 Task_Pool，供 D-InSAR 引擎直接使用。'
              : '该归口用于跨目录/跨机器下发源压缩包，不作为生产运行入口。'}
          </div>
        </div>
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
          <label>3. {dinsarPurpose === DINSAR_PURPOSE_PRODUCTION ? '生产任务名' : '分发任务名'}：</label>
          <input
            type="text"
            value={targetName}
            onChange={(e) => setTargetName(e.target.value)}
            placeholder={
              dinsarPurpose === DINSAR_PURPOSE_PRODUCTION
                ? '例如：MDJ_20240422_20240520'
                : '例如：Project_X_DInSAR_Source_Bundle'
            }
            disabled={status === 'RUNNING' || readOnly}
            style={{ width: '100%', padding: '8px' }}
          />
          <div style={{ fontSize: '12px', color: '#64748b', marginTop: '4px' }}>
            {dinsarPurpose === DINSAR_PURPOSE_PRODUCTION
              ? `服务器写入目录：${dinsarTaskPoolRoot || FALLBACK_DINSAR_TASK_POOL_ROOT}\\${targetName || '<任务名>'}`
              : `服务器写入目录：${dataDistributionRoot || FALLBACK_DATA_DISTRIBUTION_ROOT}\\${targetName || '<任务名>'}`}
          </div>
        </div>

        <div className="actions" style={{ display: 'flex', gap: '10px' }}>
          <button
            onClick={handleStartCopy}
            disabled={status === 'RUNNING' || isUploading || !selectedBatchId || !targetName.trim() || readOnly}
            className="primary-btn"
            style={{ flex: 1 }}
          >
            {status === 'RUNNING'
              ? '处理中...'
              : (readOnly ? '只读模式' : (dinsarPurpose === DINSAR_PURPOSE_PRODUCTION ? '生成生产任务' : '开始分发'))}
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
