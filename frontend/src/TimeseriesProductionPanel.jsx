import React, { useCallback, useEffect, useMemo, useState } from 'react';

import { getPsBatches } from './api/taskBatches';
import {
  createTimeseriesRun,
  getTimeseriesRunDetail,
  listTimeseriesRuns,
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

function formatDateTime(value) {
  if (!value) return '-';
  try {
    return new Date(value).toLocaleString();
  } catch {
    return String(value);
  }
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

export default function TimeseriesProductionPanel({ readOnly = false, onJobQueued }) {
  const [batches, setBatches] = useState([]);
  const [runs, setRuns] = useState([]);
  const [selectedBatchId, setSelectedBatchId] = useState('');
  const [selectedRunId, setSelectedRunId] = useState('');
  const [selectedRunDetail, setSelectedRunDetail] = useState(null);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState('');
  const [runName, setRunName] = useState('');
  const [referenceDate, setReferenceDate] = useState('');
  const [waterMaskMode, setWaterMaskMode] = useState('synthetic_fallback');
  const [notes, setNotes] = useState('');

  const selectedBatch = useMemo(
    () => batches.find(item => item.batch_id === selectedBatchId) || null,
    [batches, selectedBatchId]
  );

  const loadBatches = useCallback(async () => {
    try {
      const data = await getPsBatches();
      const nextItems = Array.isArray(data) ? data : [];
      setBatches(nextItems);
      setSelectedBatchId(current => current || nextItems[0]?.batch_id || '');
    } catch {
      setBatches([]);
    }
  }, []);

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
      return;
    }
    setDetailLoading(true);
    try {
      const detail = await getTimeseriesRunDetail(runId);
      setSelectedRunDetail(detail);
    } catch (error) {
      setSelectedRunDetail({
        error: error?.response?.data?.detail || error.message || '运行详情加载失败',
      });
    } finally {
      setDetailLoading(false);
    }
  }, []);

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
        notes: notes.trim() || null,
      });
      setMessage(`运行已入队：${result.run_id} / task=${result.task_id}`);
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

  return (
    <div style={{ padding: '16px 0', width: '100%' }}>
      <div style={card}>
        <strong style={{ fontSize: 14, display: 'block', marginBottom: 10 }}>时序InSAR 运行入口</strong>
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
          当前接入实现为 SBAS。现阶段已连通完整八步链路：prepare、stack_prep_initial、materialize、
          stack_prep_refresh、run_isce2_stack、run_mintpy_sbas、export_publish_bundle、
          register_psinsar_product。提交后系统会依次生成选栈 manifest、物化 LT-1 SLC、执行 ISCE2
          stack、运行 MintPy SBAS、导出 publish bundle，并把结果注册进时序InSAR catalog。
        </div>
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
            <div><strong>方向：</strong>{selectedBatch.direction || '-'}</div>
            <div><strong>影像数：</strong>{selectedBatch.total_items || 0}</div>
            <div><strong>批次状态：</strong>{selectedBatch.status || '-'}</div>
            <div><strong>更新时间：</strong>{formatDateTime(selectedBatch.updated_at)}</div>
          </div>
        )}

        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 12 }}>
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
                <div><strong>轨道摘要：</strong>{JSON.stringify(runData?.orbit_summary_json || {}, null, 2)}</div>
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
                          <div style={{ flexShrink: 0 }}>
                            <StatusPill value={step.status} />
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
