import React, { useCallback, useEffect, useState } from 'react';

import { listEngines, listRuns, previewPyintInputAssets, submitRun } from './api/dinsarProduction';
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
  pyint: 'PyINT / Gamma',
  landsar: 'LANDSAR',
};

const TASK_TYPE_LABEL = {
  ISCE2_RUN: 'ISCE2生产',
  PYINT_RUN: 'PyINT生产',
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

const PYINT_DEM_MODE_LABEL = {
  local_fabdem: '本地 FABDEM',
  opentopo: 'OpenTopography',
  prepared_file: '现有 DEM',
};

const PYINT_ORBIT_POLICY_LABEL = {
  validate_only: '仅校验',
  require_txt: '必须存在',
  stage_txt: '校验并留痕',
};

const PYINT_PRECISE_ORBIT_MODE_LABEL = {
  replace: '状态向量替换',
  replace_and_validate: '替换并校验',
};

const RERUN_MODE_LABEL = {
  unfinished_only: '只跑未完成',
  rerun_all: '全部重跑',
};

const RERUN_MODE_OPTIONS = [
  {
    value: 'unfinished_only',
    label: '只跑未完成',
    description: '按当前引擎和当前模板检查已有结果，已完成任务会跳过。',
  },
  {
    value: 'rerun_all',
    label: '全部重跑',
    description: '忽略已有结果，对本次选中的任务全部重新执行。',
  },
];

function formatEngineLabel(engineCode, engineLabel = '') {
  return engineLabel || ENGINE_LABEL[engineCode] || engineCode || '-';
}

function formatTaskType(taskType) {
  return TASK_TYPE_LABEL[taskType] || taskType || '-';
}

function formatStatus(status) {
  return STATUS_LABEL[status] || status || '-';
}

function formatPyintDemMode(mode) {
  return PYINT_DEM_MODE_LABEL[mode] || mode || '-';
}

function formatPyintOrbitPolicy(policy) {
  return PYINT_ORBIT_POLICY_LABEL[policy] || policy || '-';
}

function formatPyintPreciseOrbitMode(mode) {
  return PYINT_PRECISE_ORBIT_MODE_LABEL[mode] || mode || '-';
}

function PreviewIssueList({ title, items, tone = 'warning' }) {
  if (!Array.isArray(items) || items.length === 0) {
    return null;
  }

  const palette = tone === 'error'
    ? {
      background: '#fef2f2',
      border: '#fecaca',
      title: '#b91c1c',
      text: '#7f1d1d',
    }
    : {
      background: '#fff7ed',
      border: '#fed7aa',
      title: '#c2410c',
      text: '#9a3412',
    };

  return (
    <div
      style={{
        padding: '8px 10px',
        borderRadius: 6,
        border: `1px solid ${palette.border}`,
        background: palette.background,
      }}
    >
      <div style={{ fontSize: 12, fontWeight: 600, color: palette.title, marginBottom: 6 }}>{title}</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {items.slice(0, 6).map((item, index) => (
          <div key={`${title}-${index}`} style={{ fontSize: 11, lineHeight: 1.45, color: palette.text }}>
            {item}
          </div>
        ))}
        {items.length > 6 && (
          <div style={{ fontSize: 11, color: palette.text }}>其余 {items.length - 6} 项已折叠。</div>
        )}
      </div>
    </div>
  );
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

function buildParamSections(schema) {
  const sections = [];
  const indexByTitle = new Map();
  Object.entries(schema || {}).forEach(([name, item]) => {
    const title = item.section || '处理参数';
    if (!indexByTitle.has(title)) {
      indexByTitle.set(title, sections.length);
      sections.push({ title, items: [] });
    }
    sections[indexByTitle.get(title)].items.push([name, item]);
  });
  return sections;
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

  if (Array.isArray(schema.enum) && schema.enum.length > 0) {
    return (
      <div style={{ minWidth: 180, flex: '0 1 220px' }}>
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
        <select
          value={value ?? schema.default ?? schema.enum[0]}
          disabled={disabled || isReadonly}
          onChange={event => onChange(name, event.target.value)}
          style={inputStyle}
        >
          {schema.enum.map(option => (
            <option key={option} value={option}>{option}</option>
          ))}
        </select>
        {description && <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 4 }}>{description}</div>}
        {recommendation && <div style={{ fontSize: 11, color: '#2563eb', marginTop: 4 }}>推荐：{recommendation}</div>}
      </div>
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
  const [submitDialogOpen, setSubmitDialogOpen] = useState(false);
  const [rerunMode, setRerunMode] = useState('unfinished_only');
  const [pyintPreview, setPyintPreview] = useState(null);
  const [pyintPreviewLoading, setPyintPreviewLoading] = useState(false);
  const [pyintPreviewFeedback, setPyintPreviewFeedback] = useState({ message: '', error: false });

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
  const currentParamSections = buildParamSections(currentParamSchema);
  const currentDefaultTimeoutSec = Number(currentEngineObj?.default_timeout_seconds || 0) || 0;
  const currentParamHelpText = selectedEngine === 'pyint'
    ? '这些参数影响 PyINT 的多视、并行度以及是否执行解缠/地理编码。建议先直接使用默认值，优先确认当前任务目录里的 LT-1 原始压缩包是否能被正常识别。'
    : selectedEngine === 'isce2'
      ? '这些参数现在按执行、交付、增强分组展示。结果异常时，优先尝试关闭增强项，再回看基础几何和配对质量。'
      : '这些参数影响当前引擎的生产模板。建议先使用默认值，只有在结果边界、噪声或几何表现异常时再逐项调整。';
  const pyintPreviewBlocksSubmit = selectedEngine === 'pyint' && pyintPreview && pyintPreview.allow_submit === false;
  const latestRunWithTask = runs.find(run => run?.task_id) || null;
  const monitoredTask = activeTask || (
    latestRunWithTask
      ? {
        task_id: latestRunWithTask.task_id,
        task_type:
          latestRunWithTask.engine === 'isce2'
            ? 'ISCE2_RUN'
            : latestRunWithTask.engine === 'pyint'
              ? 'PYINT_RUN'
              : 'IDL_RUN_DINSAR',
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
      const nextRuns = data.runs || [];
      setRuns(nextRuns);
      return nextRuns;
    } catch {
      setRuns([]);
      return [];
    } finally {
      setRunsLoading(false);
    }
  }, []);

  const loadActiveTask = useCallback(async () => {
    try {
      const data = await getActiveTasks();
      const tasks = Array.isArray(data) ? data : (data?.tasks || []);
      const relevantTask = tasks.find(task => ['ISCE2_RUN', 'PYINT_RUN', 'IDL_RUN_DINSAR'].includes(task.task_type)) || null;
      setActiveTask(relevantTask);
      return relevantTask;
    } catch {
      setActiveTask(null);
      return null;
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

  const refreshMonitor = useCallback(async () => {
    const [nextRuns, nextActiveTask] = await Promise.all([
      loadRuns(),
      loadActiveTask(),
    ]);
    const fallbackTaskId = nextActiveTask?.task_id || nextRuns.find(run => run?.task_id)?.task_id || '';
    await loadTaskLogs(fallbackTaskId);
  }, [loadActiveTask, loadRuns, loadTaskLogs]);

  useEffect(() => {
    loadEngines();
    refreshMonitor();
  }, [loadEngines, refreshMonitor]);

  useEffect(() => {
    if (currentProfiles.length > 0) {
      setSelectedProfile(currentProfiles[0].code);
    }
  }, [selectedEngine, currentProfiles]);

  useEffect(() => {
    setEngineExtraParams(buildDefaults(currentParamSchema));
  }, [selectedEngine, selectedProfile, currentParamSchema]);

  useEffect(() => {
    if (currentDefaultTimeoutSec > 0) {
      setTimeoutSec(String(currentDefaultTimeoutSec));
      return;
    }
    setTimeoutSec('');
  }, [selectedEngine, currentDefaultTimeoutSec]);

  useEffect(() => {
    setPyintPreview(null);
    setPyintPreviewFeedback({ message: '', error: false });
  }, [selectedEngine, rootDir, numToProcess]);

  const handleParamChange = useCallback((name, value) => {
    setEngineExtraParams(current => ({
      ...current,
      [name]: value,
    }));
  }, []);

  const handlePreviewPyint = useCallback(async () => {
    if (!rootDir.trim()) {
      setPyintPreview(null);
      setPyintPreviewFeedback({ message: '请先输入根目录。', error: true });
      return;
    }

    setPyintPreviewLoading(true);
    setPyintPreviewFeedback({ message: '', error: false });
    try {
      const data = await previewPyintInputAssets({
        root_dir: rootDir.trim(),
        num_to_process: Number(numToProcess) || 0,
      });
      setPyintPreview(data);
      const taskCount = Number(data?.selected_task_count || data?.task_count || 0);
      const orbitMissingCount = Number(data?.orbits?.missing_task_count || 0);
      const detail = data?.allow_submit
        ? `预检完成，可提交 ${taskCount} 个任务。`
        : `预检完成，存在阻塞项；涉及 ${orbitMissingCount} 个轨道未齐全任务。`;
      setPyintPreviewFeedback({ message: detail, error: !data?.allow_submit });
    } catch (err) {
      setPyintPreview(null);
      setPyintPreviewFeedback({
        message: `预检失败：${err?.response?.data?.detail || err.message}`,
        error: true,
      });
    } finally {
      setPyintPreviewLoading(false);
    }
  }, [numToProcess, rootDir]);

  const handleOpenSubmitDialog = useCallback(() => {
    if (!rootDir.trim()) {
      setSubmitError(true);
      setSubmitMsg('请输入根目录。');
      return;
    }
    if (pyintPreviewBlocksSubmit) {
      setSubmitError(true);
      setSubmitMsg('PyINT 输入资产预检未通过，请先修复阻塞项。');
      return;
    }
    setSubmitError(false);
    setSubmitMsg('');
    setSubmitDialogOpen(true);
  }, [pyintPreviewBlocksSubmit, rootDir]);

  const handleCloseSubmitDialog = useCallback(() => {
    if (submitting) return;
    setSubmitDialogOpen(false);
  }, [submitting]);

  const handleSubmit = async () => {
    setSubmitting(true);
    setSubmitMsg('');
    setSubmitError(false);
    try {
      const extra = buildExtraPayload(currentParamSchema, engineExtraParams);
      setSubmitDialogOpen(false);
      const result = await submitRun({
        engine_code: selectedEngine,
        profile: selectedProfile,
        root_dir: rootDir.trim(),
        num_to_process: Number(numToProcess) || 0,
        rerun_mode: rerunMode,
        timeout_seconds: timeoutSec ? Number(timeoutSec) : null,
        extra,
      });
      const taskCount = result?.selected_task_count ? `，选中 ${result.selected_task_count} 个任务` : '';
      const skippedCompleted = Number(result?.skipped_completed_count || 0);
      const skippedText = skippedCompleted > 0 ? `，跳过 ${skippedCompleted} 个已完成任务` : '';
      setSubmitError(false);
      setSubmitMsg(`任务已入队：${result.task_id}${taskCount}${skippedText}`);
      if (onJobQueued) onJobQueued(result.task_id);
      await refreshMonitor();
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

  const isSubmitDisabled = readOnly || submitting || !currentEngineObj?.available || pyintPreviewBlocksSubmit;

  return (
    <div style={{ padding: '16px 0', width: '100%' }}>
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

      {submitDialogOpen && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(15,23,42,0.42)',
            zIndex: 9998,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 20,
          }}
        >
          <div
            style={{
              width: 'min(560px, 100%)',
              background: '#fff',
              borderRadius: 14,
              border: '1px solid #cbd5e1',
              boxShadow: '0 20px 60px rgba(15, 23, 42, 0.24)',
              padding: 20,
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginBottom: 10 }}>
              <strong style={{ fontSize: 16, color: '#0f172a' }}>提交生产任务</strong>
              <button
                onClick={handleCloseSubmitDialog}
                disabled={submitting}
                style={{
                  border: 'none',
                  background: 'none',
                  color: '#64748b',
                  cursor: submitting ? 'not-allowed' : 'pointer',
                  fontSize: 13,
                }}
              >
                关闭
              </button>
            </div>

            <div style={{ fontSize: 12, color: '#475569', lineHeight: 1.6, marginBottom: 12 }}>
              选择本次批处理策略。任务数量限制会在“只跑未完成”过滤之后再生效，按当前引擎和当前模板判断已完成状态。
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 14 }}>
              {RERUN_MODE_OPTIONS.map(option => {
                const selected = rerunMode === option.value;
                return (
                  <label
                    key={option.value}
                    style={{
                      display: 'flex',
                      gap: 12,
                      alignItems: 'flex-start',
                      padding: 12,
                      borderRadius: 10,
                      border: selected ? '2px solid #3b82f6' : '1px solid #dbeafe',
                      background: selected ? '#eff6ff' : '#f8fafc',
                      cursor: 'pointer',
                    }}
                  >
                    <input
                      type="radio"
                      name="rerun-mode"
                      checked={selected}
                      onChange={() => setRerunMode(option.value)}
                    />
                    <div>
                      <div style={{ fontSize: 13, fontWeight: 600, color: '#0f172a', marginBottom: 4 }}>{option.label}</div>
                      <div style={{ fontSize: 12, color: '#64748b', lineHeight: 1.5 }}>{option.description}</div>
                    </div>
                  </label>
                );
              })}
            </div>

            <div
              style={{
                marginBottom: 16,
                padding: '10px 12px',
                borderRadius: 10,
                border: '1px solid #e2e8f0',
                background: '#f8fafc',
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
                gap: 8,
              }}
            >
              <div style={{ fontSize: 12, color: '#475569' }}>引擎：{formatEngineLabel(selectedEngine, currentEngineObj?.engine_label)}</div>
              <div style={{ fontSize: 12, color: '#475569' }}>模板：{currentProfileObj?.label || selectedProfile}</div>
              <div style={{ fontSize: 12, color: '#475569' }}>任务数量：{Number(numToProcess) > 0 ? Number(numToProcess) : '全部'}</div>
              <div style={{ fontSize: 12, color: '#475569' }}>执行策略：{RERUN_MODE_LABEL[rerunMode] || rerunMode}</div>
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
              <button
                onClick={handleCloseSubmitDialog}
                disabled={submitting}
                style={{
                  padding: '6px 14px',
                  borderRadius: 6,
                  border: '1px solid #cbd5e1',
                  background: '#fff',
                  color: '#0f172a',
                  cursor: submitting ? 'not-allowed' : 'pointer',
                }}
              >
                取消
              </button>
              <button
                onClick={handleSubmit}
                disabled={submitting}
                style={{
                  padding: '6px 16px',
                  borderRadius: 6,
                  border: 'none',
                  background: '#2563eb',
                  color: '#fff',
                  cursor: submitting ? 'not-allowed' : 'pointer',
                  fontWeight: 600,
                }}
              >
                {submitting ? '提交中...' : `确认 ${RERUN_MODE_LABEL[rerunMode] || ''}`}
              </button>
            </div>
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
              超时时间（秒）
            </label>
            <input
              type="number"
              min={60}
              value={timeoutSec}
              onChange={event => setTimeoutSec(event.target.value)}
              placeholder={currentDefaultTimeoutSec > 0 ? `默认 ${currentDefaultTimeoutSec}` : '默认'}
              disabled={readOnly}
              style={{ width: 140, padding: '5px 8px', borderRadius: 4, border: '1px solid #e2e8f0', fontSize: 13 }}
            />
            {selectedEngine === 'isce2' && currentDefaultTimeoutSec > 0 && (
              <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 4 }}>
                ISCE2 默认按单对任务使用 {currentDefaultTimeoutSec} 秒；批量目录会串行逐对套用该超时。
              </div>
            )}
            {selectedEngine === 'pyint' && currentDefaultTimeoutSec > 0 && (
              <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 4 }}>
                PyINT 默认按单对任务使用 {currentDefaultTimeoutSec} 秒；当前会逐对串行创建工作区并运行外部 PyINT / Gamma 流程。
              </div>
            )}
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
              {currentParamHelpText}
            </div>
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
              {currentParamSections.map(section => (
                <div key={section.title} style={{ width: '100%' }}>
                  <div style={{ fontSize: 12, color: '#0f172a', fontWeight: 600, marginBottom: 8 }}>
                    {section.title}
                  </div>
                  <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                    {section.items.map(([name, schema]) => (
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
              ))}
            </div>
          </div>
        )}

        {selectedEngine === 'pyint' && (
          <div
            style={{
              marginBottom: 10,
              padding: '10px 12px',
              background: '#f8fafc',
              borderRadius: 6,
              border: '1px solid #e2e8f0',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
              <div>
                <div style={{ fontSize: 12, color: '#0f172a', fontWeight: 600 }}>PyINT 输入资产预检</div>
                <div style={{ fontSize: 11, color: '#64748b', marginTop: 4 }}>
                  提交前检查 Task_* 结构、DEM 策略和 LT-1 轨道是否齐备。即使不手动预检，后端提交时也会做同样校验。
                </div>
              </div>
              <button
                onClick={handlePreviewPyint}
                disabled={readOnly || pyintPreviewLoading || !rootDir.trim()}
                style={{
                  fontSize: 12,
                  padding: '5px 12px',
                  borderRadius: 6,
                  border: '1px solid #cbd5e1',
                  cursor: readOnly || pyintPreviewLoading || !rootDir.trim() ? 'not-allowed' : 'pointer',
                  background: '#fff',
                  color: '#0f172a',
                }}
              >
                {pyintPreviewLoading ? '预检中...' : '预检输入资产'}
              </button>
            </div>

            {pyintPreviewFeedback.message && (
              <div
                style={{
                  marginBottom: pyintPreview ? 10 : 0,
                  padding: '8px 10px',
                  borderRadius: 6,
                  border: `1px solid ${pyintPreviewFeedback.error ? '#fecaca' : '#bbf7d0'}`,
                  background: pyintPreviewFeedback.error ? '#fef2f2' : '#f0fdf4',
                  color: pyintPreviewFeedback.error ? '#b91c1c' : '#15803d',
                  fontSize: 12,
                  lineHeight: 1.5,
                }}
              >
                {pyintPreviewFeedback.message}
              </div>
            )}

            {!pyintPreview && !pyintPreviewLoading && (
              <div style={{ fontSize: 11, color: '#94a3b8' }}>
                尚未执行预检。建议在首次处理新批次前先预检一次。
              </div>
            )}

            {pyintPreview && (
              <>
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
                    gap: 8,
                    marginBottom: 10,
                  }}
                >
                  {[
                    { label: '任务数', value: pyintPreview.selected_task_count ?? pyintPreview.task_count ?? 0, color: '#0f172a' },
                  { label: 'DEM 策略', value: formatPyintDemMode(pyintPreview?.dem?.mode), color: '#1d4ed8' },
                  { label: '轨道策略', value: formatPyintOrbitPolicy(pyintPreview?.orbits?.policy), color: '#7c3aed' },
                  {
                    label: '精轨桥接',
                    value: pyintPreview?.precise_orbit_bridge?.enabled
                      ? formatPyintPreciseOrbitMode(pyintPreview?.precise_orbit_bridge?.mode)
                      : '关闭',
                    color: pyintPreview?.precise_orbit_bridge?.enabled ? '#0f766e' : '#64748b',
                  },
                  { label: '可提交', value: pyintPreview.allow_submit ? '是' : '否', color: pyintPreview.allow_submit ? '#15803d' : '#b91c1c' },
                  { label: '缺轨道任务', value: pyintPreview?.orbits?.missing_task_count ?? 0, color: (pyintPreview?.orbits?.missing_task_count || 0) > 0 ? '#b91c1c' : '#475569' },
                  { label: '无效目录', value: pyintPreview?.invalid_candidates?.length ?? 0, color: (pyintPreview?.invalid_candidates?.length || 0) > 0 ? '#c2410c' : '#475569' },
                ].map(item => (
                    <div
                      key={item.label}
                      style={{
                        padding: '8px 10px',
                        borderRadius: 6,
                        border: '1px solid #e2e8f0',
                        background: '#fff',
                      }}
                    >
                      <div style={{ fontSize: 11, color: '#64748b', marginBottom: 4 }}>{item.label}</div>
                      <div style={{ fontSize: 13, fontWeight: 600, color: item.color, wordBreak: 'break-word' }}>{item.value}</div>
                    </div>
                  ))}
                </div>

                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
                    gap: 8,
                    marginBottom: 10,
                  }}
                >
                  <PreviewIssueList title="阻塞项" items={pyintPreview.blockers || []} tone="error" />
                  <PreviewIssueList title="警告" items={pyintPreview.warnings || []} tone="warning" />
                </div>

                <div style={{ fontSize: 12, color: '#475569', marginBottom: 6 }}>任务级预检结果</div>
                <div
                  style={{
                    border: '1px solid #e2e8f0',
                    borderRadius: 6,
                    overflowX: 'auto',
                    overflowY: 'hidden',
                    background: '#fff',
                  }}
                >
                  <div
                    style={{
                      display: 'grid',
                      gridTemplateColumns: 'minmax(180px, 1.6fr) minmax(70px, 0.8fr) minmax(220px, 1.5fr) minmax(70px, 0.7fr)',
                      minWidth: 560,
                      gap: 0,
                      background: '#f8fafc',
                      borderBottom: '1px solid #e2e8f0',
                    }}
                  >
                    {['任务', '源文件', '轨道解析', '提交'].map(header => (
                      <div key={header} style={{ padding: '8px 10px', fontSize: 11, color: '#64748b', fontWeight: 600 }}>
                        {header}
                      </div>
                    ))}
                  </div>
                  {(pyintPreview.tasks || []).map(task => {
                    const masterOrbit = task?.orbit_resolution?.master;
                    const slaveOrbit = task?.orbit_resolution?.slave;
                    return (
                      <div
                        key={task.task_dir}
                        style={{
                          display: 'grid',
                          gridTemplateColumns: 'minmax(180px, 1.6fr) minmax(70px, 0.8fr) minmax(220px, 1.5fr) minmax(70px, 0.7fr)',
                          minWidth: 560,
                          borderBottom: '1px solid #f1f5f9',
                        }}
                      >
                        <div style={{ padding: '8px 10px', minWidth: 0 }}>
                          <div style={{ fontSize: 12, color: '#0f172a', fontWeight: 600 }}>{task.task_alias || task.task_name}</div>
                          <div style={{ fontSize: 11, color: '#64748b', marginTop: 4 }}>
                            {task.master_date || '-'} / {task.slave_date || '-'}
                          </div>
                        </div>
                        <div style={{ padding: '8px 10px', fontSize: 11, color: '#334155' }}>
                          M {task?.archive_counts?.master ?? 0}
                          <br />
                          S {task?.archive_counts?.slave ?? 0}
                        </div>
                        <div style={{ padding: '8px 10px', fontSize: 11, lineHeight: 1.5 }}>
                          <div style={{ color: masterOrbit?.resolved ? '#15803d' : '#b91c1c' }}>
                            M {masterOrbit?.resolved ? `${masterOrbit.satellite}/${masterOrbit.date}` : '缺失'}
                          </div>
                          <div style={{ color: slaveOrbit?.resolved ? '#15803d' : '#b91c1c' }}>
                            S {slaveOrbit?.resolved ? `${slaveOrbit.satellite}/${slaveOrbit.date}` : '缺失'}
                          </div>
                        </div>
                        <div style={{ padding: '8px 10px', fontSize: 11, fontWeight: 600, color: task.allow_submit ? '#15803d' : '#b91c1c' }}>
                          {task.allow_submit ? '可提交' : '阻塞'}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </>
            )}
          </div>
        )}

        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <button
            onClick={handleOpenSubmitDialog}
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
            onClick={refreshMonitor}
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
        <div style={{ fontSize: 11, color: '#94a3b8', marginBottom: 10 }}>
          监控与日志改为手动刷新，避免界面持续轮询请求。
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

