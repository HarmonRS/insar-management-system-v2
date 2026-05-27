import React, { useCallback, useEffect, useMemo, useState } from 'react';

import {
  auditSbasInsarStack,
  decideSbasInsarItab,
  discoverSbasInsarStacks,
  getSbasInsarCapabilities,
  getSbasInsarRun,
  getSbasInsarRunArtifactUrl,
  listSbasInsarRuns,
  prepareSbasInsarCoregistration,
  prepareSbasInsarInterferograms,
  prepareSbasInsarIptaTimeseries,
  prepareSbasInsarRdcDem,
  prepareSbasInsarWorkflow,
  runSbasInsarBaselineAudit,
  submitSbasInsarCoregistrationJob,
  submitSbasInsarInterferogramsJob,
  submitSbasInsarIptaTimeseriesJob,
  submitSbasInsarRdcDemJob,
  submitSbasInsarRun,
  submitSbasInsarWorkflowJob,
} from './api/sbasInsarProduction';

const shellStyle = {
  display: 'grid',
  gap: 12,
};

const sectionStyle = {
  background: '#ffffff',
  border: '1px solid #d8dee8',
  borderRadius: 8,
  padding: 14,
};

const mutedStyle = {
  color: '#64748b',
  fontSize: 12,
  lineHeight: 1.55,
};

const labelStyle = {
  color: '#475569',
  fontSize: 12,
};

const valueStyle = {
  color: '#0f172a',
  fontSize: 14,
  fontWeight: 650,
};

const gridStyle = {
  display: 'grid',
  gridTemplateColumns: 'minmax(280px, 360px) minmax(0, 1fr)',
  gap: 12,
  alignItems: 'start',
};

const metricGridStyle = {
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
  gap: 10,
};

const buttonBaseStyle = {
  width: '100%',
  textAlign: 'left',
  border: '1px solid #d8dee8',
  borderRadius: 8,
  background: '#ffffff',
  padding: '10px 12px',
  cursor: 'pointer',
};

function formatValue(value, suffix = '') {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '-';
  return `${numeric.toFixed(Math.abs(numeric) >= 100 ? 1 : 2)}${suffix}`;
}

