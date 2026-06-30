import { useState } from 'react';
import { getTaskTypeLabel } from '../config/taskUiPolicies';

export default function GlobalTaskCenter({
  isVisible,
  activeTasks,
  runtimeSummary,
  t,
  isAdmin,
  showCancelTask,
  cancelTaskPwd,
  onShowCancelTask,
  onCancelTaskPwdChange,
  onCancelTaskConfirm,
  onCloseCancelTask,
}) {
  const [expanded, setExpanded] = useState(false);
  const jobs = runtimeSummary?.jobs || {};
  const worker = runtimeSummary?.worker || {};
  const scan = runtimeSummary?.scan || {};
  const activeJobs = Array.isArray(jobs.items) ? jobs.items : [];
  const activeCount = Math.max(
    activeTasks.length,
    Number(jobs.active_count) || 0,
  );
  const workerCount = Number(worker.worker_count) || 0;
  const queuedJobs = Number(jobs.queued_count) || 0;
  const runningJobs = Number(jobs.running_count) || 0;
  const scanJobCount = Number(scan.active_job_count) || 0;

  if (!isVisible || activeCount === 0) {
    return null;
  }

  const avgProgress = Math.round(
    activeTasks.reduce((sum, task) => sum + (Number(task.progress) || 0), 0) / Math.max(1, activeCount)
  );
  const visibleJobs = activeJobs.slice(0, 8);

  return (
    <div className="global-task-overlay">
      {!expanded && (
        <button className="task-center-button" onClick={() => setExpanded(true)}>
          <span className="task-center-dot" />
          <span>后台任务 {activeCount}</span>
          <strong>{runningJobs > 0 ? `执行 ${runningJobs}` : `${avgProgress}%`}</strong>
        </button>
      )}
      {expanded && (
        <div className="overlay-content">
          <div className="task-center-header">
            <div>
              <h3>后台任务</h3>
              <p>展示 Worker、执行中 Job、排队 Job 和任务进度；同类重复提交由后端冲突检查处理。</p>
            </div>
            <button className="task-center-close" onClick={() => setExpanded(false)} aria-label="关闭任务中心">
              ×
            </button>
          </div>
          <div className="task-runtime-summary">
            <div>
              <span>Worker</span>
              <strong>{workerCount}</strong>
            </div>
            <div>
              <span>执行中</span>
              <strong>{runningJobs}</strong>
            </div>
            <div>
              <span>排队</span>
              <strong>{queuedJobs}</strong>
            </div>
            <div>
              <span>扫描</span>
              <strong>{scanJobCount}</strong>
            </div>
          </div>
          {visibleJobs.length > 0 && (
            <div className="active-jobs-container">
              {visibleJobs.map((job) => (
                <div key={job.job_id} className="job-runtime-row">
                  <span className={`job-status-chip ${String(job.status || '').toLowerCase()}`}>{job.status || '-'}</span>
                  <span className="job-runtime-title">
                    {getTaskTypeLabel(job.task_type || job.job_type)}
                  </span>
                  <span className="job-runtime-worker" title={job.locked_by || ''}>
                    {job.locked_by ? `Worker ${job.locked_by}` : (job.status === 'RETRY' ? '等待重试' : '等待领取')}
                  </span>
                </div>
              ))}
            </div>
          )}
          <div className="active-tasks-container">
            {(() => {
              const waterTasks = activeTasks.filter(task =>
                task.task_type?.startsWith('WATER_GEOCODE_') || task.task_type?.startsWith('WATER_FLOOD_')
              );
              const otherTasks = activeTasks.filter(task =>
                !task.task_type?.startsWith('WATER_GEOCODE_') && !task.task_type?.startsWith('WATER_FLOOD_')
              );
              const waterDone = waterTasks.filter(task => task.progress >= 100).length;
              return (
                <>
                  {otherTasks.map((task) => (
                    <div key={task.task_id} className="task-progress-item">
                      <div className="task-info-row">
                        <span className="task-label">{getTaskTypeLabel(task.task_type)}</span>
                        <span className="task-percent">{Number(task.progress) || 0}%</span>
                      </div>
                      <div className="task-progress-bar">
                        <div className="task-progress-fill" style={{ width: `${Number(task.progress) || 0}%` }}></div>
                      </div>
                      <p className="task-status-msg">{t(task.message || '')}</p>
                    </div>
                  ))}
                  {waterTasks.length > 0 && (
                    <div className="task-progress-item">
                      <div className="task-info-row">
                        <span className="task-label">水体处理（剩余 {waterTasks.length - waterDone} 景）</span>
                      </div>
                    </div>
                  )}
                  {otherTasks.length === 0 && waterTasks.length === 0 && visibleJobs.length > 0 && (
                    <div className="task-progress-item task-progress-item--muted">
                      <p className="task-status-msg">当前只有 Job 运行态，任务进度尚未写入 system_tasks。</p>
                    </div>
                  )}
                </>
              );
            })()}
          </div>
          <p className="overlay-footer-hint">取消按钮只作用于可跟踪的 Task；纯 Job 取消需要在对应功能页或运维接口处理。</p>
          {isAdmin && (
            <div style={{ marginTop: '16px', textAlign: 'center' }}>
              {!showCancelTask ? (
                <button
                  onClick={onShowCancelTask}
                  style={{
                    padding: '6px 16px',
                    borderRadius: '6px',
                    border: '1px solid #fecaca',
                    background: '#fff1f2',
                    color: '#b91c1c',
                    fontSize: '12px',
                    cursor: 'pointer',
                  }}
                >
                  管理员取消任务
                </button>
              ) : (
                <div style={{ display: 'inline-flex', alignItems: 'center', gap: '8px' }}>
                  <input
                    type="password"
                    placeholder="输入管理员密码"
                    value={cancelTaskPwd}
                    onChange={(e) => onCancelTaskPwdChange(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        onCancelTaskConfirm();
                      }
                    }}
                    style={{
                      padding: '5px 10px',
                      fontSize: '12px',
                      borderRadius: '4px',
                      border: '1px solid #cbd5e1',
                      background: '#ffffff',
                      color: '#0f172a',
                      width: '160px',
                      outline: 'none',
                    }}
                  />
                  <button
                    disabled={!cancelTaskPwd}
                    onClick={onCancelTaskConfirm}
                    style={{
                      padding: '5px 14px',
                      borderRadius: '4px',
                      border: '1px solid #dc2626',
                      background: '#dc2626',
                      color: '#fff',
                      fontSize: '12px',
                      cursor: cancelTaskPwd ? 'pointer' : 'not-allowed',
                      opacity: cancelTaskPwd ? 1 : 0.5,
                    }}
                  >
                    确认取消
                  </button>
                  <button
                    onClick={onCloseCancelTask}
                    style={{
                      padding: '5px 10px',
                      borderRadius: '4px',
                      border: '1px solid #cbd5e1',
                      background: '#ffffff',
                      color: '#334155',
                      fontSize: '12px',
                      cursor: 'pointer',
                    }}
                  >
                    关闭
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
