import React, { useCallback, useEffect, useMemo, useState } from 'react';

import {
  auditSbasInsarStack,
  decideSbasInsarItab,
  discoverSbasInsarStacks,
  getSbasInsarArtifactUrl,
  getSbasInsarCapabilities,
  getSbasInsarRun,
  getSbasInsarRunArtifactUrl,
  getSbasInsarTrialRun,
  listSbasInsarRuns,
  listSbasInsarTrialRuns,
  prepareSbasInsarCoregistration,
  runSbasInsarBaselineAudit,
  submitSbasInsarCoregistrationJob,
  submitSbasInsarRun,
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
  const okValues = new Set(['TRIAL_READY', 'READY', 'READY_FOR_GAMMA_BASELINE_AUDIT']);
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

function ArtifactLink({ trialId, artifact }) {
  const href = getSbasInsarArtifactUrl(trialId, artifact.relative_path);
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      style={{ color: '#0f766e', fontWeight: 650, textDecoration: 'none' }}
    >
      打开
    </a>
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
  const [trials, setTrials] = useState([]);
  const [runs, setRuns] = useState([]);
  const [selectedTrialId, setSelectedTrialId] = useState('');
  const [selectedRunId, setSelectedRunId] = useState('');
  const [detail, setDetail] = useState(null);
  const [runDetail, setRunDetail] = useState(null);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
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

  const loadTrials = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [capabilityData, trialData, runData] = await Promise.all([
        getSbasInsarCapabilities(),
        listSbasInsarTrialRuns(),
        listSbasInsarRuns(),
      ]);
      const items = Array.isArray(trialData?.items) ? trialData.items : [];
      const runItems = Array.isArray(runData?.items) ? runData.items : [];
      setCapabilities(capabilityData);
      setTrials(items);
      setRuns(runItems);
      setSelectedTrialId(current => current || items[0]?.trial_id || '');
      setSelectedRunId(current => current || runItems[0]?.run_id || '');
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR 列表加载失败');
      setCapabilities(null);
      setTrials([]);
      setRuns([]);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadDetail = useCallback(async trialId => {
    if (!trialId) {
      setDetail(null);
      return;
    }
    setDetailLoading(true);
    setError('');
    try {
      const data = await getSbasInsarTrialRun(trialId);
      setDetail(data);
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR 详情加载失败');
      setDetail(null);
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    loadTrials();
  }, [loadTrials]);

  useEffect(() => {
    loadDetail(selectedTrialId);
  }, [loadDetail, selectedTrialId]);

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
        dry_run: true,
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

  const artifacts = useMemo(() => detail?.artifacts || [], [detail]);
  const primaryPreview = (
    artifacts.find(item => item.key === 'los_rate_toward_mm_per_year_geo_preview_png')
    || artifacts.find(item => item.key === 'los_rate_toward_mm_per_year_bmp')
  );
  const sigmaPreview = (
    artifacts.find(item => item.key === 'los_sigma_mm_per_year_geo_preview_png')
    || artifacts.find(item => item.key === 'los_sigma_mm_per_year_bmp')
  );
  const monitorPreview = artifacts.find(item => item.role === 'monitor_point' && item.relative_path.endsWith('.png'));
  const monitorCsv = artifacts.find(item => item.role === 'monitor_point' && item.relative_path.endsWith('.csv'));
  const productArtifacts = artifacts.filter(item => item.role !== 'monitor_point');
  const trial = detail?.trial || null;
  const stack = trial?.stack || {};
  const summary = detail?.summary || {};
  const radar = summary.radar || {};
  const monitorPoint = Array.isArray(summary.monitor_points) ? summary.monitor_points[0] : null;
  const selectedStack = stackCandidates.find(item => item.stack_id === selectedStackId) || null;
  const run = runDetail?.run || null;
  const runManifest = runDetail?.manifest || {};
  const stagePlan = runDetail?.command_manifest?.stage_plan || [];
  const runArtifacts = runDetail?.artifacts || [];
  const baselineSummary = runManifest.baseline_audit?.summary || null;
  const itabDecision = runManifest.baseline_audit?.itab_decision || null;
  const coregistrationPlan = runManifest.coregistration || null;
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
            onClick={loadTrials}
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

      <div style={gridStyle}>
        <section style={sectionStyle}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
            <h3 style={{ margin: 0, fontSize: 15, color: '#0f172a' }}>试验/生产序列</h3>
            <span style={mutedStyle}>{trials.length} 组</span>
          </div>
          <div style={{ display: 'grid', gap: 8, marginTop: 12 }}>
            {trials.map(item => {
              const active = item.trial_id === selectedTrialId;
              return (
                <button
                  key={item.trial_id}
                  type="button"
                  onClick={() => setSelectedTrialId(item.trial_id)}
                  style={{
                    ...buttonBaseStyle,
                    borderColor: active ? '#0f766e' : '#d8dee8',
                    background: active ? '#f0fdfa' : '#ffffff',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                    <strong style={{ color: '#0f172a', fontSize: 13 }}>{item.platform || 'LT1'} / {item.relative_orbit || '-'}</strong>
                    <StatusBadge value={item.status} />
                  </div>
                  <div style={{ ...mutedStyle, marginTop: 6 }}>
                    {item.dates?.[0] || '-'} 至 {item.dates?.[item.dates.length - 1] || '-'}，
                    {item.scene_count || 0} 景，{item.direction || '-'}，{item.polarization || '-'}
                  </div>
                </button>
              );
            })}
            {!loading && trials.length === 0 && (
              <div style={{ ...mutedStyle, padding: '10px 0' }}>
                未发现可读取的 Gamma SBAS/IPTA 试验汇总。
              </div>
            )}
          </div>
        </section>

        <section style={sectionStyle}>
          {detailLoading && <div style={mutedStyle}>正在加载详情...</div>}
          {!detailLoading && trial && (
            <div style={{ display: 'grid', gap: 12 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'flex-start' }}>
                <div>
                  <h3 style={{ margin: 0, fontSize: 17, color: '#0f172a' }}>{trial.trial_id}</h3>
                  <div style={{ ...mutedStyle, marginTop: 5 }}>
                    {stack.platform} / relOrbit {stack.relative_orbit} / {stack.direction} / {stack.mode} / {stack.polarization}
                  </div>
                </div>
                <StatusBadge value={trial.status} />
              </div>

              <div style={metricGridStyle}>
                <Metric label="日期数" value={`${trial.scene_count || 0}`} />
                <Metric label="参考日期" value={trial.reference_date || '-'} />
                <Metric label="LOS 速率中位数" value={formatValue(trial.primary_rate_median_mm_year, ' mm/yr')} />
                <Metric label="LOS sigma 中位数" value={formatValue(trial.sigma_median_mm_year, ' mm/yr')} />
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 12 }}>
                {primaryPreview && (
                  <div>
                    <div style={{ ...valueStyle, marginBottom: 6 }}>LOS 速率图</div>
                    <div style={{ ...mutedStyle, marginBottom: 6 }}>
                      {primaryPreview.key.endsWith('_geo_preview_png') ? 'WGS84 地理编码预览' : 'RDC 处理几何浏览图'}
                    </div>
                    <img
                      alt="LOS velocity toward radar positive"
                      src={getSbasInsarArtifactUrl(trial.trial_id, primaryPreview.relative_path)}
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
                {monitorPreview && (
                  <div>
                    <div style={{ ...valueStyle, marginBottom: 6 }}>监测点形变曲线</div>
                    <img
                      alt="Monitoring point LOS displacement time series"
                      src={getSbasInsarArtifactUrl(trial.trial_id, monitorPreview.relative_path)}
                      style={{
                        width: '100%',
                        aspectRatio: '4 / 3',
                        objectFit: 'contain',
                        border: '1px solid #d8dee8',
                        borderRadius: 8,
                        background: '#ffffff',
                      }}
                    />
                  </div>
                )}
                {sigmaPreview && (
                  <div>
                    <div style={{ ...valueStyle, marginBottom: 6 }}>LOS sigma 图</div>
                    <div style={{ ...mutedStyle, marginBottom: 6 }}>
                      {sigmaPreview.key.endsWith('_geo_preview_png') ? 'WGS84 地理编码预览' : 'RDC 处理几何浏览图'}
                    </div>
                    <img
                      alt="LOS velocity sigma"
                      src={getSbasInsarArtifactUrl(trial.trial_id, sigmaPreview.relative_path)}
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
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 12 }}>
                <div style={{ border: '1px solid #e2e8f0', borderRadius: 8, padding: 10 }}>
                  <div style={valueStyle}>LOS 符号约定</div>
                  <div style={{ ...mutedStyle, marginTop: 6 }}>
                    {radar.los_sign_convention || trial.los_sign_convention || '-'}
                  </div>
                </div>
              <div style={{ border: '1px solid #e2e8f0', borderRadius: 8, padding: 10 }}>
                <div style={valueStyle}>监测点</div>
                <div style={{ ...mutedStyle, marginTop: 6 }}>
                    {monitorPoint ? (
                      <>
                        {monitorPoint.point_id}，约 {formatValue(monitorPoint.approx_lonlat?.lon)}E /
                        {formatValue(monitorPoint.approx_lonlat?.lat)}N，速率
                        {formatValue(monitorPoint.los_rate_toward_mm_per_year, ' mm/yr')}
                        {monitorCsv && (
                          <>
                            {' '}
                            <ArtifactLink trialId={trial.trial_id} artifact={monitorCsv} />
                          </>
                        )}
                      </>
                    ) : '-'}
                  </div>
                </div>
              </div>
              <div style={{ ...mutedStyle }}>
                当前曲线是自动选取的单个样例点，用于验证时序曲线能力；正式监测点需要用户点击、导入点位或质量筛选后的点集。
              </div>

              <div>
                <div style={{ ...valueStyle, marginBottom: 8 }}>产品文件</div>
                <div style={{ overflowX: 'auto', border: '1px solid #e2e8f0', borderRadius: 8 }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                    <thead>
                      <tr style={{ background: '#f8fafc', color: '#475569' }}>
                        <th style={{ textAlign: 'left', padding: '8px 10px', borderBottom: '1px solid #e2e8f0' }}>产品</th>
                        <th style={{ textAlign: 'left', padding: '8px 10px', borderBottom: '1px solid #e2e8f0' }}>角色</th>
                        <th style={{ textAlign: 'right', padding: '8px 10px', borderBottom: '1px solid #e2e8f0' }}>大小</th>
                        <th style={{ textAlign: 'right', padding: '8px 10px', borderBottom: '1px solid #e2e8f0' }}>操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {productArtifacts.map(item => (
                        <tr key={item.key}>
                          <td style={{ padding: '8px 10px', borderBottom: '1px solid #f1f5f9', color: '#0f172a' }}>{item.label}</td>
                          <td style={{ padding: '8px 10px', borderBottom: '1px solid #f1f5f9', color: '#64748b' }}>{item.role}</td>
                          <td style={{ padding: '8px 10px', borderBottom: '1px solid #f1f5f9', textAlign: 'right', color: '#64748b' }}>
                            {formatBytes(item.size_bytes)}
                          </td>
                          <td style={{ padding: '8px 10px', borderBottom: '1px solid #f1f5f9', textAlign: 'right' }}>
                            <ArtifactLink trialId={trial.trial_id} artifact={item} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}
          {!detailLoading && !trial && (
            <div style={mutedStyle}>请选择一个 SBAS-InSAR 试验或生产序列。</div>
          )}
        </section>
      </div>
    </div>
  );
}