function formatBytes(value) {
  const size = Number(value || 0);
  if (!Number.isFinite(size) || size <= 0) return '-';
  if (size < 1024) return `${size} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let current = size / 1024;
  let index = 0;
  while (current >= 1024 && index < units.length - 1) {
    current /= 1024;
    index += 1;
  }
  return `${current.toFixed(current >= 100 ? 0 : 1)} ${units[index]}`;
}

function StatusBadge({ value }) {
  const okValues = new Set([
    'READY',
    'READY_FOR_GAMMA_BASELINE_AUDIT',
    'WORKFLOW_READY',
    'WORKFLOW_COMPLETED',
    'COMPLETED',
    'IPTA_TIMESERIES_READY',
  ]);
  const isOk = okValues.has(value);
  const color = isOk ? '#0f766e' : '#92400e';
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '2px 8px',
        borderRadius: 999,
        color,
        background: isOk ? '#ccfbf1' : '#fef3c7',
        fontSize: 12,
        fontWeight: 700,
      }}
    >
      <span style={{ width: 7, height: 7, borderRadius: 999, background: color }} />
      {value || 'UNKNOWN'}
    </span>
  );
}

function Metric({ label, value }) {
  return (
    <div
      style={{
        border: '1px solid #e2e8f0',
        borderRadius: 8,
        padding: '9px 10px',
        background: '#f8fafc',
        minHeight: 58,
      }}
    >
      <div style={labelStyle}>{label}</div>
      <div style={{ ...valueStyle, marginTop: 4 }}>{value}</div>
    </div>
  );
}

function RunArtifactLink({ runId, artifact }) {
  const href = getSbasInsarRunArtifactUrl(runId, artifact.relative_path);
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      style={{ color: '#1d4ed8', fontWeight: 650, textDecoration: 'none' }}
    >
      打开
    </a>
  );
}

export default function SbasInsarProductionPanel({ readOnly = false }) {
  const [capabilities, setCapabilities] = useState(null);
  const [runs, setRuns] = useState([]);
  const [selectedRunId, setSelectedRunId] = useState('');
  const [runDetail, setRunDetail] = useState(null);
  const [loading, setLoading] = useState(false);
  const [runDetailLoading, setRunDetailLoading] = useState(false);
  const [error, setError] = useState('');
  const [discovering, setDiscovering] = useState(false);
  const [stackCandidates, setStackCandidates] = useState([]);
  const [selectedStackId, setSelectedStackId] = useState('');
  const [auditLoading, setAuditLoading] = useState(false);
  const [stackAudit, setStackAudit] = useState(null);
  const [submitLoading, setSubmitLoading] = useState(false);
  const [baselineAuditLoading, setBaselineAuditLoading] = useState(false);
  const [itabDecisionLoading, setItabDecisionLoading] = useState(false);
  const [coregistrationLoading, setCoregistrationLoading] = useState(false);
  const [coregistrationJobLoading, setCoregistrationJobLoading] = useState(false);
  const [coregistrationJob, setCoregistrationJob] = useState(null);
  const [rdcDemLoading, setRdcDemLoading] = useState(false);
  const [rdcDemJobLoading, setRdcDemJobLoading] = useState(false);
  const [rdcDemJob, setRdcDemJob] = useState(null);
  const [interferogramLoading, setInterferogramLoading] = useState(false);
  const [interferogramJobLoading, setInterferogramJobLoading] = useState(false);
  const [interferogramJob, setInterferogramJob] = useState(null);
  const [iptaTimeseriesLoading, setIptaTimeseriesLoading] = useState(false);
  const [iptaTimeseriesJobLoading, setIptaTimeseriesJobLoading] = useState(false);
  const [iptaTimeseriesJob, setIptaTimeseriesJob] = useState(null);
  const [workflowLoading, setWorkflowLoading] = useState(false);
  const [workflowJobLoading, setWorkflowJobLoading] = useState(false);
  const [workflowJob, setWorkflowJob] = useState(null);

  const loadProductionRuns = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [capabilityData, runData] = await Promise.all([
        getSbasInsarCapabilities(),
        listSbasInsarRuns(),
      ]);
      const runItems = Array.isArray(runData?.items) ? runData.items : [];
      setCapabilities(capabilityData);
      setRuns(runItems);
      setSelectedRunId(current => current || runItems[0]?.run_id || '');
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR 列表加载失败');
      setCapabilities(null);
      setRuns([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadProductionRuns();
  }, [loadProductionRuns]);

  const loadRunDetail = useCallback(async runId => {
    if (!runId) {
      setRunDetail(null);
      return;
    }
    setRunDetailLoading(true);
    setError('');
    try {
      const data = await getSbasInsarRun(runId);
      setRunDetail(data);
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR 生产 Run 详情加载失败');
      setRunDetail(null);
    } finally {
      setRunDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    loadRunDetail(selectedRunId);
  }, [loadRunDetail, selectedRunId]);

  const handleDiscoverStacks = useCallback(async () => {
    setDiscovering(true);
    setError('');
    setStackAudit(null);
    try {
      const data = await discoverSbasInsarStacks({
        min_scenes: 3,
        require_orbits: true,
        include_scenes: false,
        limit: 30,
      });
      const items = Array.isArray(data?.items) ? data.items : [];
      setStackCandidates(items);
      setSelectedStackId(current => current || items[0]?.stack_id || '');
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR 栈发现失败');
      setStackCandidates([]);
    } finally {
      setDiscovering(false);
    }
  }, []);

  const handleAuditStack = useCallback(async stackId => {
    if (!stackId) return;
    setAuditLoading(true);
    setError('');
    try {
      const data = await auditSbasInsarStack(stackId, {
        min_scenes: 3,
        require_orbits: true,
      });
      setStackAudit(data);
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR 栈审查失败');
      setStackAudit(null);
    } finally {
      setAuditLoading(false);
    }
  }, []);

  const handleSubmitRun = useCallback(async () => {
    if (!selectedStackId || readOnly) return;
    setSubmitLoading(true);
    setError('');
    try {
      const candidate = stackCandidates.find(item => item.stack_id === selectedStackId);
      const data = await submitSbasInsarRun(selectedStackId, {
        run_label: candidate
          ? `${candidate.satellite || 'LT1'} ${candidate.relative_orbit || ''} ${candidate.center_bucket || ''}`.trim()
          : undefined,
        min_scenes: 3,
        require_orbits: true,
        dry_run: false,
        monitor_point_strategy: 'auto_low_sigma_high_rate',
      });
      const runId = data?.run?.run_id;
      const runData = await listSbasInsarRuns();
      const runItems = Array.isArray(runData?.items) ? runData.items : [];
      setRuns(runItems);
      if (runId) {
        setSelectedRunId(runId);
        setRunDetail(data);
      }
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR 生产 Run 创建失败');
    } finally {
      setSubmitLoading(false);
    }
  }, [readOnly, selectedStackId, stackCandidates]);

  const workflowPayload = useMemo(() => ({
    force: false,
    rlks: 8,
    azlks: 8,
    reference_window: 16,
    mb_mode: 0,
    timeout_seconds: 172800,
  }), []);

  const handlePrepareWorkflow = useCallback(async () => {
    if (!selectedRunId || readOnly) return;
    setWorkflowLoading(true);
    setError('');
    try {
      const data = await prepareSbasInsarWorkflow(selectedRunId, workflowPayload);
      setRunDetail(data);
      const runData = await listSbasInsarRuns();
      setRuns(Array.isArray(runData?.items) ? runData.items : []);
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'Gamma SBAS workflow 生成失败');
    } finally {
      setWorkflowLoading(false);
    }
  }, [readOnly, selectedRunId, workflowPayload]);

  const handleSubmitWorkflowJob = useCallback(async () => {
    if (!selectedRunId || readOnly) return;
    setWorkflowJobLoading(true);
    setError('');
    try {
      const data = await submitSbasInsarWorkflowJob(selectedRunId, workflowPayload);
      setWorkflowJob(data);
      const detailData = await getSbasInsarRun(selectedRunId);
      setRunDetail(detailData);
      const runData = await listSbasInsarRuns();
      setRuns(Array.isArray(runData?.items) ? runData.items : []);
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'Gamma SBAS workflow 任务提交失败');
      setWorkflowJob(null);
    } finally {
      setWorkflowJobLoading(false);
    }
  }, [readOnly, selectedRunId, workflowPayload]);

  const handleBaselineAudit = useCallback(async (execute = false) => {
    if (!selectedRunId || readOnly) return;
    setBaselineAuditLoading(true);
    setError('');
    try {
      const data = await runSbasInsarBaselineAudit(selectedRunId, {
        execute,
        rlks: 8,
        azlks: 8,
        max_delta_n: 1,
        timeout_seconds: 21600,
      });
      setRunDetail(data);
      const runData = await listSbasInsarRuns();
      setRuns(Array.isArray(runData?.items) ? runData.items : []);
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR baseline audit 失败');
    } finally {
      setBaselineAuditLoading(false);
    }
  }, [readOnly, selectedRunId]);

  const handleItabDecision = useCallback(async decision => {
    if (!selectedRunId || readOnly) return;
    setItabDecisionLoading(true);
    setError('');
    try {
      const data = await decideSbasInsarItab(selectedRunId, {
        decision,
        reviewer: 'ui',
        note: decision === 'approve'
          ? 'Baseline-audited adjacent itab accepted from SBAS production page.'
          : 'Baseline-audited adjacent itab rejected from SBAS production page.',
      });
      setRunDetail(data);
      const runData = await listSbasInsarRuns();
      setRuns(Array.isArray(runData?.items) ? runData.items : []);
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR itab 审批失败');
    } finally {
      setItabDecisionLoading(false);
    }
  }, [readOnly, selectedRunId]);

  const handlePrepareCoregistration = useCallback(async () => {
    if (!selectedRunId || readOnly) return;
    setCoregistrationLoading(true);
    setError('');
    try {
      const data = await prepareSbasInsarCoregistration(selectedRunId, {
        execute: false,
        rlks: 8,
        azlks: 8,
      });
      setRunDetail(data);
      const runData = await listSbasInsarRuns();
      setRuns(Array.isArray(runData?.items) ? runData.items : []);
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR 共参考配准计划生成失败');
    } finally {
      setCoregistrationLoading(false);
    }
  }, [readOnly, selectedRunId]);

  const handleSubmitCoregistrationJob = useCallback(async () => {
    if (!selectedRunId || readOnly) return;
    setCoregistrationJobLoading(true);
    setError('');
    try {
      const data = await submitSbasInsarCoregistrationJob(selectedRunId, {
        rlks: 8,
        azlks: 8,
        timeout_seconds: 43200,
      });
      setCoregistrationJob(data);
      const detailData = await getSbasInsarRun(selectedRunId);
      setRunDetail(detailData);
      const runData = await listSbasInsarRuns();
      setRuns(Array.isArray(runData?.items) ? runData.items : []);
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR 共参考配准任务提交失败');
      setCoregistrationJob(null);
    } finally {
      setCoregistrationJobLoading(false);
    }
  }, [readOnly, selectedRunId]);

  const handlePrepareRdcDem = useCallback(async () => {
    if (!selectedRunId || readOnly) return;
    setRdcDemLoading(true);
    setError('');
    try {
      const data = await prepareSbasInsarRdcDem(selectedRunId, {
        execute: false,
        rlks: 8,
      });
      setRunDetail(data);
      const runData = await listSbasInsarRuns();
      setRuns(Array.isArray(runData?.items) ? runData.items : []);
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR RDC DEM 计划生成失败');
    } finally {
      setRdcDemLoading(false);
    }
  }, [readOnly, selectedRunId]);

  const handleSubmitRdcDemJob = useCallback(async () => {
    if (!selectedRunId || readOnly) return;
    setRdcDemJobLoading(true);
    setError('');
    try {
      const data = await submitSbasInsarRdcDemJob(selectedRunId, {
        rlks: 8,
        timeout_seconds: 43200,
      });
      setRdcDemJob(data);
      const detailData = await getSbasInsarRun(selectedRunId);
      setRunDetail(detailData);
      const runData = await listSbasInsarRuns();
      setRuns(Array.isArray(runData?.items) ? runData.items : []);
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR RDC DEM 任务提交失败');
      setRdcDemJob(null);
    } finally {
      setRdcDemJobLoading(false);
    }
  }, [readOnly, selectedRunId]);

  const handlePrepareInterferograms = useCallback(async () => {
    if (!selectedRunId || readOnly) return;
    setInterferogramLoading(true);
    setError('');
    try {
      const data = await prepareSbasInsarInterferograms(selectedRunId, {
        execute: false,
        rlks: 8,
        azlks: 8,
        unwrap_threshold: 0.2,
      });
      setRunDetail(data);
      const runData = await listSbasInsarRuns();
      setRuns(Array.isArray(runData?.items) ? runData.items : []);
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR interferogram 计划生成失败');
    } finally {
      setInterferogramLoading(false);
    }
  }, [readOnly, selectedRunId]);

  const handleSubmitInterferogramsJob = useCallback(async () => {
    if (!selectedRunId || readOnly) return;
    setInterferogramJobLoading(true);
    setError('');
    try {
      const data = await submitSbasInsarInterferogramsJob(selectedRunId, {
        rlks: 8,
        azlks: 8,
        unwrap_threshold: 0.2,
        timeout_seconds: 43200,
      });
      setInterferogramJob(data);
      const detailData = await getSbasInsarRun(selectedRunId);
      setRunDetail(detailData);
      const runData = await listSbasInsarRuns();
      setRuns(Array.isArray(runData?.items) ? runData.items : []);
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR interferogram 任务提交失败');
      setInterferogramJob(null);
    } finally {
      setInterferogramJobLoading(false);
    }
  }, [readOnly, selectedRunId]);

  const handlePrepareIptaTimeseries = useCallback(async () => {
    if (!selectedRunId || readOnly) return;
    setIptaTimeseriesLoading(true);
    setError('');
    try {
      const data = await prepareSbasInsarIptaTimeseries(selectedRunId, {
        execute: false,
        rlks: 8,
        reference_window: 16,
      });
      setRunDetail(data);
      const runData = await listSbasInsarRuns();
      setRuns(Array.isArray(runData?.items) ? runData.items : []);
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR IPTA timeseries 计划生成失败');
    } finally {
      setIptaTimeseriesLoading(false);
    }
  }, [readOnly, selectedRunId]);

  const handleSubmitIptaTimeseriesJob = useCallback(async () => {
    if (!selectedRunId || readOnly) return;
    setIptaTimeseriesJobLoading(true);
    setError('');
    try {
      const data = await submitSbasInsarIptaTimeseriesJob(selectedRunId, {
        rlks: 8,
        reference_window: 16,
        timeout_seconds: 43200,
      });
      setIptaTimeseriesJob(data);
      const detailData = await getSbasInsarRun(selectedRunId);
      setRunDetail(detailData);
      const runData = await listSbasInsarRuns();
      setRuns(Array.isArray(runData?.items) ? runData.items : []);
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR IPTA timeseries 任务提交失败');
      setIptaTimeseriesJob(null);
    } finally {
      setIptaTimeseriesJobLoading(false);
    }
  }, [readOnly, selectedRunId]);

  const selectedStack = stackCandidates.find(item => item.stack_id === selectedStackId) || null;
  const run = runDetail?.run || null;
  const runManifest = runDetail?.manifest || {};
  const workflowManifest = runDetail?.workflow_manifest || {};
  const workflowState = runDetail?.workflow_state || {};
  const workflowSteps = Array.isArray(workflowManifest.steps) ? workflowManifest.steps : [];
  const expertDocumentSteps = Array.isArray(workflowManifest.expert_document?.steps)
    ? workflowManifest.expert_document.steps
    : [];
  const workflowStepState = workflowState.steps || {};
  const stagePlan = runDetail?.command_manifest?.stage_plan || [];
  const runArtifacts = runDetail?.artifacts || [];
  const baselineSummary = runManifest.baseline_audit?.summary || null;
  const itabDecision = runManifest.baseline_audit?.itab_decision || null;
  const coregistrationPlan = runManifest.coregistration || null;
  const rdcDemPlan = runManifest.rdc_dem || null;
  const interferogramPlan = runManifest.interferograms || null;
  const detrendAtmPlan = runManifest.detrend_atm || null;
  const iptaTimeseriesPlan = runManifest.ipta_timeseries || null;
  const publishProductsPlan = runManifest.publish_products || null;
  const monitorProductsPlan = runManifest.monitor_point_products || null;
  const runPrimaryPreview = (
    runArtifacts.find(item => item.key === 'los_rate_toward_m_per_year_hls_geo_preview_png')
    || runArtifacts.find(item => item.key === 'los_rate_toward_mm_per_year_geo_preview_png')
    || runArtifacts.find(item => item.key === 'los_rate_toward_mm_per_year_bmp')
  );
  const runSigmaPreview = (
    runArtifacts.find(item => item.key === 'los_sigma_m_per_year_cc_geo_preview_png')
    || runArtifacts.find(item => item.key === 'los_sigma_mm_per_year_geo_preview_png')
    || runArtifacts.find(item => item.key === 'los_sigma_mm_per_year_bmp')
  );
  const runMonitorPreview = runArtifacts.find(item => item.role === 'monitor_point' && item.relative_path.endsWith('.png'));
  const runMonitorCsv = runArtifacts.find(item => item.role === 'monitor_point' && item.relative_path.endsWith('.csv'));
  const itabApproved = itabDecision?.decision === 'approve' || runManifest.baseline_audit?.approved_for_next_stage === true;
  const itabRejected = itabDecision?.decision === 'reject';

  return (
    <div style={shellStyle}>
      <section style={sectionStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start' }}>
          <div>
            <h2 style={{ margin: 0, fontSize: 20, color: '#0f172a' }}>SBAS-InSAR 生产</h2>
            <div style={{ ...mutedStyle, marginTop: 6 }}>
              Gamma IPTA SBAS 生产入口。当前阶段接入已验证的 LT1/Gamma 试验成果，作业提交在下一阶段开放。
            </div>
          </div>
          <button
            type="button"
            onClick={loadProductionRuns}
            disabled={loading}
            style={{
              border: '1px solid #0f766e',
              borderRadius: 8,
              background: '#f0fdfa',
              color: '#0f766e',
              padding: '8px 12px',
              fontWeight: 700,
              cursor: loading ? 'default' : 'pointer',
              whiteSpace: 'nowrap',
            }}
          >
            {loading ? '刷新中' : '刷新'}
          </button>
        </div>
        {capabilities && (
          <div style={{ ...metricGridStyle, marginTop: 12 }}>
            <Metric label="处理器" value={capabilities.processor_code || '-'} />
            <Metric label="引擎" value={capabilities.engine_code || '-'} />
            <Metric label="阶段" value={capabilities.implementation_state || '-'} />
            <Metric label="默认 LOS" value={capabilities.default_los_convention?.description || '-'} />
          </div>
        )}
        {readOnly && (
          <div style={{ ...mutedStyle, marginTop: 10 }}>
            当前账号只读，生产提交按钮不会显示。
          </div>
        )}
        {error && (
          <div
            style={{
              marginTop: 10,
              padding: '8px 10px',
              borderRadius: 8,
              border: '1px solid #fecaca',
              background: '#fef2f2',
              color: '#991b1b',
              fontSize: 13,
            }}
          >
            {error}
          </div>
        )}
      </section>

      <section style={sectionStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start' }}>
          <div>
            <h3 style={{ margin: 0, fontSize: 15, color: '#0f172a' }}>候选 SBAS 序列发现</h3>
            <div style={{ ...mutedStyle, marginTop: 5 }}>
              直接扫描本地 LT1 数据池，按平台、相对轨道、升降轨、模式、极化、接收站和中心桶硬分组，并检查精轨 TXT。
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              type="button"
              onClick={handleDiscoverStacks}
              disabled={discovering}
              style={{
                border: '1px solid #0f766e',
                borderRadius: 8,
                background: '#f0fdfa',
                color: '#0f766e',
                padding: '8px 12px',
                fontWeight: 700,
                cursor: discovering ? 'default' : 'pointer',
                whiteSpace: 'nowrap',
              }}
            >
              {discovering ? '发现中' : '发现序列'}
            </button>
            <button
              type="button"
              onClick={() => handleAuditStack(selectedStackId)}
              disabled={!selectedStackId || auditLoading}
              style={{
                border: '1px solid #1d4ed8',
                borderRadius: 8,
                background: selectedStackId ? '#eff6ff' : '#f8fafc',
                color: selectedStackId ? '#1d4ed8' : '#94a3b8',
                padding: '8px 12px',
                fontWeight: 700,
                cursor: selectedStackId && !auditLoading ? 'pointer' : 'default',
                whiteSpace: 'nowrap',
              }}
            >
              {auditLoading ? '审查中' : '生成 Manifest'}
            </button>
            {!readOnly && (
              <button
                type="button"
                onClick={handleSubmitRun}
                disabled={!selectedStackId || submitLoading}
                style={{
                  border: '1px solid #7c3aed',
                  borderRadius: 8,
                  background: selectedStackId ? '#f5f3ff' : '#f8fafc',
                  color: selectedStackId ? '#6d28d9' : '#94a3b8',
                  padding: '8px 12px',
                  fontWeight: 700,
                  cursor: selectedStackId && !submitLoading ? 'pointer' : 'default',
                  whiteSpace: 'nowrap',
                }}
              >
                {submitLoading ? '创建中' : '创建计划 Run'}
              </button>
            )}
          </div>
        </div>

        {stackCandidates.length > 0 && (
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(260px, 420px) minmax(0, 1fr)', gap: 12, marginTop: 12 }}>
            <div style={{ display: 'grid', gap: 8, maxHeight: 360, overflow: 'auto' }}>
              {stackCandidates.map(item => {
                const active = item.stack_id === selectedStackId;
                return (
                  <button
                    key={item.stack_id}
                    type="button"
                    onClick={() => setSelectedStackId(item.stack_id)}
                    style={{
                      ...buttonBaseStyle,
                      borderColor: active ? '#1d4ed8' : '#d8dee8',
                      background: active ? '#eff6ff' : '#ffffff',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                      <strong style={{ color: '#0f172a', fontSize: 13 }}>
                        {item.satellite} / relOrbit {item.relative_orbit} / {item.center_bucket}
                      </strong>
                      <StatusBadge value={item.status} />
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 6 }}>
                      {item.date_start} 至 {item.date_end}，可用 {item.usable_scene_count}/{item.scene_count} 景，
                      缺精轨 {item.missing_orbit_count}，最大间隔 {item.max_temporal_gap_days} 天
                    </div>
                  </button>
                );
              })}
            </div>
            <div style={{ display: 'grid', gap: 10 }}>
              {selectedStack && (
                <div style={metricGridStyle}>
                  <Metric label="平台/模式" value={`${selectedStack.satellite || '-'} / ${selectedStack.imaging_mode || '-'}`} />
                  <Metric label="轨道方向" value={selectedStack.orbit_direction || '-'} />
                  <Metric label="极化/接收站" value={`${selectedStack.polarization || '-'} / ${selectedStack.receiving_station || '-'}`} />
                  <Metric label="建议参考日期" value={selectedStack.reference_date || '-'} />
                </div>
              )}
              {stackAudit && (
                <div style={{ border: '1px solid #dbeafe', borderRadius: 8, padding: 10, background: '#eff6ff' }}>
                  <div style={valueStyle}>Manifest 已生成</div>
                  <div style={{ ...mutedStyle, marginTop: 6, wordBreak: 'break-all' }}>
                    {stackAudit.manifest_path}
                  </div>
                  <div style={{ ...mutedStyle, marginTop: 6 }}>
                    状态：{stackAudit.status}；pair 数：
                    {stackAudit.manifest?.pair_network?.pairs?.length || 0}
                  </div>
                  {(stackAudit.manifest?.warnings || []).length > 0 && (
                    <div style={{ ...mutedStyle, marginTop: 6 }}>
                      警告：{stackAudit.manifest.warnings.join('；')}
                    </div>
                  )}
                  {(stackAudit.manifest?.blockers || []).length > 0 && (
                    <div style={{ ...mutedStyle, marginTop: 6, color: '#991b1b' }}>
                      阻断：{stackAudit.manifest.blockers.join('；')}
                    </div>
                  )}
                </div>
              )}
              {run && (
                <div style={{ border: '1px solid #ddd6fe', borderRadius: 8, padding: 10, background: '#f5f3ff' }}>
                  <div style={valueStyle}>计划 Run 已创建</div>
                  <div style={{ ...mutedStyle, marginTop: 6 }}>
                    {run.run_id}；状态：{run.status}；下一步：{run.next_stage || '-'}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </section>

      <section style={sectionStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start' }}>
          <div>
            <h3 style={{ margin: 0, fontSize: 15, color: '#0f172a' }}>生产 Run 计划</h3>
            <div style={{ ...mutedStyle, marginTop: 5 }}>
              当前只创建可复现实验记录、Gamma 命令计划和监测点配置；正式执行在 baseline 审核后接入。
            </div>
          </div>
          <span style={mutedStyle}>{runs.length} 个</span>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(260px, 420px) minmax(0, 1fr)', gap: 12, marginTop: 12 }}>
          <div style={{ display: 'grid', gap: 8, maxHeight: 300, overflow: 'auto' }}>
            {runs.map(item => {
              const active = item.run_id === selectedRunId;
              return (
                <button
                  key={item.run_id}
                  type="button"
                  onClick={() => setSelectedRunId(item.run_id)}
                  style={{
                    ...buttonBaseStyle,
                    borderColor: active ? '#7c3aed' : '#d8dee8',
                    background: active ? '#f5f3ff' : '#ffffff',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                    <strong style={{ color: '#0f172a', fontSize: 13 }}>
                      {item.platform || 'LT1'} / relOrbit {item.relative_orbit || '-'} / {item.center_bucket || '-'}
                    </strong>
                    <StatusBadge value={item.status} />
                  </div>
                  <div style={{ ...mutedStyle, marginTop: 6 }}>
                    {item.scene_count || 0} 景，{item.pair_count || 0} 对，下一步 {item.next_stage || '-'}
                  </div>
                </button>
              );
            })}
            {!loading && runs.length === 0 && (
              <div style={{ ...mutedStyle, padding: '10px 0' }}>
                暂无计划 Run。先发现序列，再创建计划 Run。
              </div>
            )}
          </div>

          <div style={{ display: 'grid', gap: 10 }}>
            {runDetailLoading && <div style={mutedStyle}>正在加载 Run 详情...</div>}
            {!runDetailLoading && run && (
              <>
                <div style={metricGridStyle}>
                  <Metric label="状态" value={run.status || '-'} />
                  <Metric label="参考日期" value={run.reference_date || '-'} />
                  <Metric label="场景/配对" value={`${run.scene_count || 0} / ${run.pair_count || 0}`} />
                  <Metric label="下一阶段" value={run.next_stage || '-'} />
                </div>

                {!readOnly && (
                  <div style={{ border: '1px solid #bbf7d0', borderRadius: 8, padding: 10, background: '#f0fdf4' }}>
                    <div style={valueStyle}>Gamma SBAS Workflow</div>
                    <div style={{ ...mutedStyle, marginTop: 6 }}>
                      专家文档目录 + manifest + WSL runner 主路径。旧分阶段执行仅作为兼容桥接。
                    </div>
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 10 }}>
                      <button
                        type="button"
                        onClick={handlePrepareWorkflow}
                        disabled={workflowLoading}
                        style={{
                          border: '1px solid #15803d',
                          borderRadius: 8,
                          background: '#dcfce7',
                          color: '#166534',
                          padding: '8px 12px',
                          fontWeight: 700,
                          cursor: workflowLoading ? 'default' : 'pointer',
                        }}
                      >
                        {workflowLoading ? '生成中' : '生成 Workflow Manifest'}
                      </button>
                      <button
                        type="button"
                        onClick={handleSubmitWorkflowJob}
                        disabled={workflowJobLoading}
                        style={{
                          border: '1px solid #0f766e',
                          borderRadius: 8,
                          background: '#ccfbf1',
                          color: '#0f766e',
                          padding: '8px 12px',
                          fontWeight: 700,
                          cursor: workflowJobLoading ? 'default' : 'pointer',
                        }}
                      >
                        {workflowJobLoading ? '提交中' : '提交 Gamma SBAS Workflow'}
                      </button>
                    </div>
                    {workflowJob && workflowJob.run_id === run.run_id && (
                      <div style={{ ...mutedStyle, marginTop: 8 }}>
                        已提交后台任务：{workflowJob.task_id}；Job：{workflowJob.job_id}
                      </div>
                    )}
                    {workflowSteps.length > 0 && (
                      <div style={{ display: 'grid', gap: 6, marginTop: 10 }}>
                        {workflowSteps.map(step => {
                          const state = workflowStepState[step.id] || {};
                          return (
                            <div
                              key={step.id}
                              style={{
                                display: 'grid',
                                gridTemplateColumns: 'minmax(190px, 1fr) minmax(120px, auto)',
                                gap: 8,
                                alignItems: 'center',
                                border: '1px solid #bbf7d0',
                                borderRadius: 8,
                                padding: '8px 10px',
                                background: '#ffffff',
                              }}
                            >
                              <div>
                                <div style={{ color: '#0f172a', fontSize: 13, fontWeight: 650 }}>
                                  {step.id} · {step.name}
                                </div>
                                <div style={mutedStyle}>
                                  {(step.expert_tools || []).join(', ') || 'planned'}；{step.enabled ? 'enabled' : 'planned'}
                                </div>
                              </div>
                              <StatusBadge value={state.status || step.status || 'PENDING'} />
                            </div>
                          );
                        })}
                      </div>
                    )}
                    {expertDocumentSteps.length > 0 && (
                      <div style={{ marginTop: 12 }}>
                        <div style={valueStyle}>Expert document path</div>
                        <div style={{ ...mutedStyle, marginTop: 4 }}>
                          {expertDocumentSteps.length} sections from the LT1 Gamma SBAS expert document. Commands are shown as the acceptance checklist; implementation may be a bridge where the verified experiment already covers the same Gamma function.
                        </div>
                        <div style={{ display: 'grid', gap: 6, marginTop: 8 }}>
                          {expertDocumentSteps.map(item => {
                            const mappedStatuses = (item.mapped_workflow_steps || [])
                              .map(mapped => {
                                const state = workflowStepState[mapped.id] || {};
                                return state.status || mapped.status;
                              })
                              .filter(Boolean);
                            const displayStatus = mappedStatuses[0] || item.implementation_status || 'planned';
                            const commandPreview = (item.commands || []).slice(0, 3).join(' | ');
                            return (
                              <div
                                key={item.id}
                                style={{
                                  display: 'grid',
                                  gridTemplateColumns: 'minmax(210px, 1fr) minmax(120px, auto)',
                                  gap: 8,
                                  alignItems: 'start',
                                  border: '1px solid #e2e8f0',
                                  borderRadius: 8,
                                  padding: '8px 10px',
                                  background: item.enabled ? '#ffffff' : '#f8fafc',
                                }}
                              >
                                <div>
                                  <div style={{ color: '#0f172a', fontSize: 13, fontWeight: 650 }}>
                                    {item.order}. {item.title}
                                  </div>
                                  <div style={mutedStyle}>
                                    maps to {(item.workflow_steps || []).join(', ') || '-'}; {item.command_count || 0} commands; {item.implementation_status}
                                  </div>
                                  {commandPreview && (
                                    <div style={{ ...mutedStyle, marginTop: 3, fontFamily: 'monospace', overflowWrap: 'anywhere' }}>
                                      {commandPreview}
                                    </div>
                                  )}
                                </div>
                                <StatusBadge value={displayStatus} />
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {!readOnly && false && (
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    <button
                      type="button"
                      onClick={() => handleBaselineAudit(false)}
                      disabled={baselineAuditLoading}
                      style={{
                        border: '1px solid #1d4ed8',
                        borderRadius: 8,
                        background: '#eff6ff',
                        color: '#1d4ed8',
                        padding: '8px 12px',
                        fontWeight: 700,
                        cursor: baselineAuditLoading ? 'default' : 'pointer',
                      }}
                    >
                      {baselineAuditLoading ? '处理中' : '生成/解析 baseline audit'}
                    </button>
                    <button
                      type="button"
                      onClick={() => handleBaselineAudit(true)}
                      disabled={baselineAuditLoading}
                      style={{
                        border: '1px solid #7c3aed',
                        borderRadius: 8,
                        background: '#f5f3ff',
                        color: '#6d28d9',
                        padding: '8px 12px',
                        fontWeight: 700,
                        cursor: baselineAuditLoading ? 'default' : 'pointer',
                      }}
                    >
                      执行 Gamma baseline audit
                    </button>
                    <button
                      type="button"
                      onClick={handlePrepareCoregistration}
                      disabled={coregistrationLoading}
                      style={{
                        border: '1px solid #0f766e',
                        borderRadius: 8,
                        background: '#f0fdfa',
                        color: '#0f766e',
                        padding: '8px 12px',
                        fontWeight: 700,
                        cursor: coregistrationLoading ? 'default' : 'pointer',
                      }}
                    >
                      {coregistrationLoading ? '生成中' : '生成共参考配准脚本'}
                    </button>
                    <button
                      type="button"
                      onClick={handleSubmitCoregistrationJob}
                      disabled={coregistrationJobLoading}
                      style={{
                        border: '1px solid #b45309',
                        borderRadius: 8,
                        background: '#fffbeb',
                        color: '#92400e',
                        padding: '8px 12px',
                        fontWeight: 700,
                        cursor: coregistrationJobLoading ? 'default' : 'pointer',
                      }}
                    >
                      {coregistrationJobLoading ? '提交中' : '提交共参考配准任务'}
                    </button>
                    <button
                      type="button"
                      onClick={handlePrepareRdcDem}
                      disabled={rdcDemLoading}
                      style={{
                        border: '1px solid #0369a1',
                        borderRadius: 8,
                        background: '#f0f9ff',
                        color: '#0369a1',
                        padding: '8px 12px',
                        fontWeight: 700,
                        cursor: rdcDemLoading ? 'default' : 'pointer',
                      }}
                    >
                      {rdcDemLoading ? '生成中' : '生成 RDC DEM 脚本'}
                    </button>
                    <button
                      type="button"
                      onClick={handleSubmitRdcDemJob}
                      disabled={rdcDemJobLoading}
                      style={{
                        border: '1px solid #4338ca',
                        borderRadius: 8,
                        background: '#eef2ff',
                        color: '#3730a3',
                        padding: '8px 12px',
                        fontWeight: 700,
                        cursor: rdcDemJobLoading ? 'default' : 'pointer',
                      }}
                    >
                      {rdcDemJobLoading ? '提交中' : '提交 RDC DEM 任务'}
                    </button>
                    <button
                      type="button"
                      onClick={handlePrepareInterferograms}
                      disabled={interferogramLoading}
                      style={{
                        border: '1px solid #7c2d12',
                        borderRadius: 8,
                        background: '#fff7ed',
                        color: '#7c2d12',
                        padding: '8px 12px',
                        fontWeight: 700,
                        cursor: interferogramLoading ? 'default' : 'pointer',
                      }}
                    >
                      {interferogramLoading ? '生成中' : '生成干涉图脚本'}
                    </button>
                    <button
                      type="button"
                      onClick={handleSubmitInterferogramsJob}
                      disabled={interferogramJobLoading}
                      style={{
                        border: '1px solid #be123c',
                        borderRadius: 8,
                        background: '#fff1f2',
                        color: '#be123c',
                        padding: '8px 12px',
                        fontWeight: 700,
                        cursor: interferogramJobLoading ? 'default' : 'pointer',
                      }}
                    >
                      {interferogramJobLoading ? '提交中' : '提交干涉图任务'}
                    </button>
                    <button
                      type="button"
                      onClick={handlePrepareIptaTimeseries}
                      disabled={iptaTimeseriesLoading}
                      style={{
                        border: '1px solid #166534',
                        borderRadius: 8,
                        background: '#f0fdf4',
                        color: '#166534',
                        padding: '8px 12px',
                        fontWeight: 700,
                        cursor: iptaTimeseriesLoading ? 'default' : 'pointer',
                      }}
                    >
                      {iptaTimeseriesLoading ? '生成中' : '生成 IPTA 脚本'}
                    </button>
                    <button
                      type="button"
                      onClick={handleSubmitIptaTimeseriesJob}
                      disabled={iptaTimeseriesJobLoading}
                      style={{
                        border: '1px solid #15803d',
                        borderRadius: 8,
                        background: '#dcfce7',
                        color: '#166534',
                        padding: '8px 12px',
                        fontWeight: 700,
                        cursor: iptaTimeseriesJobLoading ? 'default' : 'pointer',
                      }}
                    >
                      {iptaTimeseriesJobLoading ? '提交中' : '提交 IPTA 任务'}
                    </button>
                  </div>
                )}

                <div style={{ border: '1px solid #e2e8f0', borderRadius: 8, padding: 10 }}>
                  <div style={valueStyle}>Gamma 阶段计划</div>
                  <div style={{ display: 'grid', gap: 6, marginTop: 8 }}>
                    {stagePlan.map(stage => (
                      <div
                        key={stage.stage_id}
                        style={{
                          display: 'grid',
                          gridTemplateColumns: 'minmax(160px, 1fr) minmax(120px, auto)',
                          gap: 8,
                          alignItems: 'center',
                          padding: '7px 8px',
                          border: '1px solid #f1f5f9',
                          borderRadius: 8,
                          background: '#f8fafc',
                        }}
                      >
                        <div>
                          <div style={{ color: '#0f172a', fontSize: 13, fontWeight: 650 }}>{stage.label}</div>
                          <div style={mutedStyle}>{(stage.gamma_tools || []).join(', ') || '应用内产品处理'}</div>
                        </div>
                        <StatusBadge value={stage.status} />
                      </div>
                    ))}
                  </div>
                </div>

                {baselineSummary && (
                  <div style={{ border: '1px solid #dbeafe', borderRadius: 8, padding: 10, background: '#eff6ff' }}>
                    <div style={valueStyle}>Baseline Audit 结果</div>
                    <div style={{ ...metricGridStyle, marginTop: 8 }}>
                      <Metric label="全部配对" value={`${baselineSummary.all_pair_count || 0}`} />
                      <Metric label="相邻配对" value={`${baselineSummary.adjacent_pair_count || 0}`} />
                      <Metric label="最大 |Bperp|" value={formatValue(baselineSummary.max_abs_bperp_m, ' m')} />
                      <Metric label="最大时间间隔" value={formatValue(baselineSummary.max_delta_days, ' d')} />
                    </div>
                    <div
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        gap: 10,
                        flexWrap: 'wrap',
                        alignItems: 'center',
                        marginTop: 10,
                      }}
                    >
                      <div style={mutedStyle}>
                        itab 状态：
                        {itabDecision
                          ? `${itabDecision.decision} / ${itabDecision.decided_at || '-'}`
                          : '等待审批'}
                      </div>
                      {!readOnly && (
                        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                          <button
                            type="button"
                            onClick={() => handleItabDecision('approve')}
                            disabled={itabDecisionLoading || itabApproved}
                            style={{
                              border: '1px solid #0f766e',
                              borderRadius: 8,
                              background: itabApproved ? '#f8fafc' : '#f0fdfa',
                              color: itabApproved ? '#94a3b8' : '#0f766e',
                              padding: '7px 11px',
                              fontWeight: 700,
                              cursor: itabDecisionLoading || itabApproved ? 'default' : 'pointer',
                            }}
                          >
                            {itabApproved ? 'itab 已批准' : '批准 itab'}
                          </button>
                          <button
                            type="button"
                            onClick={() => handleItabDecision('reject')}
                            disabled={itabDecisionLoading || itabApproved || itabRejected}
                            style={{
                              border: '1px solid #b91c1c',
                              borderRadius: 8,
                              background: itabApproved || itabRejected ? '#f8fafc' : '#fef2f2',
                              color: itabApproved || itabRejected ? '#94a3b8' : '#b91c1c',
                              padding: '7px 11px',
                              fontWeight: 700,
                              cursor: itabDecisionLoading || itabApproved || itabRejected ? 'default' : 'pointer',
                            }}
                          >
                            {itabRejected ? 'itab 已拒绝' : '拒绝 itab'}
                          </button>
                        </div>
                      )}
                    </div>
                    <div style={{ overflowX: 'auto', marginTop: 10 }}>
                      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                        <thead>
                          <tr style={{ color: '#475569' }}>
                            <th style={{ textAlign: 'left', padding: '6px 8px', borderBottom: '1px solid #bfdbfe' }}>Pair</th>
                            <th style={{ textAlign: 'left', padding: '6px 8px', borderBottom: '1px solid #bfdbfe' }}>日期</th>
                            <th style={{ textAlign: 'right', padding: '6px 8px', borderBottom: '1px solid #bfdbfe' }}>Bperp</th>
                            <th style={{ textAlign: 'right', padding: '6px 8px', borderBottom: '1px solid #bfdbfe' }}>间隔</th>
                          </tr>
                        </thead>
                        <tbody>
                          {(baselineSummary.adjacent_pairs || []).map(pair => (
                            <tr key={`${pair.master_date}_${pair.slave_date}`}>
                              <td style={{ padding: '6px 8px', borderBottom: '1px solid #dbeafe' }}>{pair.pair_index}</td>
                              <td style={{ padding: '6px 8px', borderBottom: '1px solid #dbeafe' }}>
                                {pair.master_date}{' -> '}{pair.slave_date}
                              </td>
                              <td style={{ padding: '6px 8px', borderBottom: '1px solid #dbeafe', textAlign: 'right' }}>
                                {formatValue(pair.bperp_m, ' m')}
                              </td>
                              <td style={{ padding: '6px 8px', borderBottom: '1px solid #dbeafe', textAlign: 'right' }}>
                                {formatValue(pair.delta_days, ' d')}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {coregistrationPlan && (
                  <div style={{ border: '1px solid #ccfbf1', borderRadius: 8, padding: 10, background: '#f0fdfa' }}>
                    <div style={valueStyle}>共参考配准计划</div>
                    <div style={{ ...metricGridStyle, marginTop: 8 }}>
                      <Metric label="参考日期" value={coregistrationPlan.reference_date || '-'} />
                      <Metric label="场景数" value={`${coregistrationPlan.scene_count || 0}`} />
                      <Metric label="批准配对" value={`${coregistrationPlan.approved_pair_count || 0}`} />
                      <Metric label="多视参数" value={`${coregistrationPlan.rlks || '-'} / ${coregistrationPlan.azlks || '-'}`} />
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 8, wordBreak: 'break-all' }}>
                      脚本：{coregistrationPlan.script_path || '-'}
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 5, wordBreak: 'break-all' }}>
                      输出目录：{coregistrationPlan.outputs?.common_dir || '-'}
                    </div>
                    {coregistrationPlan.execution && (
                      <div style={{ ...mutedStyle, marginTop: 5 }}>
                        执行状态：{coregistrationPlan.execution.status || '-'}；
                        {coregistrationPlan.execution.ended_at || coregistrationPlan.execution.started_at || '-'}
                      </div>
                    )}
                    {coregistrationPlan.summary && (
                      <div style={{ ...mutedStyle, marginTop: 5 }}>
                        RSLC 就绪：
                        {coregistrationPlan.summary.ready_secondary_count || 0}/
                        {coregistrationPlan.summary.expected_secondary_count || 0}；
                        缺失日期：{(coregistrationPlan.summary.missing_dates || []).join(', ') || '无'}
                      </div>
                    )}
                    {coregistrationJob && coregistrationJob.run_id === run.run_id && (
                      <div
                        style={{
                          marginTop: 8,
                          border: '1px solid #fde68a',
                          borderRadius: 8,
                          background: '#fffbeb',
                          color: '#92400e',
                          padding: '7px 9px',
                          fontSize: 12,
                          lineHeight: 1.5,
                          wordBreak: 'break-all',
                        }}
                      >
                        已提交后台任务：{coregistrationJob.task_id}；Job：{coregistrationJob.job_id}
                      </div>
                    )}
                  </div>
                )}

                <div style={{ border: '1px solid #e2e8f0', borderRadius: 8, padding: 10 }}>
                  <div style={valueStyle}>监测点配置</div>
                  <div style={{ ...mutedStyle, marginTop: 6 }}>
                    模式：{runDetail.monitor_points?.mode || '-'}；
                    点数：{runDetail.monitor_points?.points?.length || 0}
                  </div>
                  <div style={{ ...mutedStyle, marginTop: 6 }}>
                    {runDetail.monitor_points?.note || '等待产品生成后提取时序曲线。'}
                  </div>
                </div>

                {rdcDemPlan && (
                  <div style={{ border: '1px solid #bfdbfe', borderRadius: 8, padding: 10, background: '#eff6ff' }}>
                    <div style={valueStyle}>RDC DEM Plan</div>
                    <div style={{ ...metricGridStyle, marginTop: 8 }}>
                      <Metric label="Reference date" value={rdcDemPlan.reference_date || '-'} />
                      <Metric label="RLKS" value={`${rdcDemPlan.rlks || '-'}`} />
                      <Metric
                        label="DEM covers center"
                        value={rdcDemPlan.dem_source?.covers_stack_center === false ? 'No' : 'Yes'}
                      />
                      <Metric
                        label="DEM covers bbox"
                        value={rdcDemPlan.dem_source?.covers_stack_bbox ? 'Yes' : 'Partial'}
                      />
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 8, wordBreak: 'break-all' }}>
                      Script: {rdcDemPlan.script_path || '-'}
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 5, wordBreak: 'break-all' }}>
                      DEM source: {rdcDemPlan.dem_source?.windows_path || rdcDemPlan.dem_source?.wsl_path || '-'}
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 5, wordBreak: 'break-all' }}>
                      Output: {rdcDemPlan.outputs?.rdc_dem || '-'}
                    </div>
                    {rdcDemPlan.execution && (
                      <div style={{ ...mutedStyle, marginTop: 5 }}>
                        Execution: {rdcDemPlan.execution.status || '-'};{' '}
                        {rdcDemPlan.execution.ended_at || rdcDemPlan.execution.started_at || '-'}
                      </div>
                    )}
                    {rdcDemPlan.summary && (
                      <div style={{ ...mutedStyle, marginTop: 5 }}>
                        Ready: {rdcDemPlan.summary.ready ? 'Yes' : 'No'}; missing:{' '}
                        {(rdcDemPlan.summary.missing_outputs || []).join(', ') || 'none'}
                      </div>
                    )}
                    {rdcDemJob && rdcDemJob.run_id === run.run_id && (
                      <div
                        style={{
                          marginTop: 8,
                          border: '1px solid #c7d2fe',
                          borderRadius: 8,
                          background: '#eef2ff',
                          color: '#3730a3',
                          padding: '7px 9px',
                          fontSize: 12,
                          lineHeight: 1.5,
                          wordBreak: 'break-all',
                        }}
                      >
                        Queued task: {rdcDemJob.task_id}; Job: {rdcDemJob.job_id}
                      </div>
                    )}
                  </div>
                )}

                {interferogramPlan && (
                  <div style={{ border: '1px solid #fed7aa', borderRadius: 8, padding: 10, background: '#fff7ed' }}>
                    <div style={valueStyle}>Interferogram Plan</div>
                    <div style={{ ...metricGridStyle, marginTop: 8 }}>
                      <Metric label="Reference date" value={interferogramPlan.reference_date || '-'} />
                      <Metric label="Pairs" value={`${interferogramPlan.pair_count || 0}`} />
                      <Metric label="Looks" value={`${interferogramPlan.rlks || '-'} / ${interferogramPlan.azlks || '-'}`} />
                      <Metric label="Threshold" value={`${interferogramPlan.unwrap_threshold ?? '-'}`} />
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 8, wordBreak: 'break-all' }}>
                      Script: {interferogramPlan.script_path || '-'}
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 5, wordBreak: 'break-all' }}>
                      DIFF_tab: {interferogramPlan.outputs?.diff_tab || '-'}
                    </div>
                    {interferogramPlan.execution && (
                      <div style={{ ...mutedStyle, marginTop: 5 }}>
                        Execution: {interferogramPlan.execution.status || '-'};{' '}
                        {interferogramPlan.execution.ended_at || interferogramPlan.execution.started_at || '-'}
                      </div>
                    )}
                    {interferogramPlan.summary && (
                      <div style={{ ...mutedStyle, marginTop: 5 }}>
                        Ready pairs: {interferogramPlan.summary.ready_pair_count || 0}/
                        {interferogramPlan.summary.pair_count || 0}; missing:{' '}
                        {(interferogramPlan.summary.missing_pairs || []).join(', ') || 'none'}
                      </div>
                    )}
                    {interferogramJob && interferogramJob.run_id === run.run_id && (
                      <div
                        style={{
                          marginTop: 8,
                          border: '1px solid #fecdd3',
                          borderRadius: 8,
                          background: '#fff1f2',
                          color: '#be123c',
                          padding: '7px 9px',
                          fontSize: 12,
                          lineHeight: 1.5,
                          wordBreak: 'break-all',
                        }}
                      >
                        Queued task: {interferogramJob.task_id}; Job: {interferogramJob.job_id}
                      </div>
                    )}
                  </div>
                )}

                {detrendAtmPlan && (
                  <div style={{ border: '1px solid #fed7aa', borderRadius: 8, padding: 10, background: '#fff7ed' }}>
                    <div style={valueStyle}>Detrend / ATM Plan</div>
                    <div style={{ ...metricGridStyle, marginTop: 8 }}>
                      <Metric label="Reference date" value={detrendAtmPlan.reference_date || '-'} />
                      <Metric label="Pairs" value={`${detrendAtmPlan.summary?.ready_pair_count || 0}/${detrendAtmPlan.pair_count || detrendAtmPlan.summary?.pair_count || 0}`} />
                      <Metric label="CC min" value={`${detrendAtmPlan.coherence_min ?? '-'}`} />
                      <Metric label="Ready" value={detrendAtmPlan.summary?.ready ? 'Yes' : '-'} />
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 8, wordBreak: 'break-all' }}>
                      Script: {detrendAtmPlan.script_path || '-'}
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 5, wordBreak: 'break-all' }}>
                      DIFF_atmsub_tab: {detrendAtmPlan.outputs?.diff_atmsub_tab || detrendAtmPlan.summary?.outputs?.diff_atmsub_tab?.path || '-'}
                    </div>
                    {detrendAtmPlan.execution && (
                      <div style={{ ...mutedStyle, marginTop: 5 }}>
                        Execution: {detrendAtmPlan.execution.status || '-'};{' '}
                        {detrendAtmPlan.execution.ended_at || detrendAtmPlan.execution.started_at || '-'}
                      </div>
                    )}
                    {detrendAtmPlan.summary && (
                      <div style={{ ...mutedStyle, marginTop: 5 }}>
                        Missing pairs: {(detrendAtmPlan.summary.missing_pairs || []).join(', ') || 'none'}; rows:{' '}
                        {detrendAtmPlan.summary.diff_atmsub_tab_row_count || 0}/
                        {detrendAtmPlan.summary.itab_atmsub_row_count || 0}
                      </div>
                    )}
                  </div>
                )}

                {iptaTimeseriesPlan && (
                  <div style={{ border: '1px solid #bbf7d0', borderRadius: 8, padding: 10, background: '#f0fdf4' }}>
                    <div style={valueStyle}>IPTA Time-Series Plan</div>
                    <div style={{ ...metricGridStyle, marginTop: 8 }}>
                      <Metric label="Reference date" value={iptaTimeseriesPlan.reference_date || '-'} />
                      <Metric label="RLKS" value={`${iptaTimeseriesPlan.rlks || '-'}`} />
                      <Metric label="Window" value={`${iptaTimeseriesPlan.reference_window || '-'}`} />
                      <Metric label="Ready" value={iptaTimeseriesPlan.summary?.ready ? 'Yes' : '-'} />
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 8, wordBreak: 'break-all' }}>
                      Script: {iptaTimeseriesPlan.script_path || '-'}
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 5, wordBreak: 'break-all' }}>
                      ts_rate: {iptaTimeseriesPlan.outputs?.ts_rate || iptaTimeseriesPlan.summary?.outputs?.ts_rate?.path || '-'}
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 5, wordBreak: 'break-all' }}>
                      sigma_rate: {iptaTimeseriesPlan.outputs?.sigma_rate || iptaTimeseriesPlan.summary?.outputs?.sigma_rate?.path || '-'}
                    </div>
                    {iptaTimeseriesPlan.execution && (
                      <div style={{ ...mutedStyle, marginTop: 5 }}>
                        Execution: {iptaTimeseriesPlan.execution.status || '-'};{' '}
                        {iptaTimeseriesPlan.execution.ended_at || iptaTimeseriesPlan.execution.started_at || '-'}
                      </div>
                    )}
                    {iptaTimeseriesPlan.summary && (
                      <div style={{ ...mutedStyle, marginTop: 5 }}>
                        Missing: {(iptaTimeseriesPlan.summary.missing_outputs || []).join(', ') || 'none'}; rows:{' '}
                        {iptaTimeseriesPlan.summary.diff_ts_row_count || 0}/
                        {iptaTimeseriesPlan.summary.itab_ts_row_count || 0}
                      </div>
                    )}
                    {iptaTimeseriesJob && iptaTimeseriesJob.run_id === run.run_id && (
                      <div
                        style={{
                          marginTop: 8,
                          border: '1px solid #bbf7d0',
                          borderRadius: 8,
                          background: '#dcfce7',
                          color: '#166534',
                          padding: '7px 9px',
                          fontSize: 12,
                          lineHeight: 1.5,
                          wordBreak: 'break-all',
                        }}
                      >
                        Queued task: {iptaTimeseriesJob.task_id}; Job: {iptaTimeseriesJob.job_id}
                      </div>
                    )}
                  </div>
                )}

                {publishProductsPlan && (
                  <div style={{ border: '1px solid #bae6fd', borderRadius: 8, padding: 10, background: '#f0f9ff' }}>
                    <div style={valueStyle}>Publish Products</div>
                    <div style={{ ...metricGridStyle, marginTop: 8 }}>
                      <Metric label="Reference date" value={publishProductsPlan.reference_date || '-'} />
                      <Metric label="RLKS" value={`${publishProductsPlan.rlks || '-'}`} />
                      <Metric label="Ready" value={publishProductsPlan.summary?.ready ? 'Yes' : '-'} />
                      <Metric label="LOS sign" value="Toward positive" />
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 8, wordBreak: 'break-all' }}>
                      Script: {publishProductsPlan.script_path || '-'}
                    </div>
                    {publishProductsPlan.summary && (
                      <div style={{ ...mutedStyle, marginTop: 5 }}>
                        Missing: {(publishProductsPlan.summary.missing_outputs || []).join(', ') || 'none'}
                      </div>
                    )}
                  </div>
                )}

                {monitorProductsPlan && (
                  <div style={{ border: '1px solid #ddd6fe', borderRadius: 8, padding: 10, background: '#f5f3ff' }}>
                    <div style={valueStyle}>Monitoring Point Products</div>
                    <div style={{ ...metricGridStyle, marginTop: 8 }}>
                      <Metric label="Reference date" value={monitorProductsPlan.reference_date || '-'} />
                      <Metric label="Dates" value={`${monitorProductsPlan.dates?.length || 0}`} />
                      <Metric label="Ready" value={monitorProductsPlan.summary?.ready ? 'Yes' : '-'} />
                      <Metric label="Mode" value={runDetail.monitor_points?.mode || '-'} />
                    </div>
                    {monitorProductsPlan.summary?.monitor_outputs?.length > 0 && (
                      <div style={{ ...mutedStyle, marginTop: 5 }}>
                        Points: {monitorProductsPlan.summary.monitor_outputs.map(item => item.point_id).join(', ')}
                      </div>
                    )}
                  </div>
                )}

                {(runPrimaryPreview || runSigmaPreview || runMonitorPreview) && (
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 12 }}>
                    {runPrimaryPreview && (
                      <div>
                        <div style={{ ...valueStyle, marginBottom: 6 }}>LOS Velocity</div>
                        <div style={{ ...mutedStyle, marginBottom: 6 }}>
                          {runPrimaryPreview.key.endsWith('_geo_preview_png') ? 'WGS84 geocoded preview' : 'RDC QA preview'}
                        </div>
                        <img
                          alt="Run LOS velocity toward radar positive"
                          src={getSbasInsarRunArtifactUrl(run.run_id, runPrimaryPreview.relative_path)}
                          style={{
                            width: '100%',
                            aspectRatio: '4 / 3',
                            objectFit: 'contain',
                            border: '1px solid #d8dee8',
                            borderRadius: 8,
                            background: '#f8fafc',
                          }}
                        />
                      </div>
                    )}
                    {runSigmaPreview && (
                      <div>
                        <div style={{ ...valueStyle, marginBottom: 6 }}>LOS Sigma</div>
                        <div style={{ ...mutedStyle, marginBottom: 6 }}>
                          {runSigmaPreview.key.endsWith('_geo_preview_png') ? 'WGS84 geocoded preview' : 'RDC QA preview'}
                        </div>
                        <img
                          alt="Run LOS velocity sigma"
                          src={getSbasInsarRunArtifactUrl(run.run_id, runSigmaPreview.relative_path)}
                          style={{
                            width: '100%',
                            aspectRatio: '4 / 3',
                            objectFit: 'contain',
                            border: '1px solid #d8dee8',
                            borderRadius: 8,
                            background: '#f8fafc',
                          }}
                        />
                      </div>
                    )}
                    {runMonitorPreview && (
                      <div>
                        <div style={{ ...valueStyle, marginBottom: 6 }}>Monitoring Curve</div>
                        <img
                          alt="Run monitoring point LOS displacement time series"
                          src={getSbasInsarRunArtifactUrl(run.run_id, runMonitorPreview.relative_path)}
                          style={{
                            width: '100%',
                            aspectRatio: '4 / 3',
                            objectFit: 'contain',
                            border: '1px solid #d8dee8',
                            borderRadius: 8,
                            background: '#ffffff',
                          }}
                        />
                        {runMonitorCsv && (
                          <div style={{ ...mutedStyle, marginTop: 6 }}>
                            <RunArtifactLink runId={run.run_id} artifact={runMonitorCsv} />
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}

                {runArtifacts.length > 0 && (
                  <div style={{ border: '1px solid #e2e8f0', borderRadius: 8, overflow: 'hidden' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                      <thead>
                        <tr style={{ background: '#f8fafc', color: '#475569' }}>
                          <th style={{ textAlign: 'left', padding: '8px 10px', borderBottom: '1px solid #e2e8f0' }}>文件</th>
                          <th style={{ textAlign: 'left', padding: '8px 10px', borderBottom: '1px solid #e2e8f0' }}>角色</th>
                          <th style={{ textAlign: 'right', padding: '8px 10px', borderBottom: '1px solid #e2e8f0' }}>操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        {runArtifacts.map(item => (
                          <tr key={item.key}>
                            <td style={{ padding: '8px 10px', borderBottom: '1px solid #f1f5f9', color: '#0f172a' }}>{item.label}</td>
                            <td style={{ padding: '8px 10px', borderBottom: '1px solid #f1f5f9', color: '#64748b' }}>{item.role}</td>
                            <td style={{ padding: '8px 10px', borderBottom: '1px solid #f1f5f9', textAlign: 'right' }}>
                              <RunArtifactLink runId={run.run_id} artifact={item} />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </>
            )}
            {!runDetailLoading && !run && (
              <div style={mutedStyle}>请选择一个生产 Run，或从候选序列创建新的计划 Run。</div>
            )}
          </div>
        </div>
      </section>

    </div>
  );
}
