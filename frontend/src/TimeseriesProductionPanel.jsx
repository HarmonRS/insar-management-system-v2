import React, { useCallback, useEffect, useMemo, useState } from 'react';

import { getPsBatches } from './api/taskBatches';
import { useBatchStore } from './store';
import {
  createTimeseriesRun,
  getTimeseriesPreparedStack,
  getTimeseriesRunDetail,
  listTimeseriesRuns,
  retryTimeseriesStep,
  runSarscapeSbasPreflight,
  runTimeseriesPreflight,
  runTimeseriesWslCheck,
} from './api/timeseriesProduction';

const card = {
  background: '#fff',
  padding: '12px',
  borderRadius: '8px',
  border: '1px solid #e2e8f0',
  marginBottom: '12px',
};

const STATUS_COLOR = {
  PENDING: '#64748b',
  RUNNING: '#2563eb',
  PREPARING: '#2563eb',
  PREPARED: '#16a34a',
  STACK_PREPARING: '#d97706',
  STACK_PREPARED: '#0891b2',
  MATERIALIZING: '#7c3aed',
  MATERIALIZED: '#9333ea',
  STACK_READY: '#15803d',
  STACK_RUNNING: '#ea580c',
  STACK_COMPLETED: '#16a34a',
  MINTPY_RUNNING: '#2563eb',
  MINTPY_COMPLETED: '#0f766e',
  EXPORTING: '#c2410c',
  EXPORTED: '#0891b2',
  REGISTERING: '#7c3aed',
  FAILED: '#dc2626',
  PUBLISHED: '#16a34a',
};

const PREPARED_STACK_STATE = {
  not_prepared: { label: 'Not prepared', color: '#64748b' },
  manifest_unreadable: { label: 'Manifest unreadable', color: '#dc2626' },
  prepared_invalid: { label: 'Prepared invalid', color: '#dc2626' },
  prepared: { label: 'Prepared', color: '#16a34a' },
  processor_blocked: { label: 'Processor blocked', color: '#d97706' },
  ready_for_execution: { label: 'Ready for execution', color: '#15803d' },
};

function formatDateTime(value) {
  if (!value) return '-';
  try {
    return new Date(value).toLocaleString();
  } catch {
    return String(value);
  }
}

function StateBadge({ value }) {
  const state = PREPARED_STACK_STATE[value] || { label: value || 'Unknown', color: '#64748b' };
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '2px 10px',
        borderRadius: 999,
        background: `${state.color}14`,
        color: state.color,
        fontSize: 12,
        fontWeight: 600,
      }}
    >
      <span
        style={{
          width: 7,
          height: 7,
          borderRadius: '50%',
          background: state.color,
          display: 'inline-block',
        }}
      />
      {state.label}
    </span>
  );
}

function StatusPill({ value }) {
  const color = STATUS_COLOR[value] || '#64748b';
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '2px 10px',
        borderRadius: 999,
        background: `${color}14`,
        color,
        fontSize: 12,
        fontWeight: 600,
      }}
    >
      <span
        style={{
          width: 7,
          height: 7,
          borderRadius: '50%',
          background: color,
          display: 'inline-block',
        }}
      />
      {value || 'UNKNOWN'}
    </span>
  );
}

