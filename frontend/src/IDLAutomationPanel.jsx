import React, { useCallback, useEffect, useState } from 'react';
import { Chart as ChartJS, ArcElement, Tooltip, Legend, CategoryScale, LinearScale, BarElement, Title } from 'chart.js';
import { Doughnut, Bar } from 'react-chartjs-2';
import {
  getEnviStatus,
  inspectImport,
  inspectDinsar,
  queueImportJob,
  queueDinsarJob,
  getRecentRuns,
  forceCancelTask,
  extractDispResults,
  getTaskOverview,
  getJobLog,
  deleteRun,
} from './api/idl';
import TaskStatusPanel from './components/tasks/TaskStatusPanel';
import useTaskMonitor from './hooks/useTaskMonitor';

import { getStatistics } from './api/stats';

ChartJS.register(ArcElement, Tooltip, Legend, CategoryScale, LinearScale, BarElement, Title);

const cardStyle = {
  background: '#fff',
  padding: '12px',
  borderRadius: '8px',
  border: '1px solid #e2e8f0',
  marginBottom: '12px',
};

function IDLAutomationPanel({ readOnly = false, onJobQueued }) {
  const [status, setStatus] = useState(null);
  const [recentRuns, setRecentRuns] = useState([]);
  const [isBusy, setIsBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [showCancelInput, setShowCancelInput] = useState(false);
  const [cancelPassword, setCancelPassword] = useState('');
  const idlTaskMonitor = useTaskMonitor({
    taskTypes: ['IDL_RUN_IMPORT', 'IDL_RUN_DINSAR', 'EXTRACT_DINSAR_PRODUCTS'],
    showRecent: true,
    recentLimit: 1,
  });
  const runningTask = idlTaskMonitor.activeTasks[0] || null;

  const [importRootDir, setImportRootDir] = useState('');
  const [importNumToProcess, setImportNumToProcess] = useState(0);
  const [dinsarRootDir, setDinsarRootDir] = useState('');
  const [dinsarNumToProcess, setDinsarNumToProcess] = useState(0);
  const [extractRootDir, setExtractRootDir] = useState('');
  const [extractDestDir, setExtractDestDir] = useState('');
  const [extractResult, setExtractResult] = useState(null);
  const [overview, setOverview] = useState(null);
  const [overviewLoading, setOverviewLoading] = useState(false);
  const [logModal, setLogModal] = useState({ open: false, runId: '', content: '', loading: false, truncated: false, sizeBytes: 0 });
  const [chartStats, setChartStats] = useState(null);

  useEffect(() => {
    getStatistics().then(setChartStats).catch(() => {});
  }, []);

  const [importInspect, setImportInspect] = useState(null);
  const [dinsarInspect, setDinsarInspect] = useState(null);

  const refreshData = useCallback(async () => {
    const [s, runs] = await Promise.all([
      getEnviStatus(),
      getRecentRuns(20),
    ]);
    setStatus(s);
    setRecentRuns(Array.isArray(runs?.runs) ? runs.runs : []);
  }, []);

  useEffect(() => {
    refreshData().catch(() => {});
    if (!runningTask) {
      return undefined;
    }
    const timer = setInterval(() => refreshData().catch(() => {}), 15000);
    return () => clearInterval(timer);
  }, [refreshData, runningTask]);

  const runAction = async (action) => {
    setIsBusy(true);
    setMessage('');
    try {
      await action();
      await refreshData();
    } catch (error) {
      const detail = error?.response?.data?.detail || error?.message || '未知错误';
      setMessage(`执行失败: ${detail}`);
    } finally {
      setIsBusy(false);
    }
  };

  const handleInspectImport = () =>
    runAction(async () => {
      const r = await inspectImport(importRootDir.trim());
      setImportInspect(r);
      setMessage(`Import 预检查: ${r?.ready ? '就绪' : '未就绪'}`);
    });

  const handleInspectDinsar = () =>
    runAction(async () => {
      const r = await inspectDinsar(dinsarRootDir.trim());
      setDinsarInspect(r);
      setMessage(`D-InSAR 预检查: ${r?.ready ? '就绪' : '未就绪'}`);
    });

  const handleQueueImport = () => {
    if (readOnly) return setMessage('只读账号无法提交任务。');
    const root = importRootDir.trim();
    if (!root) return setMessage('请填写数据目录。');
    runAction(async () => {
      const r = await queueImportJob({
        root_dir: root,
        num_to_process: Number(importNumToProcess) || 0,
      });
      setMessage(`Import 任务已入队。task_id=${r?.task_id || '-'}`);
      if (r?.task_id) onJobQueued?.(r.task_id);
    });
  };

  const handleQueueDinsar = (mode) => {
    if (readOnly) return setMessage('只读账号无法提交任务。');
    const root = dinsarRootDir.trim();
    if (!root) return setMessage('请填写数据目录。');
    const modeLabel = mode === 'custom' ? '自定义' : '默认';
    runAction(async () => {
      const r = await queueDinsarJob({
        root_dir: root,
        num_to_process: Number(dinsarNumToProcess) || 0,
        mode,
      });
      setMessage(`D-InSAR (${modeLabel}) 任务已入队。task_id=${r?.task_id || '-'}`);
      if (r?.task_id) onJobQueued?.(r.task_id);
    });
  };

  const handleExtractDisp = () => {
    if (readOnly) return setMessage('只读账号无法执行提取。');
    const root = extractRootDir.trim();
    if (!root) return setMessage('请填写数据目录。');
    const dest = extractDestDir.trim() || null;
    runAction(async () => {
      const r = await extractDispResults(root, dest);
      setExtractResult(r);
      setMessage(`D-InSAR 结果提取与登记任务已入队。task_id=${r?.task_id || '-'}`);
      if (r?.task_id) onJobQueued?.(r.task_id);
    });
  };

  const handleLoadOverview = async () => {
    const root = dinsarRootDir.trim();
    if (!root) return;
    setOverviewLoading(true);
    try {
      const r = await getTaskOverview(root);
      setOverview(r);
    } catch (e) {
      setOverview(null);
      setMessage(`总览加载失败: ${e?.response?.data?.detail || e?.message}`);
    } finally {
      setOverviewLoading(false);
    }
  };

  const handleOpenLog = async (runId) => {
    setLogModal({ open: true, runId, content: '', loading: true, truncated: false, sizeBytes: 0 });
    try {
      const r = await getJobLog(runId);
      setLogModal({ open: true, runId, content: r.content, loading: false, truncated: r.truncated, sizeBytes: r.size_bytes });
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.message || '加载失败';
      setLogModal({ open: true, runId, content: `错误: ${msg}`, loading: false, truncated: false, sizeBytes: 0 });
    }
  };

  const handleCancelRunningTask = async () => {
    if (!runningTask || !cancelPassword) return;
    try {
      await forceCancelTask(runningTask.task_id, cancelPassword);
      setMessage('任务取消请求已提交。');
      setShowCancelInput(false);
      setCancelPassword('');
      await idlTaskMonitor.refreshRecentTasks();
      await refreshData();
    } catch (error) {
      const detail = error?.response?.data?.detail || error?.message || '取消失败';
      setMessage(`取消任务失败: ${detail}`);
    }
  };

  const renderInspect = (title, result) => {
    if (!result) return null;
    return (
      <div style={{ marginTop: '8px', padding: '10px', background: '#f8fafc', borderRadius: '6px', border: '1px solid #e2e8f0' }}>
        <div style={{ fontWeight: 600, marginBottom: '4px' }}>{title}</div>
        <div style={{ fontSize: '12px', color: '#334155' }}>
          状态: <strong style={{ color: result?.ready ? '#16a34a' : '#dc2626' }}>{result?.ready ? '就绪' : '未就绪'}</strong>
        </div>
        {result?.warnings?.length > 0 && (
          <div style={{ fontSize: '12px', color: '#b45309', marginTop: '4px' }}>
            {result.warnings.map((w, i) => <div key={i}>⚠ {w}</div>)}
          </div>
        )}
        <pre style={{ margin: '8px 0 0', whiteSpace: 'pre-wrap', fontSize: '12px', color: '#475569' }}>
          {JSON.stringify(result?.summary || {}, null, 2)}
        </pre>
      </div>
    );
  };

  // Buttons are locally disabled when submitting API calls or an IDL task is already active.
  const isLocked = isBusy || !!runningTask;

  const demDisplay = status?.dem_base_file || '-';
  const demOk = status?.dem_exists;

  return (
    <div className="idl-automation-panel" style={{ padding: '15px', height: '100%', overflowY: 'auto' }}>
      <h3 style={{ marginTop: 0 }}>SARscape 处理中心</h3>

      {/* Status bar */}
      <div style={{ ...cardStyle, background: '#f8fafc' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap', fontSize: '13px' }}>
          <span>
            ENVI: <strong>{status?.idl_installed ? '已安装' : '未检测到'}</strong>
            {status?.idl_running && ' (运行中)'}
          </span>
          <span>引擎: <strong>{status?.engine || '-'}</strong></span>
          <span style={{ color: demOk ? '#16a34a' : '#dc2626' }}>
            DEM: <code style={{ fontSize: '11px' }}>{demDisplay.length > 40 ? '...' + demDisplay.slice(-37) : demDisplay}</code>
            {demOk ? ' ✓' : ' ✗'}
          </span>
        </div>
      </div>

      <TaskStatusPanel
        title="ENVI / SARscape 任务"
        activeTasks={idlTaskMonitor.activeTasks}
        recentTasks={idlTaskMonitor.recentTasks}
        latestTask={idlTaskMonitor.latestTask}
        isBusy={idlTaskMonitor.isBusy}
        idleText="当前没有正在执行的 ENVI / SARscape 任务。"
        action={runningTask && !readOnly && !showCancelInput ? (
          <button
            type="button"
            onClick={() => setShowCancelInput(true)}
            style={{
              padding: '3px 10px',
              borderRadius: '4px',
              border: '1px solid #dc2626',
              background: '#fef2f2',
              color: '#dc2626',
              fontSize: '12px',
              cursor: 'pointer',
            }}
          >
            取消任务
          </button>
        ) : null}
        footer={runningTask ? (
          <>
          {showCancelInput && (
            <div style={{ marginTop: '8px', display: 'flex', alignItems: 'center', gap: '8px' }}>
              <input
                type="password"
                placeholder="输入管理员密码"
                value={cancelPassword}
                onChange={(e) => setCancelPassword(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleCancelRunningTask()}
                style={{ padding: '4px 8px', fontSize: '12px', borderRadius: '4px', border: '1px solid #d1d5db', width: '160px' }}
              />
              <button
                type="button"
                onClick={handleCancelRunningTask}
                disabled={!cancelPassword}
                style={{
                  padding: '4px 10px',
                  borderRadius: '4px',
                  border: '1px solid #dc2626',
                  background: '#dc2626',
                  color: '#fff',
                  fontSize: '12px',
                  cursor: cancelPassword ? 'pointer' : 'not-allowed',
                  opacity: cancelPassword ? 1 : 0.5,
                }}
              >
                确认取消
              </button>
              <button
                type="button"
                onClick={() => { setShowCancelInput(false); setCancelPassword(''); }}
                style={{ padding: '4px 10px', borderRadius: '4px', border: '1px solid #d1d5db', background: '#fff', fontSize: '12px', cursor: 'pointer' }}
              >
                取消
              </button>
            </div>
          )}
          {!showCancelInput && (
            <div style={{ fontSize: '11px', color: '#92400e', marginTop: '4px', marginLeft: '26px' }}>
              同类 ENVI/SARscape 任务运行中，当前提交按钮暂不可用。
            </div>
          )}
          </>
        ) : null}
      />

      {/* Task 状态总览 */}
      <div style={cardStyle}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
          <h4 style={{ margin: 0 }}>Task 状态总览</h4>
          <button
            type="button"
            className="secondary-btn"
            onClick={handleLoadOverview}
            disabled={overviewLoading || !dinsarRootDir.trim()}
            style={{ fontSize: '12px', padding: '3px 10px' }}
          >
            {overviewLoading ? '加载中...' : '刷新'}
          </button>
        </div>
        {!overview && !overviewLoading && (
          <div style={{ fontSize: '12px', color: '#94a3b8' }}>填写下方数据目录后点击刷新</div>
        )}
        {overview && (
          <>
            <div style={{ fontSize: '12px', color: '#475569', marginBottom: '8px' }}>
              共 <strong>{overview.summary.total}</strong> 个 Task &nbsp;·&nbsp;
              已导入 <strong style={{ color: '#2563eb' }}>{overview.summary.imported}</strong> &nbsp;·&nbsp;
              已处理 <strong style={{ color: '#7c3aed' }}>{overview.summary.dinsar_done}</strong> &nbsp;·&nbsp;
              已提取 <strong style={{ color: '#16a34a' }}>{overview.summary.extracted}</strong>
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                <thead>
                  <tr style={{ background: '#f1f5f9' }}>
                    <th style={{ textAlign: 'left', padding: '4px 8px', fontWeight: 600 }}>Task</th>
                    <th style={{ textAlign: 'center', padding: '4px 8px', fontWeight: 600 }}>结构</th>
                    <th style={{ textAlign: 'center', padding: '4px 8px', fontWeight: 600 }}>已导入</th>
                    <th style={{ textAlign: 'center', padding: '4px 8px', fontWeight: 600 }}>已处理</th>
                    <th style={{ textAlign: 'center', padding: '4px 8px', fontWeight: 600 }}>已提取</th>
                    <th style={{ textAlign: 'right', padding: '4px 8px', fontWeight: 600, color: '#94a3b8' }}>修改时间</th>
                  </tr>
                </thead>
                <tbody>
                  {overview.tasks.map((t) => (
                    <tr key={t.task_name} style={{ borderTop: '1px solid #e2e8f0' }}>
                      <td style={{ padding: '4px 8px', fontFamily: 'monospace', fontSize: '11px' }}>{t.task_name}</td>
                      {[t.has_structure, t.imported, t.dinsar_done, t.extracted].map((v, i) => (
                        <td key={i} style={{ textAlign: 'center', padding: '4px 8px' }}>
                          <span style={{ color: v ? '#16a34a' : '#dc2626', fontWeight: 700 }}>{v ? '✓' : '✗'}</span>
                        </td>
                      ))}
                      <td style={{ textAlign: 'right', padding: '4px 8px', color: '#94a3b8', fontSize: '11px' }}>{t.last_modified}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>

      {/* Step 1: Import */}
      <div style={cardStyle}>
        <h4 style={{ marginTop: 0 }}>Step 1: 数据导入 (Import)</h4>
        <p style={{ fontSize: '12px', color: 'var(--color-text-secondary)', margin: '0 0 10px' }}>
          扫描目录下的 LuTan-1 原始数据 (*.meta.xml)，转换为 ENVI SARscape 格式 (*.sml)。
          支持 Task_*/master|slave 结构和平铺目录。
        </p>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 140px', gap: '10px', alignItems: 'start' }}>
          <label className="user-admin-field">
            <span>数据目录 (root_dir)</span>
            <input
              type="text"
              value={importRootDir}
              onChange={(e) => setImportRootDir(e.target.value)}
              placeholder="例如: Z:/Test_data/Test_IDL_1"
              disabled={isLocked}
            />
          </label>
          <label className="user-admin-field">
            <span>最大处理数</span>
            <input
              type="number"
              min={0}
              value={importNumToProcess}
              onChange={(e) => setImportNumToProcess(e.target.value)}
              placeholder="0"
              disabled={isLocked}
            />
            <span style={{ fontSize: '11px', color: 'var(--color-text-muted, #94a3b8)' }}>0 = 不限制</span>
          </label>
        </div>
        <div style={{ marginTop: '10px', display: 'flex', gap: '8px' }}>
          <button type="button" className="secondary-btn" disabled={isLocked || readOnly} onClick={handleInspectImport}>
            预检查
          </button>
          <button type="button" className="primary-btn" disabled={isLocked || readOnly} onClick={handleQueueImport}>
            开始导入
          </button>
        </div>
        {renderInspect('Import 检查结果', importInspect)}
      </div>

      {/* Step 2: D-InSAR */}
      <div style={cardStyle}>
        <h4 style={{ marginTop: 0 }}>Step 2: D-InSAR 生产</h4>
        <p style={{ fontSize: '12px', color: 'var(--color-text-secondary)', margin: '0 0 10px' }}>
          对 Task_* 目录执行 D-InSAR 位移生成。
          未导入的数据将自动先执行 Import，再进行 D-InSAR 处理。
          DEM 路径由系统配置管理，无需手动指定。
        </p>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 140px', gap: '10px', alignItems: 'start' }}>
          <label className="user-admin-field">
            <span>数据目录 (root_dir)</span>
            <input
              type="text"
              value={dinsarRootDir}
              onChange={(e) => setDinsarRootDir(e.target.value)}
              placeholder="例如: Z:/Test_data/Test_IDL_1"
              disabled={isLocked}
            />
          </label>
          <label className="user-admin-field">
            <span>最大处理数</span>
            <input
              type="number"
              min={0}
              value={dinsarNumToProcess}
              onChange={(e) => setDinsarNumToProcess(e.target.value)}
              placeholder="0"
              disabled={isLocked}
            />
            <span style={{ fontSize: '11px', color: 'var(--color-text-muted, #94a3b8)' }}>0 = 不限制</span>
          </label>
        </div>
        <div style={{ marginTop: '10px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {/* 预检查 */}
          <div>
            <button type="button" className="secondary-btn" disabled={isLocked || readOnly} onClick={handleInspectDinsar}>
              预检查
            </button>
            <div style={{ marginTop: '3px', fontSize: '11px', color: '#64748b' }}>
              扫描目录，统计待处理对数，检查 DEM 和 SML 文件是否就绪，不执行任何处理。
            </div>
          </div>
          {/* 默认处理 */}
          <div>
            <button type="button" className="primary-btn" disabled={isLocked || readOnly} onClick={() => handleQueueDinsar('metatask')}>
              默认处理
            </button>
            <div style={{ marginTop: '3px', fontSize: '11px', color: '#64748b' }}>
              调用 ENVI SARscape metatask 一键执行全流程。参数：3×3 多视、默认自适应滤波、自动 GCP 精配准。
            </div>
          </div>
          {/* 自定义处理 */}
          <div>
            <button
              type="button"
              disabled={isLocked || readOnly}
              onClick={() => handleQueueDinsar('custom')}
              style={{
                padding: '6px 14px',
                borderRadius: '6px',
                border: '1px solid #818cf8',
                background: '#eef2ff',
                color: '#4338ca',
                fontWeight: 500,
                fontSize: '13px',
                cursor: isLocked || readOnly ? 'not-allowed' : 'pointer',
                opacity: isLocked || readOnly ? 0.5 : 1,
              }}
            >
              自定义处理
            </button>
            <div style={{ marginTop: '3px', fontSize: '11px', color: '#64748b' }}>
              逐步调用各处理模块，参数可控。多视：动态计算、GOLDSTEIN 滤波、自动 GCP、相干阈值 coh=0.05。
            </div>
          </div>
        </div>
        {renderInspect('D-InSAR 检查结果', dinsarInspect)}
      </div>

      {/* Step 3: Extract Disp */}
      <div style={cardStyle}>
        <h4 style={{ marginTop: 0 }}>Step 3: 结果提取</h4>
        <p style={{ fontSize: '12px', color: 'var(--color-text-secondary)', margin: '0 0 10px' }}>
          将 D-InSAR 生产结果中的 disp 文件提取到目标目录，以便系统识别。
        </p>
        <label className="user-admin-field">
          <span>数据目录 (root_dir)</span>
          <input
            type="text"
            value={extractRootDir}
            onChange={(e) => setExtractRootDir(e.target.value)}
            placeholder="例如: Z:/Test_data/Test_IDL_1"
            disabled={isLocked}
          />
        </label>
        <label className="user-admin-field" style={{ marginTop: '8px' }}>
          <span>目标目录 (可选)</span>
          <input
            type="text"
            value={extractDestDir}
            onChange={(e) => setExtractDestDir(e.target.value)}
            placeholder="留空则使用默认路径 (MONITOR_DINSAR_DIRS[0])"
            disabled={isLocked}
          />
        </label>
        <div style={{ marginTop: '10px' }}>
          <button
            type="button"
            className="primary-btn"
            disabled={isLocked || readOnly || isBusy}
            onClick={handleExtractDisp}
          >
            提取 Disp 结果
          </button>
        </div>
        {extractResult && (
          <div style={{ marginTop: '8px', padding: '10px', background: '#f0fdf4', borderRadius: '6px', border: '1px solid #bbf7d0', fontSize: '12px', color: '#166534' }}>
            {extractResult.queued ? (
              <div>D-InSAR 结果提取与登记任务已入队。task_id={extractResult.task_id || '-'}</div>
            ) : (
              <>
                <div>目标目录: <code style={{ fontSize: '11px' }}>{extractResult.target_dir}</code></div>
                <div style={{ marginTop: '4px' }}>
                  处理 {extractResult.processed} 个 Task &nbsp;·&nbsp;
                  新增 {extractResult.copied} &nbsp;·&nbsp;
                  更新 {extractResult.overwritten} &nbsp;·&nbsp;
                  跳过 {extractResult.skipped}
                  {extractResult.failed > 0 && (
                    <span style={{ color: '#dc2626' }}> &nbsp;·&nbsp; 失败 {extractResult.failed}</span>
                  )}
                </div>
              </>
            )}
          </div>
        )}
      </div>

      {/* Run history */}
      <div style={cardStyle}>
        <h4 style={{ marginTop: 0 }}>任务历史</h4>
        {recentRuns.length === 0 ? (
          <div style={{ color: '#64748b', fontSize: '13px' }}>暂无运行记录</div>
        ) : (
          <div style={{ display: 'grid', gap: '6px' }}>
            {recentRuns.map((run) => {
              const s = run.summary || {};
              const counts = s.processed != null
                ? `成功 ${s.processed} / 失败 ${s.failed || 0} / 跳过 ${s.skipped || 0}`
                : '';
              const autoImported = s.auto_imported > 0 ? ` (自动导入 ${s.auto_imported})` : '';
              const statusLabel =
                run.status === 'success' ? '成功' :
                run.status === 'failed' ? '失败' : run.status;
              const statusColor =
                run.status === 'success' ? '#16a34a' :
                run.status === 'failed' ? '#dc2626' : '#d97706';
              return (
                <div key={run.run_id} style={{ border: '1px solid #e2e8f0', borderRadius: '6px', padding: '8px', fontSize: '13px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                    <strong>{run.workflow === 'dinsar' ? 'D-InSAR' : run.workflow === 'dinsar_custom' ? 'D-InSAR (自定义)' : 'Import'}</strong>
                    <span style={{ color: statusColor, fontWeight: 500 }}>{statusLabel}</span>
                    <span style={{ color: '#475569' }}>{run.duration_seconds}s</span>
                    <button
                      type="button"
                      onClick={() => handleOpenLog(run.run_id)}
                      style={{ padding: '2px 8px', borderRadius: '4px', border: '1px solid #cbd5e1', background: 'transparent', color: '#64748b', fontSize: '11px', cursor: 'pointer' }}
                      title="查看日志"
                    >
                      日志
                    </button>
                    {!readOnly && (
                      <button
                        type="button"
                        onClick={async () => {
                          try {
                            await deleteRun(run.run_id);
                            setRecentRuns((prev) => prev.filter((r) => r.run_id !== run.run_id));
                          } catch (err) {
                            setMessage(`删除失败: ${err.response?.data?.detail || err.message}`);
                          }
                        }}
                        style={{ marginLeft: 'auto', padding: '2px 8px', borderRadius: '4px', border: '1px solid #e2e8f0', background: 'transparent', color: '#94a3b8', fontSize: '11px', cursor: 'pointer' }}
                        title="删除运行记录"
                      >
                        ✕
                      </button>
                    )}
                  </div>
                  {counts && (
                    <div style={{ fontSize: '12px', color: '#475569', marginTop: '2px' }}>
                      {counts}{autoImported}
                    </div>
                  )}
                  <div style={{ fontSize: '11px', color: '#94a3b8', marginTop: '2px' }}>
                    {run.finished_at || run.started_at || '-'}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {message && (
        <div style={{ ...cardStyle, borderColor: '#cbd5e1', background: '#f8fafc', marginBottom: 0 }}>
          <strong>消息:</strong> {message}
        </div>
      )}

      {/* 统计图表 */}
      {(recentRuns.length > 0 || chartStats) && (() => {
        // 图表1：近期运行成功/失败（来自 recentRuns）
        const successCount = recentRuns.filter(r => r.status === 'success').length;
        const failedCount = recentRuns.filter(r => r.status === 'failed').length;
        const otherCount = recentRuns.length - successCount - failedCount;
        const doughnutData = {
          labels: ['成功', '失败', '其他'],
          datasets: [{ data: [successCount, failedCount, otherCount], backgroundColor: ['#16a34a', '#dc2626', '#94a3b8'] }],
        };

        // 图表2：各工作流平均耗时
        const avgDur = chartStats?.idl_processing_stats?.avg_duration_by_workflow || {};
        const wfLabels = { import: 'Import', dinsar: 'D-InSAR', dinsar_custom: 'D-InSAR 自定义' };
        const durKeys = Object.keys(avgDur);
        const barDurData = {
          labels: durKeys.map(k => wfLabels[k] || k),
          datasets: [{ label: '平均耗时 (s)', data: durKeys.map(k => avgDur[k]), backgroundColor: '#818cf8' }],
        };

        // 图表3：D-InSAR 结果按月入库
        const byMonth = chartStats?.dinsar_by_month || [];
        const barMonthData = {
          labels: byMonth.map(d => d.month),
          datasets: [{ label: '入库数量', data: byMonth.map(d => d.count), backgroundColor: '#34d399' }],
        };

        const miniOpts = { responsive: true, plugins: { legend: { display: false }, title: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } };
        const doughnutOpts = { responsive: true, plugins: { legend: { position: 'bottom', labels: { font: { size: 11 } } } } };

        return (
          <div style={cardStyle}>
            <h4 style={{ marginTop: 0, marginBottom: '12px' }}>处理统计</h4>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '16px', alignItems: 'start' }}>
              <div>
                <div style={{ fontSize: '12px', color: '#64748b', marginBottom: '6px', textAlign: 'center' }}>近期运行结果</div>
                <Doughnut data={doughnutData} options={doughnutOpts} />
              </div>
              {durKeys.length > 0 && (
                <div>
                  <div style={{ fontSize: '12px', color: '#64748b', marginBottom: '6px', textAlign: 'center' }}>平均耗时 (秒)</div>
                  <Bar data={barDurData} options={miniOpts} />
                </div>
              )}
              {byMonth.length > 0 && (
                <div>
                  <div style={{ fontSize: '12px', color: '#64748b', marginBottom: '6px', textAlign: 'center' }}>结果入库趋势</div>
                  <Bar data={barMonthData} options={miniOpts} />
                </div>
              )}
            </div>
          </div>
        );
      })()}

      {/* 日志 Modal */}
      {logModal.open && (
        <div
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          onClick={() => setLogModal((s) => ({ ...s, open: false }))}
        >
          <div
            style={{ background: '#1e293b', borderRadius: '8px', width: '700px', maxWidth: '95vw', maxHeight: '75vh', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ padding: '12px 16px', borderBottom: '1px solid #334155', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ color: '#e2e8f0', fontWeight: 600, fontSize: '13px', fontFamily: 'monospace' }}>{logModal.runId}.log</span>
              <button
                type="button"
                onClick={() => setLogModal((s) => ({ ...s, open: false }))}
                style={{ background: 'transparent', border: 'none', color: '#94a3b8', fontSize: '16px', cursor: 'pointer', lineHeight: 1 }}
              >✕</button>
            </div>
            <div style={{ flex: 1, overflowY: 'auto', padding: '12px 16px' }}>
              {logModal.loading ? (
                <div style={{ color: '#94a3b8', fontSize: '13px' }}>加载中...</div>
              ) : (
                <pre style={{ margin: 0, color: '#cbd5e1', fontSize: '11px', lineHeight: 1.6, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                  {logModal.content}
                </pre>
              )}
            </div>
            <div style={{ padding: '8px 16px', borderTop: '1px solid #334155', fontSize: '11px', color: '#64748b' }}>
              {logModal.sizeBytes > 0 && `文件大小: ${(logModal.sizeBytes / 1024).toFixed(1)} KB`}
              {logModal.truncated && ' · 已截断，仅显示末尾 200KB'}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default IDLAutomationPanel;
