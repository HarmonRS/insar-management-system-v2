import React, { useCallback, useEffect, useState } from 'react';

import { listEngines, listRuns, submitRun } from './api/dinsarProduction';
import { getJobLog } from './api/idl';
import { clearTaskLogs, deleteTaskLog, getActiveTasks, getTaskLogs } from './api/tasks';

const card = {
  background: '#fff',
  padding: '12px',
  borderRadius: '8px',
  border: '1px solid #e2e8f0',
  marginBottom: '12px',
};

const EMPTY_ARRAY = [];
const EMPTY_OBJECT = {};

const ENGINE_STATUS_COLOR = {
  ok: '#22c55e',
  degraded: '#f59e0b',
  unavailable: '#ef4444',
  not_implemented: '#94a3b8',
  error: '#ef4444',
};

const ENGINE_STATUS_LABEL = {
  ok: '可用',
  degraded: '降级',
  unavailable: '不可用',
  not_implemented: '预留',
  error: '异常',
};

const ENGINE_LABEL = {
  sarscape: 'SARscape',
  isce2: 'ISCE2',
  landsar: 'LANDSAR',
};

const TASK_TYPE_LABEL = {
  ISCE2_RUN: 'ISCE2生产',
  IDL_RUN_DINSAR: 'ENVI生产',
};

const STATUS_LABEL = {
  PENDING: '等待中',
  RUNNING: '运行中',
  COMPLETED: '已完成',
  FAILED: '失败',
  CANCELLED: '已取消',
  CANCELED: '已取消',
  success: '成功',
  failed: '失败',
  running: '运行中',
  pending: '等待中',
};

function formatEngineLabel(engineCode, engineLabel = '') {
  return engineLabel || ENGINE_LABEL[engineCode] || engineCode || '-';
}

function formatTaskType(taskType) {
  return TASK_TYPE_LABEL[taskType] || taskType || '-';
}

function formatStatus(status) {
  return STATUS_LABEL[status] || status || '-';
}

function EngineStatusCard({ engine, onSelect, selected }) {
  const color = ENGINE_STATUS_COLOR[engine.status] || '#94a3b8';
  const label = ENGINE_STATUS_LABEL[engine.status] || engine.status;
  const clickable = engine.available;

  return (
    <div
      onClick={() => clickable && onSelect(engine.engine_code)}
      style={{
        ...card,
        marginBottom: 0,
        cursor: clickable ? 'pointer' : 'default',
        border: selected ? '2px solid #3b82f6' : `1px solid ${clickable ? '#e2e8f0' : '#f1f5f9'}`,
        background: selected ? '#eff6ff' : clickable ? '#fff' : '#f8fafc',
        opacity: clickable ? 1 : 0.7,
        minWidth: 160,
        flex: 1,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
        <span
          style={{
            width: 8,
            height: 8,
            borderRadius: '50%',
            background: color,
            display: 'inline-block',
            flexShrink: 0,
          }}
        />
        <span style={{ fontWeight: 600, fontSize: 13 }}>{engine.engine_label}</span>
      </div>
      <div style={{ fontSize: 11, color }}>{label}</div>
      {engine.message && (
        <div style={{ fontSize: 11, color: '#64748b', marginTop: 2, lineHeight: 1.35 }}>
          {engine.message}
        </div>
      )}
    </div>
  );
}

function buildDefaults(schema) {
  const defaults = {};
  Object.entries(schema || {}).forEach(([key, item]) => {
    if (Object.prototype.hasOwnProperty.call(item, 'default')) {
      defaults[key] = item.default;
    } else if (item.type === 'boolean') {
      defaults[key] = false;
    } else {
      defaults[key] = '';
    }
  });
  return defaults;
}

function buildExtraPayload(schema, values) {
  const payload = {};
  Object.entries(schema || {}).forEach(([key, item]) => {
    if (item.readonly || item.include_in_payload === false) {
      return;
    }
    const rawValue = values[key];
    if (item.type === 'boolean') {
      payload[key] = !!rawValue;
      return;
    }
    if (rawValue == null || String(rawValue).trim() === '') return;
    if (item.type === 'number') {
      const parsed = Number(rawValue);
      if (!Number.isNaN(parsed) && Number.isFinite(parsed)) {
        payload[key] = parsed;
      }
      return;
    }
    payload[key] = String(rawValue).trim();
  });
  return payload;
}

function ParamField({ name, schema, value, disabled, onChange }) {
  const label = schema.label || name;
  const description = schema.description || '';
  const recommendation = schema.recommendation || '';
  const isReadonly = !!schema.readonly;
  const inputStyle = {
    width: '100%',
    padding: '5px 8px',
    borderRadius: 4,
    border: '1px solid #e2e8f0',
    fontSize: 13,
    boxSizing: 'border-box',
    background: isReadonly ? '#f8fafc' : '#fff',
    color: isReadonly ? '#475569' : '#0f172a',
  };

  if (schema.type === 'boolean') {
    return (
      <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: '#0f172a' }}>
        <input
          type="checkbox"
          checked={!!value}
          disabled={disabled || isReadonly}
          onChange={event => onChange(name, event.target.checked)}
        />
        <span>{label}</span>
        {isReadonly && (
          <span
            style={{
              padding: '1px 6px',
              borderRadius: 999,
              background: '#e2e8f0',
              color: '#475569',
              fontSize: 11,
            }}
          >
            固定值
          </span>
        )}
        {description && <span style={{ color: '#64748b' }}>{description}</span>}
      </label>
    );
  }

  return (
    <div style={{ minWidth: 180, flex: schema.type === 'string' ? '1 1 280px' : '0 1 180px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
        <label style={{ fontSize: 12, color: '#64748b', display: 'block' }}>{label}</label>
        {isReadonly && (
          <span
            style={{
              padding: '1px 6px',
              borderRadius: 999,
              background: '#e2e8f0',
              color: '#475569',
              fontSize: 11,
            }}
          >
            固定值
          </span>
        )}
      </div>
      <input
        type={schema.type === 'number' ? 'number' : 'text'}
        value={value ?? ''}
        disabled={disabled || isReadonly}
        step={schema.step}
        min={schema.min}
        max={schema.max}
        placeholder={schema.placeholder || ''}
        onChange={event => onChange(name, event.target.value)}
        style={inputStyle}
      />
      {description && <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 4 }}>{description}</div>}
      {recommendation && <div style={{ fontSize: 11, color: '#2563eb', marginTop: 4 }}>推荐：{recommendation}</div>}
    </div>
  );
}