function formatBytes(value) {
  const size = Number(value || 0);
  if (!Number.isFinite(size) || size <= 0) return '-';
  if (size < 1024) return `${size} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let current = size / 1024;
  let unitIndex = 0;
  while (current >= 1024 && unitIndex < units.length - 1) {
    current /= 1024;
    unitIndex += 1;
  }
  return `${current.toFixed(current >= 100 ? 0 : 1)} ${units[unitIndex]}`;
}

function QualityBadge({ ok, okLabel = '通过', failLabel = '失败' }) {
  const color = ok ? '#166534' : '#991b1b';
  const background = ok ? '#f0fdf4' : '#fef2f2';
  const border = ok ? '#bbf7d0' : '#fecaca';
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '2px 10px',
        borderRadius: 999,
        border: `1px solid ${border}`,
        background,
        color,
        fontSize: 12,
        fontWeight: 600,
      }}
    >
      <span
        style={{
          width: 7,
          height: 7,
          borderRadius: '50%',
          background: color,
          display: 'inline-block',
        }}
      />
      {ok ? okLabel : failLabel}
    </span>
  );
}

function JsonBlock({ value }) {
  return (
    <pre
      style={{
        margin: 0,
        padding: '8px 10px',
        background: '#f8fafc',
        borderRadius: 6,
        border: '1px solid #e2e8f0',
        fontSize: 11,
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-all',
      }}
    >
      {JSON.stringify(value || {}, null, 2)}
    </pre>
  );
}

export default function TimeseriesProductionPanel({ readOnly = false, onJobQueued }) {
  const pendingTimeseriesBatchId = useBatchStore(state => state.pendingTimeseriesBatchId);
  const setPendingTimeseriesBatchId = useBatchStore(state => state.setPendingTimeseriesBatchId);
  const [batches, setBatches] = useState([]);
  const [runs, setRuns] = useState([]);
  const [selectedBatchId, setSelectedBatchId] = useState('');
  const [selectedRunId, setSelectedRunId] = useState('');
  const [selectedRunDetail, setSelectedRunDetail] = useState(null);
  const [preparedStackSummary, setPreparedStackSummary] = useState(null);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState('');
  const [runName, setRunName] = useState('');
  const [referenceDate, setReferenceDate] = useState('');
  const [waterMaskMode, setWaterMaskMode] = useState('synthetic_fallback');
  const [processorCode, setProcessorCode] = useState('sarscape_sbas');
  const [executionMode, setExecutionMode] = useState('preflight_only');
  const [notes, setNotes] = useState('');
  const [wslChecking, setWslChecking] = useState(false);
  const [wslReport, setWslReport] = useState(null);
  const [preflightLoading, setPreflightLoading] = useState(false);
  const [preflightReport, setPreflightReport] = useState(null);
  const [retryingStepId, setRetryingStepId] = useState('');

  const selectedBatch = useMemo(
    () => batches.find(item => item.batch_id === selectedBatchId) || null,
    [batches, selectedBatchId]
  );

  const loadBatches = useCallback(async () => {
    try {
      const data = await getPsBatches();
      const nextItems = Array.isArray(data) ? data : [];
      setBatches(nextItems);
      setSelectedBatchId(current => {
        if (pendingTimeseriesBatchId && nextItems.some(item => item.batch_id === pendingTimeseriesBatchId)) {
          return pendingTimeseriesBatchId;
        }
        return current || nextItems[0]?.batch_id || '';
      });
    } catch {
      setBatches([]);
    }
  }, [pendingTimeseriesBatchId]);

  const loadRuns = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listTimeseriesRuns({ limit: 20, offset: 0 });
      const nextItems = Array.isArray(data?.items) ? data.items : [];
      setRuns(nextItems);
      setSelectedRunId(current => current || nextItems[0]?.run_id || '');
    } catch {
      setRuns([]);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadRunDetail = useCallback(async runId => {
    if (!runId) {
      setSelectedRunDetail(null);
      setPreparedStackSummary(null);
      return;
    }
    setDetailLoading(true);
    try {
      const [detail, stackSummary] = await Promise.all([
        getTimeseriesRunDetail(runId),
        getTimeseriesPreparedStack(runId).catch(error => ({
          error: error?.response?.data?.detail || error.message || 'Prepared stack summary load failed',
        })),
      ]);
      setSelectedRunDetail(detail);
      setPreparedStackSummary(stackSummary);
    } catch (error) {
      setSelectedRunDetail({
        error: error?.response?.data?.detail || error.message || '运行详情加载失败',
      });
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const handleWslCheck = useCallback(async () => {
    setWslChecking(true);
    try {
      const report = await runTimeseriesWslCheck();
      setWslReport(report);
    } catch (error) {
      setWslReport({
        overall_ok: false,
        message: error?.response?.data?.detail || error.message || 'WSL 检查失败',
        checks: [],
      });
      setPreparedStackSummary(null);
    } finally {
      setWslChecking(false);
    }
  }, []);

  const handleRetryStep = useCallback(async stepId => {
    if (!selectedRunId || !stepId) return;
    setRetryingStepId(stepId);
    setMessage('');
    try {
      await retryTimeseriesStep(selectedRunId, { step_id: stepId });
      setMessage(`已重新入队：${selectedRunId} / ${stepId}`);
      await loadRuns();
      await loadRunDetail(selectedRunId);
    } catch (error) {
      setMessage(error?.response?.data?.detail || error.message || '重试失败');
    } finally {
      setRetryingStepId('');
    }
  }, [loadRunDetail, loadRuns, selectedRunId]);

  const handlePreflight = useCallback(async () => {
    if (!selectedBatchId) {
      setPreflightReport({
        overall_ok: false,
        errors: ['请先选择一个时序批次。'],
        warnings: [],
        checks: [],
        summary: {},
      });
      return;
    }
    setPreflightLoading(true);
    try {
      const basePayload = {
        batch_id: selectedBatchId,
        reference_date: referenceDate.trim() || null,
      };
      const report = processorCode === 'sarscape_sbas'
        ? await runSarscapeSbasPreflight({
          ...basePayload,
          include_task_discovery: true,
          discovery_timeout_seconds: 120,
        })
        : await runTimeseriesPreflight({
          ...basePayload,
          water_mask_mode: waterMaskMode,
        });
      setPreflightReport(report);
    } catch (error) {
      const detail = error?.response?.data?.detail || error.message || '预检失败';
      setPreflightReport({
        overall_ok: false,
        ready_for_pipeline_design: false,
        batch_id: selectedBatchId,
        errors: [detail],
        blockers: [detail],
        warnings: [],
        checks: [],
        summary: {},
      });
    } finally {
      setPreflightLoading(false);
    }
  }, [processorCode, referenceDate, selectedBatchId, waterMaskMode]);

  useEffect(() => {
    loadBatches();
    loadRuns();
  }, [loadBatches, loadRuns]);

  useEffect(() => {
    const timer = setInterval(loadRuns, 10000);
    return () => clearInterval(timer);
  }, [loadRuns]);

  useEffect(() => {
    loadRunDetail(selectedRunId);
  }, [loadRunDetail, selectedRunId]);

  useEffect(() => {
    if (
      pendingTimeseriesBatchId &&
      batches.some(item => item.batch_id === pendingTimeseriesBatchId) &&
      selectedBatchId === pendingTimeseriesBatchId
    ) {
      setPendingTimeseriesBatchId('');
    }
  }, [batches, pendingTimeseriesBatchId, selectedBatchId, setPendingTimeseriesBatchId]);

  useEffect(() => {
    setPreflightReport(null);
  }, [selectedBatchId, referenceDate, waterMaskMode, processorCode, executionMode]);

  const handleSubmit = async () => {
    if (!selectedBatchId) {
      setMessage('请先选择一个时序批次。');
      return;
    }
    setSubmitting(true);
    setMessage('');
    try {
      const result = await createTimeseriesRun({
        batch_id: selectedBatchId,
        run_name: runName.trim() || null,
        reference_date: referenceDate.trim() || null,
        water_mask_mode: waterMaskMode,
        processor_code: processorCode,
        execution_mode: executionMode,
        notes: notes.trim() || null,
      });
      setMessage(`运行已入队：${result.run_id} / task=${result.task_id}`);
      setSelectedRunId(result.run_id);
      onJobQueued?.(result.task_id);
      await loadRuns();
    } catch (error) {
      setMessage(error?.response?.data?.detail || error.message || '运行提交失败');
    } finally {
      setSubmitting(false);
    }
  };

  const runData = selectedRunDetail?.run || null;
  const linkedProduct = selectedRunDetail?.product || null;
  const workflowSteps = selectedRunDetail?.workflow?.steps || [];
  const isSarscapePreflight = preflightReport?.schema === 'insar.sarscape-sbas-preview/v1';
  const preflightChecks = Array.isArray(preflightReport?.checks) ? preflightReport.checks : [];
  const preflightErrors = Array.isArray(preflightReport?.errors)
    ? preflightReport.errors
    : (Array.isArray(preflightReport?.blockers) ? preflightReport.blockers : []);
  const preflightWarnings = Array.isArray(preflightReport?.warnings) ? preflightReport.warnings : [];
  const preflightSummary = preflightReport?.summary || preflightReport?.stack_manifest || {};
  const preflightOk = isSarscapePreflight
    ? !!preflightReport?.ready_for_pipeline_design
    : !!preflightReport?.overall_ok;
  const runPreflightQuality = runData?.quality_summary_json?.preflight || null;
  const runPublishValidation = runData?.quality_summary_json?.publish_validation || null;
  const preparedStack = preparedStackSummary && !preparedStackSummary.error ? preparedStackSummary : null;
  const preparedValidation = preparedStack?.validation || runData?.input_snapshot_json?.prepared_stack_validation || null;
  const preparedBlockers = Array.isArray(preparedStack?.blockers)
    ? preparedStack.blockers
    : (Array.isArray(preparedValidation?.blockers) ? preparedValidation.blockers : []);
  const preparedWarnings = Array.isArray(preparedStack?.warnings)
    ? preparedStack.warnings
    : (Array.isArray(preparedValidation?.warnings) ? preparedValidation.warnings : []);
  const processorManifest = preparedStack?.processor_manifest || null;
  const processorBlockers = Array.isArray(processorManifest?.blockers) ? processorManifest.blockers : [];
  const showPreparedStack = !!(preparedStack || preparedStackSummary?.error || runData?.summary_json?.prepared_stack_id);

  return (
    <div style={{ padding: '16px 0', width: '100%' }}>
      <div style={card}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', marginBottom: 10 }}>
          <strong style={{ fontSize: 14, display: 'block' }}>时序InSAR 运行入口</strong>
          <button
            type="button"
            onClick={handleWslCheck}
            disabled={readOnly || wslChecking}
            style={{
              padding: '4px 10px',
              borderRadius: 6,
              border: '1px solid #cbd5e1',
              background: '#fff',
              cursor: readOnly ? 'not-allowed' : 'pointer',
              fontSize: 12,
            }}
          >
            {wslChecking ? '检查中...' : 'WSL检查'}
          </button>
        </div>
        <div
          style={{
            fontSize: 12,
            color: '#475569',
            lineHeight: 1.6,
            padding: '8px 10px',
            background: '#f8fafc',
            border: '1px solid #e2e8f0',
            borderRadius: 6,
          }}
        >
          当前生产入口采用分层 SBAS 模型：时序配对先形成候选大池，提交 run 后由 prepare 冻结 prepared SBAS 小栈。
          ENVI/SARscape SBAS 后续只读取 prepared manifest 和 selected_network_edges 审计图，不再重新扫描全量数据。
          ISCE2 + MintPy 路径仍沿用 stack_prep、materialize、stack、MintPy、publish、register 链路。
        </div>
        {wslReport && (
          <div
            style={{
              marginTop: 10,
              padding: '10px 12px',
              borderRadius: 6,
              border: `1px solid ${wslReport.overall_ok ? '#bbf7d0' : '#fecaca'}`,
              background: wslReport.overall_ok ? '#f0fdf4' : '#fef2f2',
              fontSize: 12,
              color: wslReport.overall_ok ? '#166534' : '#991b1b',
            }}
          >
            <div style={{ fontWeight: 600, marginBottom: 6 }}>
              {wslReport.overall_ok ? 'WSL运行时正常' : 'WSL运行时存在问题'}
            </div>
            <div style={{ marginBottom: 6 }}>{wslReport.message || '-'}</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {(Array.isArray(wslReport.checks) ? wslReport.checks : []).slice(0, 8).map(check => (
                <div key={check.name}>
                  <strong>{check.ok ? 'OK' : 'FAIL'}</strong> {check.name}
                  {check.detail ? `: ${check.detail}` : ''}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      <div style={card}>
        <strong style={{ fontSize: 14, display: 'block', marginBottom: 10 }}>新建运行</strong>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10 }}>
          <div>
            <div style={{ fontSize: 12, color: '#64748b', marginBottom: 4 }}>时序批次</div>
            <select
              value={selectedBatchId}
              onChange={event => setSelectedBatchId(event.target.value)}
              disabled={readOnly || submitting}
              style={{ width: '100%', padding: '6px 8px', borderRadius: 6, border: '1px solid #cbd5e1' }}
            >
              <option value="">-- 请选择批次 --</option>
              {batches.map(item => (
                <option key={item.batch_id} value={item.batch_id}>
                  {item.name || item.batch_id}
                </option>
              ))}
            </select>
          </div>
          <div>
            <div style={{ fontSize: 12, color: '#64748b', marginBottom: 4 }}>运行名称</div>
            <input
              value={runName}
              onChange={event => setRunName(event.target.value)}
              disabled={readOnly || submitting}
              placeholder="可留空，系统自动生成"
              style={{ width: '100%', padding: '6px 8px', borderRadius: 6, border: '1px solid #cbd5e1', boxSizing: 'border-box' }}
            />
          </div>
          <div>
            <div style={{ fontSize: 12, color: '#64748b', marginBottom: 4 }}>参考日期</div>
            <input
              value={referenceDate}
              onChange={event => setReferenceDate(event.target.value)}
              disabled={readOnly || submitting}
              placeholder="YYYYMMDD，可留空"
              style={{ width: '100%', padding: '6px 8px', borderRadius: 6, border: '1px solid #cbd5e1', boxSizing: 'border-box' }}
            />
          </div>
          <div>
            <div style={{ fontSize: 12, color: '#64748b', marginBottom: 4 }}>水体掩膜策略</div>
            <select
              value={waterMaskMode}
              onChange={event => setWaterMaskMode(event.target.value)}
              disabled={readOnly || submitting}
              style={{ width: '100%', padding: '6px 8px', borderRadius: 6, border: '1px solid #cbd5e1' }}
            >
              <option value="synthetic_fallback">synthetic_fallback</option>
              <option value="local">local</option>
            </select>
          </div>
          <div>
            <div style={{ fontSize: 12, color: '#64748b', marginBottom: 4 }}>Processor</div>
            <select
              value={processorCode}
              onChange={event => {
                const next = event.target.value;
                setProcessorCode(next);
                setExecutionMode(next === 'sarscape_sbas' ? 'preflight_only' : 'full');
              }}
              disabled={readOnly || submitting}
              style={{ width: '100%', padding: '6px 8px', borderRadius: 6, border: '1px solid #cbd5e1' }}
            >
              <option value="sarscape_sbas">ENVI/SARscape SBAS</option>
              <option value="isce2_stack_mintpy">ISCE2 + MintPy</option>
            </select>
          </div>
          <div>
            <div style={{ fontSize: 12, color: '#64748b', marginBottom: 4 }}>Execution</div>
            <select
              value={executionMode}
              onChange={event => setExecutionMode(event.target.value)}
              disabled={readOnly || submitting}
              style={{ width: '100%', padding: '6px 8px', borderRadius: 6, border: '1px solid #cbd5e1' }}
            >
              <option value="preflight_only">Preflight only</option>
              <option value="full">Full execution</option>
            </select>
          </div>
        </div>

        <div style={{ marginTop: 10 }}>
          <div style={{ fontSize: 12, color: '#64748b', marginBottom: 4 }}>备注</div>
          <textarea
            value={notes}
            onChange={event => setNotes(event.target.value)}
            disabled={readOnly || submitting}
            placeholder="记录本次实验目的、约束和特殊配置"
            style={{
              width: '100%',
              minHeight: 68,
              padding: '8px 10px',
              borderRadius: 6,
              border: '1px solid #cbd5e1',
              boxSizing: 'border-box',
              resize: 'vertical',
            }}
          />
        </div>

        {selectedBatch && (
          <div style={{ marginTop: 10, fontSize: 12, color: '#334155', lineHeight: 1.7 }}>
            <div><strong>Stack Plan:</strong>{selectedBatch.plan_id || '-'}</div>
            <div><strong>Plan Strategy:</strong>{selectedBatch.plan_strategy || '-'}</div>
            <div><strong>方向：</strong>{selectedBatch.direction || '-'}</div>
            <div><strong>影像数：</strong>{selectedBatch.total_items || 0}</div>
            <div><strong>批次状态：</strong>{selectedBatch.status || '-'}</div>
            <div><strong>更新时间：</strong>{formatDateTime(selectedBatch.updated_at)}</div>
          </div>
        )}

        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 12, flexWrap: 'wrap' }}>
          <button
            type="button"
            onClick={handlePreflight}
            disabled={readOnly || preflightLoading || !selectedBatchId}
            style={{
              padding: '6px 14px',
              borderRadius: 6,
              border: '1px solid #cbd5e1',
              background: '#fff',
              color: '#0f172a',
              cursor: readOnly ? 'not-allowed' : 'pointer',
            }}
          >
            {preflightLoading ? '预检中...' : '运行预检'}
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={readOnly || submitting || !selectedBatchId}
            style={{
              padding: '6px 14px',
              borderRadius: 6,
              border: 'none',
              background: readOnly ? '#cbd5e1' : '#2563eb',
              color: '#fff',
              cursor: readOnly ? 'not-allowed' : 'pointer',
            }}
          >
            {submitting ? '提交中...' : '提交时序运行（SBAS）'}
          </button>
          {message && (
            <span style={{ fontSize: 12, color: message.includes('失败') ? '#dc2626' : '#166534' }}>
              {message}
            </span>
          )}
        </div>

        {preflightReport && (
          <div
            style={{
              marginTop: 12,
              padding: '10px 12px',
              borderRadius: 6,
              border: `1px solid ${preflightOk ? '#bbf7d0' : '#fecaca'}`,
              background: preflightOk ? '#f0fdf4' : '#fef2f2',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginBottom: 8, flexWrap: 'wrap' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <strong style={{ fontSize: 13, color: preflightOk ? '#166534' : '#991b1b' }}>
                  {preflightOk ? '预检通过' : '预检发现问题'}
                </strong>
                <QualityBadge ok={preflightOk} okLabel="可提交" failLabel="需处理" />
              </div>
              <div style={{ fontSize: 11, color: '#475569' }}>
                错误 {preflightErrors.length} / 告警 {preflightWarnings.length}
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 8, marginBottom: 8 }}>
              <div style={{ padding: '8px 10px', borderRadius: 6, background: '#fff', border: '1px solid #e2e8f0', fontSize: 12 }}>
                <div style={{ color: '#64748b', marginBottom: 4 }}>有效参考日期</div>
                <strong>{preflightReport.reference_date_effective || preflightReport.reference_date || '-'}</strong>
              </div>
              <div style={{ padding: '8px 10px', borderRadius: 6, background: '#fff', border: '1px solid #e2e8f0', fontSize: 12 }}>
                <div style={{ color: '#64748b', marginBottom: 4 }}>场景规模</div>
                <strong>{preflightSummary.scene_count || 0} 景</strong>
              </div>
              <div style={{ padding: '8px 10px', borderRadius: 6, background: '#fff', border: '1px solid #e2e8f0', fontSize: 12 }}>
                <div style={{ color: '#64748b', marginBottom: 4 }}>Network edges</div>
                <strong>{preflightReport.network_edge_count ?? preflightSummary.network_edge_count ?? 0}</strong>
              </div>
              <div style={{ padding: '8px 10px', borderRadius: 6, background: '#fff', border: '1px solid #e2e8f0', fontSize: 12 }}>
                <div style={{ color: '#64748b', marginBottom: 4 }}>Stack Key</div>
                <strong style={{ wordBreak: 'break-all' }}>{preflightSummary.stack_key || '-'}</strong>
              </div>
              <div style={{ padding: '8px 10px', borderRadius: 6, background: '#fff', border: '1px solid #e2e8f0', fontSize: 12 }}>
                <div style={{ color: '#64748b', marginBottom: 4 }}>数据量</div>
                <strong>{formatBytes(preflightSummary.total_scene_bytes)}</strong>
              </div>
            </div>

            <div style={{ fontSize: 12, color: '#334155', lineHeight: 1.7 }}>
              <div><strong>Stack Plan:</strong>{preflightReport.plan_id || preflightSummary.plan_id || '-'}</div>
              <div><strong>Plan Strategy:</strong>{preflightReport.plan_strategy || preflightSummary.plan_strategy || '-'}</div>
              <div><strong>批次：</strong>{preflightReport.batch_name || preflightReport.batch_id || '-'}</div>
              <div><strong>批次状态：</strong>{preflightReport.batch_status || '-'}</div>
              <div><strong>Processor:</strong>{preflightReport.processor_manifest?.processor_code || processorCode || '-'}</div>
              <div><strong>水体掩膜：</strong>{preflightReport.water_mask_mode || '-'}</div>
              <div><strong>分组：</strong>{preflightSummary.group_key || '-'}</div>
              <div><strong>源目录：</strong>{preflightSummary.source_root_windows || '-'}</div>
              <div><strong>日期列表：</strong>{(preflightSummary.stack_dates || []).join(', ') || '-'}</div>
            </div>

            {isSarscapePreflight && (
              <div style={{ marginTop: 8, padding: '8px 10px', borderRadius: 6, background: '#fff', border: '1px solid #bfdbfe', fontSize: 12, color: '#1e3a8a', lineHeight: 1.6 }}>
                当前预检针对候选批次/候选图。提交 run 后，prepare 步骤会冻结一个 prepared SBAS stack；SARscape 后续只读取这个 prepared manifest，不再重新访问全量数据池。
              </div>
            )}

            {preflightErrors.length > 0 && (
              <div style={{ marginTop: 8, padding: '8px 10px', borderRadius: 6, background: '#fff', border: '1px solid #fecaca', fontSize: 12, color: '#991b1b' }}>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>错误</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  {preflightErrors.map((item, index) => (
                    <div key={`preflight-error-${index}`}>{item}</div>
                  ))}
                </div>
              </div>
            )}

            {preflightWarnings.length > 0 && (
              <div style={{ marginTop: 8, padding: '8px 10px', borderRadius: 6, background: '#fff', border: '1px solid #fde68a', fontSize: 12, color: '#92400e' }}>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>告警</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  {preflightWarnings.map((item, index) => (
                    <div key={`preflight-warning-${index}`}>{item}</div>
                  ))}
                </div>
              </div>
            )}

            {preflightChecks.length > 0 && (
              <div style={{ marginTop: 8 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: '#0f172a', marginBottom: 6 }}>检查项</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {preflightChecks.map(check => {
                    const accent = check.ok ? '#166534' : check.severity === 'warn' ? '#92400e' : '#991b1b';
                    return (
                      <div
                        key={check.name}
                        style={{
                          padding: '8px 10px',
                          borderRadius: 6,
                          border: '1px solid #e2e8f0',
                          background: '#fff',
                          fontSize: 12,
                          color: '#334155',
                        }}
                      >
                        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                          <strong style={{ color: accent }}>
                            {check.ok ? 'OK' : check.severity === 'warn' ? 'WARN' : 'FAIL'} / {check.name}
                          </strong>
                          {check.skipped ? <span style={{ color: '#64748b' }}>skipped</span> : null}
                        </div>
                        <div style={{ marginTop: 2, wordBreak: 'break-word' }}>{check.detail || '-'}</div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      <div style={card}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
          <strong style={{ fontSize: 14 }}>最近运行</strong>
          <button
            type="button"
            onClick={loadRuns}
            disabled={loading}
            style={{ padding: '4px 10px', borderRadius: 6, border: '1px solid #cbd5e1', background: '#fff', cursor: 'pointer' }}
          >
            {loading ? '刷新中...' : '刷新'}
          </button>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 12 }}>
          <div style={{ border: '1px solid #e2e8f0', borderRadius: 6, overflow: 'hidden' }}>
            <div style={{ padding: '8px 10px', background: '#f8fafc', fontSize: 12, fontWeight: 600 }}>
              运行列表 ({runs.length})
            </div>
            {runs.length === 0 ? (
              <div style={{ padding: '12px', fontSize: 12, color: '#94a3b8' }}>当前暂无时序InSAR运行记录。</div>
            ) : (
              <div style={{ maxHeight: 420, overflowY: 'auto' }}>
                {runs.map(item => (
                  <button
                    key={item.run_id}
                    type="button"
                    onClick={() => setSelectedRunId(item.run_id)}
                    style={{
                      display: 'block',
                      width: '100%',
                      textAlign: 'left',
                      border: 'none',
                      borderTop: '1px solid #f1f5f9',
                      background: selectedRunId === item.run_id ? '#eff6ff' : '#fff',
                      padding: '10px 12px',
                      cursor: 'pointer',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, marginBottom: 4 }}>
                      <strong style={{ fontSize: 12, color: '#0f172a', wordBreak: 'break-all' }}>
                        {item.run_name || item.run_id}
                      </strong>
                      <span style={{ fontSize: 11, color: STATUS_COLOR[item.status] || '#64748b' }}>
                        {item.status}
                      </span>
                    </div>
                    <div style={{ fontSize: 11, color: '#64748b' }}>
                      {item.processor_code || '-'} /
                      {item.reference_date || '-'} / {item.stack_size || 0} 景 / {formatDateTime(item.created_at)}
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>

          <div style={{ border: '1px solid #e2e8f0', borderRadius: 6, overflow: 'hidden' }}>
            <div style={{ padding: '8px 10px', background: '#f8fafc', fontSize: 12, fontWeight: 600 }}>运行详情</div>
            {!selectedRunId ? (
              <div style={{ padding: '12px', fontSize: 12, color: '#94a3b8' }}>请选择一个运行查看详情。</div>
            ) : detailLoading ? (
              <div style={{ padding: '12px', fontSize: 12, color: '#94a3b8' }}>正在加载详情...</div>
            ) : selectedRunDetail?.error ? (
              <div style={{ padding: '12px', fontSize: 12, color: '#dc2626' }}>{selectedRunDetail.error}</div>
            ) : (
              <div style={{ padding: '12px', display: 'flex', flexDirection: 'column', gap: 8, fontSize: 12, color: '#334155' }}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <strong>{runData?.run_name || '-'}</strong>
                  <StatusPill value={runData?.status} />
                </div>
                <div><strong>Stack Plan:</strong>{runData?.plan_id || '-'}</div>
                <div><strong>Plan Strategy:</strong>{runData?.plan_strategy || '-'}</div>
                <div><strong>Processor:</strong>{runData?.processor_code || '-'} / {runData?.engine_code || '-'}</div>
                <div><strong>运行标识：</strong>{runData?.run_id || '-'}</div>
                <div><strong>批次标识：</strong>{runData?.batch_id || '-'}</div>
                <div><strong>参考日期：</strong>{runData?.reference_date || '-'}</div>
                <div><strong>方向：</strong>{runData?.direction || '-'}</div>
                <div><strong>影像数：</strong>{runData?.stack_size || 0}</div>
                <div><strong>水体掩膜策略：</strong>{runData?.water_mask_mode || '-'}</div>
                <div><strong>环境：</strong>{runData?.env_name || '-'} / {runData?.wsl_distro || '-'}</div>
                <div><strong>DEM：</strong>{runData?.dem_path_windows || '-'}</div>
                <div><strong>轨道池：</strong>{runData?.orbit_pool_windows || '-'}</div>
                <div><strong>工作目录：</strong>{runData?.work_root_windows || '-'}</div>
                <div><strong>发布目录：</strong>{runData?.publish_dir_windows || '-'}</div>
                <div><strong>stack manifest：</strong>{runData?.manifest_path_windows || '-'}</div>
                <div><strong>创建时间：</strong>{formatDateTime(runData?.created_at)}</div>
                <div><strong>结束时间：</strong>{formatDateTime(runData?.ended_at)}</div>
                <div><strong>输入日期：</strong>{(runData?.input_snapshot_json?.stack_dates || []).join(', ') || '-'}</div>
                {showPreparedStack && (
                  <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px dashed #cbd5e1' }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
                      <strong>Prepared SBAS Stack</strong>
                      {preparedStackSummary?.error ? (
                        <StateBadge value="manifest_unreadable" />
                      ) : (
                        <StateBadge value={preparedStack?.state || (runData?.summary_json?.prepared_stack_id ? 'prepared' : 'not_prepared')} />
                      )}
                    </div>
                    {preparedStackSummary?.error ? (
                      <div style={{ padding: '8px 10px', borderRadius: 6, background: '#fef2f2', border: '1px solid #fecaca', color: '#991b1b' }}>
                        {preparedStackSummary.error}
                      </div>
                    ) : (
                      <>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 8 }}>
                          <div style={{ padding: '8px 10px', borderRadius: 6, background: '#f8fafc', border: '1px solid #e2e8f0' }}>
                            <div style={{ color: '#64748b', marginBottom: 4 }}>Prepared ID</div>
                            <strong style={{ wordBreak: 'break-all' }}>{preparedStack?.prepared_stack_id || runData?.summary_json?.prepared_stack_id || '-'}</strong>
                          </div>
                          <div style={{ padding: '8px 10px', borderRadius: 6, background: '#f8fafc', border: '1px solid #e2e8f0' }}>
                            <div style={{ color: '#64748b', marginBottom: 4 }}>Validation</div>
                            <QualityBadge ok={!!(preparedValidation?.ok ?? preparedStack?.prepared)} okLabel="OK" failLabel="Blocked" />
                          </div>
                          <div style={{ padding: '8px 10px', borderRadius: 6, background: '#f8fafc', border: '1px solid #e2e8f0' }}>
                            <div style={{ color: '#64748b', marginBottom: 4 }}>Scenes</div>
                            <strong>{preparedStack?.scene_count ?? runData?.stack_size ?? 0}</strong>
                          </div>
                          <div style={{ padding: '8px 10px', borderRadius: 6, background: '#f8fafc', border: '1px solid #e2e8f0' }}>
                            <div style={{ color: '#64748b', marginBottom: 4 }}>Network edges</div>
                            <strong>{preparedStack?.network_edge_count ?? runData?.input_snapshot_json?.network_edge_count ?? 0}</strong>
                          </div>
                        </div>
                        <div style={{ marginTop: 8, lineHeight: 1.7 }}>
                          <div><strong>Schema:</strong>{preparedStack?.prepared_stack_schema || runData?.summary_json?.prepared_stack_schema || '-'}</div>
                          <div><strong>Source plan:</strong>{preparedStack?.source_plan_id || runData?.plan_id || '-'}</div>
                          <div><strong>Source batch:</strong>{preparedStack?.source_batch_id || runData?.batch_id || '-'}</div>
                          <div><strong>Prepared manifest:</strong>{preparedStack?.manifest_path_windows || runData?.manifest_path_windows || '-'}</div>
                          <div><strong>Selected network edges:</strong>{preparedStack?.selected_network_edges_path_windows || runData?.input_snapshot_json?.selected_network_edges_path_windows || '-'}</div>
                          <div><strong>Policy:</strong>{preparedStack?.production_contract?.input_policy || '-'} / catalog_scan_after_prepare={String(preparedStack?.production_contract?.catalog_scan_allowed_after_prepare ?? false)}</div>
                        </div>
                        {processorManifest && (
                          <div style={{ marginTop: 8, padding: '8px 10px', borderRadius: 6, background: processorManifest.ready_for_execution ? '#f0fdf4' : '#fffbeb', border: `1px solid ${processorManifest.ready_for_execution ? '#bbf7d0' : '#fde68a'}` }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                              <strong>SARscape processor</strong>
                              <QualityBadge ok={!!processorManifest.ready_for_execution} okLabel="Executable" failLabel="Blocked" />
                            </div>
                            <div style={{ marginTop: 4 }}><strong>Strategy:</strong>{processorManifest.execution_strategy || '-'}</div>
                            <div><strong>Template:</strong>{processorManifest.parameter_template?.validated ? 'validated' : 'not validated'}</div>
                            <div><strong>Execution enabled:</strong>{String(!!processorManifest.execution_enabled)}</div>
                          </div>
                        )}
                        {(preparedBlockers.length > 0 || processorBlockers.length > 0) && (
                          <div style={{ marginTop: 8, padding: '8px 10px', borderRadius: 6, background: '#fff7ed', border: '1px solid #fed7aa', color: '#9a3412' }}>
                            <div style={{ fontWeight: 600, marginBottom: 4 }}>Blockers</div>
                            {[...preparedBlockers, ...processorBlockers].map((item, index) => (
                              <div key={`prepared-blocker-${index}`}>{item}</div>
                            ))}
                          </div>
                        )}
                        {preparedWarnings.length > 0 && (
                          <div style={{ marginTop: 8, padding: '8px 10px', borderRadius: 6, background: '#fffbeb', border: '1px solid #fde68a', color: '#92400e' }}>
                            <div style={{ fontWeight: 600, marginBottom: 4 }}>Warnings</div>
                            {preparedWarnings.map((item, index) => (
                              <div key={`prepared-warning-${index}`}>{item}</div>
                            ))}
                          </div>
                        )}
                      </>
                    )}
                  </div>
                )}
                <div>
                  <strong>轨道摘要：</strong>
                  <div style={{ marginTop: 4 }}>
                    <JsonBlock value={runData?.orbit_summary_json || {}} />
                  </div>
                </div>
                {(runPreflightQuality || runPublishValidation) && (
                  <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px dashed #cbd5e1' }}>
                    <div style={{ fontWeight: 600, marginBottom: 6 }}>运行质量</div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 8 }}>
                      {runPreflightQuality && (
                        <div style={{ padding: '8px 10px', borderRadius: 6, background: '#f8fafc', border: '1px solid #e2e8f0' }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center', marginBottom: 6 }}>
                            <strong>预检记录</strong>
                            <QualityBadge ok={!!runPreflightQuality.overall_ok} okLabel="通过" failLabel="失败" />
                          </div>
                          <div><strong>有效参考日期：</strong>{runPreflightQuality.reference_date_effective || '-'}</div>
                          <div><strong>错误数：</strong>{(runPreflightQuality.errors || []).length}</div>
                          <div><strong>告警数：</strong>{(runPreflightQuality.warnings || []).length}</div>
                          <details style={{ marginTop: 8 }}>
                            <summary style={{ cursor: 'pointer', color: '#2563eb' }}>查看预检详情</summary>
                            <div style={{ marginTop: 8 }}>
                              <JsonBlock value={runPreflightQuality} />
                            </div>
                          </details>
                        </div>
                      )}
                      {runPublishValidation && (
                        <div style={{ padding: '8px 10px', borderRadius: 6, background: '#f8fafc', border: '1px solid #e2e8f0' }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center', marginBottom: 6 }}>
                            <strong>发布校验</strong>
                            <QualityBadge ok={!!runPublishValidation.ok} okLabel="通过" failLabel="失败" />
                          </div>
                          <div><strong>主资产角色：</strong>{runPublishValidation.primary_role || '-'}</div>
                          <div><strong>预览角色：</strong>{runPublishValidation.preview_role || '-'}</div>
                          <div><strong>缺失角色：</strong>{(runPublishValidation.missing_roles || []).length}</div>
                          <div><strong>缺失文件：</strong>{(runPublishValidation.missing_paths || []).length}</div>
                          <details style={{ marginTop: 8 }}>
                            <summary style={{ cursor: 'pointer', color: '#2563eb' }}>查看发布校验详情</summary>
                            <div style={{ marginTop: 8 }}>
                              <JsonBlock value={runPublishValidation} />
                            </div>
                          </details>
                        </div>
                      )}
                    </div>
                  </div>
                )}
                {workflowSteps.length > 0 && (
                  <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px dashed #cbd5e1' }}>
                    <div style={{ fontWeight: 600, marginBottom: 6 }}>工作流步骤</div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                      {workflowSteps.map(step => (
                        <div
                          key={step.step_id}
                          style={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            gap: 8,
                            padding: '6px 8px',
                            borderRadius: 6,
                            background: '#f8fafc',
                            border: '1px solid #e2e8f0',
                          }}
                        >
                          <div style={{ minWidth: 0 }}>
                            <div style={{ fontWeight: 600, color: '#0f172a' }}>
                              {step.step_name || step.step_id}
                            </div>
                            <div style={{ color: '#64748b', wordBreak: 'break-all' }}>
                              {step.step_id}
                            </div>
                            {step.error && (
                              <div style={{ color: '#dc2626', marginTop: 2, wordBreak: 'break-word' }}>
                                {step.error}
                              </div>
                            )}
                          </div>
                          <div style={{ flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 6, alignItems: 'flex-end' }}>
                            <StatusPill value={step.status} />
                            {!readOnly && step.status === 'FAILED' && (
                              <button
                                type="button"
                                onClick={() => handleRetryStep(step.step_id)}
                                disabled={retryingStepId === step.step_id}
                                style={{
                                  padding: '4px 8px',
                                  borderRadius: 6,
                                  border: '1px solid #cbd5e1',
                                  background: '#fff',
                                  cursor: 'pointer',
                                  fontSize: 11,
                                }}
                              >
                                {retryingStepId === step.step_id ? '重试中...' : '重试该步'}
                              </button>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                {linkedProduct && (
                  <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px dashed #cbd5e1' }}>
                    <div><strong>关联产品：</strong>{linkedProduct.display_name || linkedProduct.product_id}</div>
                    <div><strong>产品状态：</strong>{linkedProduct.status || '-'} / {linkedProduct.health_status || '-'}</div>
                    <div><strong>发布时间：</strong>{formatDateTime(linkedProduct.published_at)}</div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