export default function DinsarProductionPanel({ readOnly = false, onJobQueued }) {
  const [engines, setEngines] = useState([]);
  const [enginesLoading, setEnginesLoading] = useState(false);
  const [selectedEngine, setSelectedEngine] = useState('sarscape');
  const [selectedProfile, setSelectedProfile] = useState('custom6');
  const [rootDir, setRootDir] = useState('');
  const [numToProcess, setNumToProcess] = useState(0);
  const [timeoutSec, setTimeoutSec] = useState('');
  const [engineExtraParams, setEngineExtraParams] = useState({});
  const [submitting, setSubmitting] = useState(false);
  const [submitMsg, setSubmitMsg] = useState('');
  const [submitError, setSubmitError] = useState(false);

  const [runs, setRuns] = useState([]);
  const [runsLoading, setRunsLoading] = useState(false);

  const [logModal, setLogModal] = useState({ open: false, runId: '', content: '', loading: false });
  const [activeTask, setActiveTask] = useState(null);
  const [taskLogs, setTaskLogs] = useState([]);
  const [taskLogsLoading, setTaskLogsLoading] = useState(false);
  const [taskLogActionLoading, setTaskLogActionLoading] = useState(false);
  const [taskLogDeletingId, setTaskLogDeletingId] = useState(null);

  const currentEngineObj = engines.find(engine => engine.engine_code === selectedEngine) || null;
  const currentProfiles = currentEngineObj?.profiles || EMPTY_ARRAY;
  const currentProfileObj = currentProfiles.find(profile => profile.code === selectedProfile) || null;
  const currentParamSchema = currentProfileObj?.params_schema || EMPTY_OBJECT;
  const latestRunWithTask = runs.find(run => run?.task_id) || null;
  const monitoredTask = activeTask || (
    latestRunWithTask
      ? {
        task_id: latestRunWithTask.task_id,
        task_type: latestRunWithTask.engine === 'isce2' ? 'ISCE2_RUN' : 'IDL_RUN_DINSAR',
        status: latestRunWithTask.raw_status || latestRunWithTask.status,
        progress: latestRunWithTask.raw_status === 'COMPLETED' || latestRunWithTask.status === 'success' ? 100 : null,
        message: latestRunWithTask.message || '最近一次任务',
      }
      : null
  );
  const logTaskId = monitoredTask?.task_id || '';
  const showingRecentTask = !activeTask && !!monitoredTask;

  const loadEngines = useCallback(async () => {
    setEnginesLoading(true);
    try {
      const data = await listEngines();
      setEngines(data.engines || []);
    } catch {
      setEngines([]);
    } finally {
      setEnginesLoading(false);
    }
  }, []);

  const loadRuns = useCallback(async () => {
    setRunsLoading(true);
    try {
      const data = await listRuns(20);
      setRuns(data.runs || []);
    } catch {
      setRuns([]);
    } finally {
      setRunsLoading(false);
    }
  }, []);

  const loadActiveTask = useCallback(async () => {
    try {
      const data = await getActiveTasks();
      const tasks = Array.isArray(data) ? data : (data?.tasks || []);
      const relevantTask = tasks.find(task => ['ISCE2_RUN', 'IDL_RUN_DINSAR'].includes(task.task_type)) || null;
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

  const handleDeleteTaskLog = useCallback(async logId => {
    const taskId = logTaskId;
    if (!taskId || !logId || taskLogActionLoading) return;
    if (!window.confirm('确定要删除这条任务日志吗？')) return;

    setTaskLogDeletingId(logId);
    setTaskLogActionLoading(true);
    try {
      await deleteTaskLog(taskId, logId);
      await loadTaskLogs(taskId);
    } catch (err) {
      setSubmitError(true);
      setSubmitMsg(`删除日志失败：${err?.response?.data?.detail || err.message}`);
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
    } catch (err) {
      setSubmitError(true);
      setSubmitMsg(`清空日志失败：${err?.response?.data?.detail || err.message}`);
    } finally {
      setTaskLogActionLoading(false);
    }
  }, [logTaskId, loadTaskLogs, taskLogActionLoading, taskLogs.length]);

  useEffect(() => {
    loadEngines();
    loadRuns();
    loadActiveTask();
  }, [loadActiveTask, loadEngines, loadRuns]);

  useEffect(() => {
    const timer = setInterval(() => {
      loadRuns();
      loadActiveTask();
    }, 5000);
    return () => clearInterval(timer);
  }, [loadActiveTask, loadRuns]);

  useEffect(() => {
    const taskId = logTaskId;
    loadTaskLogs(taskId);
    if (!taskId) return undefined;
    const timer = setInterval(() => loadTaskLogs(taskId), 5000);
    return () => clearInterval(timer);
  }, [logTaskId, loadTaskLogs]);

  useEffect(() => {
    if (currentProfiles.length > 0) {
      setSelectedProfile(currentProfiles[0].code);
    }
  }, [selectedEngine, currentProfiles]);

  useEffect(() => {
    setEngineExtraParams(buildDefaults(currentParamSchema));
  }, [selectedEngine, selectedProfile, currentParamSchema]);

  const handleParamChange = useCallback((name, value) => {
    setEngineExtraParams(current => ({
      ...current,
      [name]: value,
    }));
  }, []);

  const handleSubmit = async () => {
    if (!rootDir.trim()) {
      setSubmitError(true);
      setSubmitMsg('请输入根目录。');
      return;
    }

    setSubmitting(true);
    setSubmitMsg('');
    setSubmitError(false);
    try {
      const extra = buildExtraPayload(currentParamSchema, engineExtraParams);
      const result = await submitRun({
        engine_code: selectedEngine,
        profile: selectedProfile,
        root_dir: rootDir.trim(),
        num_to_process: Number(numToProcess) || 0,
        timeout_seconds: timeoutSec ? Number(timeoutSec) : null,
        extra,
      });
      const taskCount = result?.selected_task_count ? `，选中 ${result.selected_task_count} 个任务` : '';
      setSubmitError(false);
      setSubmitMsg(`任务已入队：${result.task_id}${taskCount}`);
      if (onJobQueued) onJobQueued(result.task_id);
      loadRuns();
      loadActiveTask();
    } catch (err) {
      setSubmitError(true);
      setSubmitMsg(`提交失败：${err?.response?.data?.detail || err.message}`);
    } finally {
      setSubmitting(false);
    }
  };

  const handleViewLog = async runId => {
    setLogModal({ open: true, runId, content: '', loading: true });
    try {
      const data = await getJobLog(runId);
      setLogModal({ open: true, runId, content: data.content || '', loading: false });
    } catch {
      setLogModal({ open: true, runId, content: '日志加载失败。', loading: false });
    }
  };

  const isSubmitDisabled = readOnly || submitting || !currentEngineObj?.available;

  return (
    <div style={{ padding: '16px', maxWidth: 960 }}>
      {logModal.open && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0,0,0,0.5)',
            zIndex: 9999,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <div
            style={{
              background: '#1e293b',
              color: '#e2e8f0',
              borderRadius: 10,
              padding: 24,
              width: 720,
              maxHeight: '80vh',
              display: 'flex',
              flexDirection: 'column',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
              <strong>运行日志 - {logModal.runId}</strong>
              <button
                onClick={() => setLogModal({ open: false, runId: '', content: '', loading: false })}
                style={{ background: 'none', border: 'none', color: '#94a3b8', cursor: 'pointer', fontSize: 18 }}
              >
                关闭
              </button>
            </div>
            <pre
              style={{
                flex: 1,
                overflowY: 'auto',
                fontSize: 11,
                lineHeight: 1.5,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-all',
                margin: 0,
              }}
            >
              {logModal.loading ? '加载中...' : logModal.content || '（日志为空）'}
            </pre>
          </div>
        </div>
      )}

      <div style={card}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
          <strong style={{ fontSize: 14 }}>引擎状态</strong>
          <button
            onClick={loadEngines}
            disabled={enginesLoading}
            style={{
              fontSize: 12,
              padding: '3px 10px',
              borderRadius: 4,
              border: '1px solid #e2e8f0',
              cursor: 'pointer',
              background: '#f8fafc',
            }}
          >
            {enginesLoading ? '刷新中...' : '刷新'}
          </button>
        </div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          {engines.length === 0 && !enginesLoading && (
            <span style={{ fontSize: 12, color: '#94a3b8' }}>暂无引擎信息。</span>
          )}
          {engines.map(engine => (
            <EngineStatusCard
              key={engine.engine_code}
              engine={engine}
              selected={selectedEngine === engine.engine_code}
              onSelect={setSelectedEngine}
            />
          ))}
        </div>
      </div>

      <div style={card}>
        <strong style={{ fontSize: 14, display: 'block', marginBottom: 10 }}>提交生产任务</strong>

        <div style={{ display: 'flex', gap: 12, marginBottom: 10, flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: 180 }}>
            <label style={{ fontSize: 12, color: '#64748b', display: 'block', marginBottom: 4 }}>处理模板</label>
            <select
              value={selectedProfile}
              onChange={event => setSelectedProfile(event.target.value)}
              disabled={readOnly || !currentEngineObj?.available}
              style={{
                width: '100%',
                padding: '5px 8px',
                borderRadius: 4,
                border: '1px solid #e2e8f0',
                fontSize: 13,
              }}
            >
              {currentProfiles.map(profile => (
                <option key={profile.code} value={profile.code}>
                  {profile.label}
                </option>
              ))}
            </select>
            {currentProfileObj?.description && (
              <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 4 }}>{currentProfileObj.description}</div>
            )}
          </div>

          <div style={{ flex: 2, minWidth: 280 }}>
            <label style={{ fontSize: 12, color: '#64748b', display: 'block', marginBottom: 4 }}>根目录</label>
            <input
              value={rootDir}
              onChange={event => setRootDir(event.target.value)}
              placeholder="批处理根目录或单个任务目录"
              disabled={readOnly}
              style={{
                width: '100%',
                padding: '5px 8px',
                borderRadius: 4,
                border: '1px solid #e2e8f0',
                fontSize: 13,
                boxSizing: 'border-box',
              }}
            />
          </div>
        </div>

        <div style={{ display: 'flex', gap: 12, marginBottom: 10, flexWrap: 'wrap' }}>
          <div style={{ minWidth: 120 }}>
            <label style={{ fontSize: 12, color: '#64748b', display: 'block', marginBottom: 4 }}>
              任务数量（0 表示全部）
            </label>
            <input
              type="number"
              min={0}
              value={numToProcess}
              onChange={event => setNumToProcess(event.target.value)}
              disabled={readOnly}
              style={{ width: 120, padding: '5px 8px', borderRadius: 4, border: '1px solid #e2e8f0', fontSize: 13 }}
            />
          </div>
          <div style={{ minWidth: 140 }}>
            <label style={{ fontSize: 12, color: '#64748b', display: 'block', marginBottom: 4 }}>
              超时时间（秒，可选）
            </label>
            <input
              type="number"
              min={60}
              value={timeoutSec}
              onChange={event => setTimeoutSec(event.target.value)}
              placeholder="默认"
              disabled={readOnly}
              style={{ width: 140, padding: '5px 8px', borderRadius: 4, border: '1px solid #e2e8f0', fontSize: 13 }}
            />
          </div>
        </div>

        {Object.keys(currentParamSchema).length > 0 && (
          <div
            style={{
              marginBottom: 10,
              padding: '10px 12px',
              background: '#f8fafc',
              borderRadius: 6,
              border: '1px solid #e2e8f0',
            }}
          >
            <div style={{ fontSize: 12, color: '#0f172a', marginBottom: 8, fontWeight: 600 }}>处理参数</div>
            <div
              style={{
                fontSize: 12,
                color: '#475569',
                lineHeight: 1.6,
                marginBottom: 10,
                padding: '8px 10px',
                background: '#ffffff',
                border: '1px solid #e2e8f0',
                borderRadius: 6,
              }}
            >
              这些参数主要影响目标网格大小、精裁剪范围、地理编码范围和位移结果掩膜。建议先使用默认值，通常优先只调整目标网格大小；只有在边缘被裁切、时间窗异常或噪声较多时，再继续调整其他参数。
            </div>
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
              {Object.entries(currentParamSchema).map(([name, schema]) => (
                <ParamField
                  key={name}
                  name={name}
                  schema={schema}
                  value={engineExtraParams[name]}
                  disabled={readOnly}
                  onChange={handleParamChange}
                />
              ))}
            </div>
          </div>
        )}

        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <button
            onClick={handleSubmit}
            disabled={isSubmitDisabled}
            style={{
              padding: '6px 20px',
              borderRadius: 6,
              border: 'none',
              background: currentEngineObj?.available ? '#3b82f6' : '#94a3b8',
              color: '#fff',
              cursor: currentEngineObj?.available ? 'pointer' : 'not-allowed',
              fontSize: 13,
              fontWeight: 600,
            }}
          >
            {submitting ? '提交中...' : '提交任务'}
          </button>
          {submitMsg && (
            <span style={{ fontSize: 12, color: submitError ? '#ef4444' : '#16a34a' }}>
              {submitMsg}
            </span>
          )}
        </div>
      </div>

      <div style={card}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
          <strong style={{ fontSize: 14 }}>运行监控</strong>
          <button
            onClick={() => {
              loadRuns();
              loadActiveTask();
            }}
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

        {monitoredTask && (
          <div
            style={{
              marginBottom: 10,
              padding: '8px 10px',
              background: showingRecentTask ? '#eff6ff' : '#fefce8',
              borderRadius: 6,
              border: `1px solid ${showingRecentTask ? '#bfdbfe' : '#fde68a'}`,
            }}
          >
            <div
              style={{
                fontSize: 12,
                fontWeight: 600,
                color: showingRecentTask ? '#1d4ed8' : '#92400e',
                marginBottom: 4,
              }}
            >
              {showingRecentTask ? '最近一次任务' : '当前任务'}
            </div>
            <div style={{ fontSize: 12, color: showingRecentTask ? '#1e40af' : '#78350f', wordBreak: 'break-all' }}>
              {monitoredTask.task_id} - {formatTaskType(monitoredTask.task_type)} - {formatStatus(monitoredTask.status)} - {monitoredTask.message}
            </div>
            {monitoredTask.progress != null && (
              <div style={{ marginTop: 6 }}>
                <div
                  style={{
                    height: 6,
                    background: showingRecentTask ? '#dbeafe' : '#fde68a',
                    borderRadius: 3,
                    overflow: 'hidden',
                  }}
                >
                  <div
                    style={{
                      height: '100%',
                      width: `${monitoredTask.progress}%`,
                      background: showingRecentTask ? '#3b82f6' : '#f59e0b',
                      transition: 'width 0.3s',
                    }}
                  />
                </div>
                <div style={{ fontSize: 11, color: showingRecentTask ? '#1d4ed8' : '#92400e', marginTop: 2 }}>{monitoredTask.progress}%</div>
              </div>
            )}

            <div
              style={{
                marginTop: 8,
                background: '#fff',
                border: `1px solid ${showingRecentTask ? '#bfdbfe' : '#fde68a'}`,
                borderRadius: 6,
                padding: '8px 10px',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <div style={{ fontSize: 11, fontWeight: 600, color: showingRecentTask ? '#1d4ed8' : '#92400e' }}>
                  {showingRecentTask ? '最近一次任务日志' : '当前任务日志'}
                </div>
                {!readOnly && (
                  <button
                    onClick={handleClearTaskLogs}
                    disabled={taskLogActionLoading || taskLogs.length === 0}
                    style={{
                      fontSize: 11,
                      padding: '2px 8px',
                      borderRadius: 4,
                      border: '1px solid #fcd34d',
                      background: taskLogActionLoading || taskLogs.length === 0 ? '#fef3c7' : '#fff7ed',
                      color: '#9a3412',
                      cursor: taskLogActionLoading || taskLogs.length === 0 ? 'not-allowed' : 'pointer',
                    }}
                  >
                    {taskLogActionLoading && taskLogDeletingId == null ? '清空中...' : '清空日志'}
                  </button>
                )}
              </div>
              {taskLogsLoading ? (
                <div style={{ fontSize: 11, color: showingRecentTask ? '#1d4ed8' : '#a16207' }}>加载中...</div>
              ) : taskLogs.length === 0 ? (
                <div style={{ fontSize: 11, color: showingRecentTask ? '#1d4ed8' : '#a16207' }}>暂无日志。</div>
              ) : (
                <div style={{ maxHeight: 220, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {taskLogs.map((log, index) => (
                    <div
                      key={log.id || `${log.timestamp || 'log'}-${index}`}
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        gap: 8,
                        alignItems: 'flex-start',
                      }}
                    >
                      <div
                        style={{
                          flex: 1,
                          minWidth: 0,
                          fontSize: 11,
                          lineHeight: 1.45,
                          color: log.level === 'ERROR' ? '#b91c1c' : log.level === 'WARNING' ? '#b45309' : '#334155',
                        }}
                      >
                        <div style={{ color: '#64748b' }}>
                          {(log.timestamp || '').replace('T', ' ').replace('Z', '')} [{log.level}]
                        </div>
                        <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{log.message}</div>
                      </div>
                      {!readOnly && (
                        <button
                          onClick={() => handleDeleteTaskLog(log.id)}
                          disabled={taskLogActionLoading || !log.id}
                          style={{
                            flexShrink: 0,
                            fontSize: 11,
                            padding: '2px 8px',
                            borderRadius: 4,
                            border: '1px solid #fecaca',
                            background: '#fef2f2',
                            color: '#b91c1c',
                            cursor: taskLogActionLoading || !log.id ? 'not-allowed' : 'pointer',
                          }}
                        >
                          {taskLogDeletingId === log.id ? '删除中...' : '删除'}
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        <div style={{ fontSize: 12, color: '#64748b', marginBottom: 6 }}>最近 20 条运行记录</div>
        {runsLoading ? (
          <div style={{ fontSize: 12, color: '#94a3b8' }}>加载中...</div>
        ) : runs.length === 0 ? (
          <div style={{ fontSize: 12, color: '#94a3b8' }}>暂无记录。</div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr style={{ background: '#f8fafc' }}>
                {['运行ID', '引擎', '状态', '时间', '操作'].map(header => (
                  <th
                    key={header}
                    style={{ padding: '4px 8px', textAlign: 'left', borderBottom: '1px solid #e2e8f0', color: '#64748b' }}
                  >
                    {header}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {runs.map(run => (
                <tr key={run.run_id} style={{ borderBottom: '1px solid #f1f5f9' }}>
                  <td style={{ padding: '4px 8px', fontFamily: 'monospace', fontSize: 11 }}>{run.run_id}</td>
                  <td style={{ padding: '4px 8px' }}>{formatEngineLabel(run.engine)}</td>
                  <td
                    style={{
                      padding: '4px 8px',
                      color: run.status === 'success' ? '#16a34a' : run.status === 'failed' ? '#ef4444' : '#64748b',
                    }}
                  >
                    {formatStatus(run.status)}
                  </td>
                  <td style={{ padding: '4px 8px', color: '#94a3b8' }}>
                    {run.started_at ? new Date(run.started_at * 1000).toLocaleString() : '-'}
                  </td>
                  <td style={{ padding: '4px 8px' }}>
                    <button
                      onClick={() => handleViewLog(run.run_id)}
                      style={{
                        fontSize: 11,
                        padding: '2px 8px',
                        borderRadius: 3,
                        border: '1px solid #e2e8f0',
                        cursor: 'pointer',
                        background: '#f8fafc',
                      }}
                    >
                      查看日志
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

