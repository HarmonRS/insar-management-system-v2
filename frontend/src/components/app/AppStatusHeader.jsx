import { memo } from 'react';
import defaultLogoUrl from '../../logo.jpg';
import { formatUtc } from '../../utils/appUiHelpers';

const ORGANIZATION_NAME = import.meta.env.VITE_APP_ORG_NAME || '黑龙江省自然资源卫星应用技术中心';
const SYSTEM_NAME = import.meta.env.VITE_APP_SYSTEM_NAME || '雷达数据生产管理系统';
const SYSTEM_TAGLINE = import.meta.env.VITE_APP_SYSTEM_TAGLINE || '科研工程生产平台';
const LOGO_URL = import.meta.env.VITE_APP_LOGO_URL || defaultLogoUrl;

function AppStatusHeader({
  language,
  currentUser,
  isAdmin,
  isReadOnlyUser,
  activeTasks,
  avgTaskProgress,
  runtimeSummary,
  licenseStatus,
  onLogout,
}) {
  const licenseOk = !!licenseStatus?.ok;
  const worker = runtimeSummary?.worker || {};
  const jobs = runtimeSummary?.jobs || {};
  const scan = runtimeSummary?.scan || {};
  const workerCount = Number(worker.worker_count) || 0;
  const runningJobs = Number(jobs.running_count) || 0;
  const queuedJobs = Number(jobs.queued_count) || 0;
  const scanJobs = Number(scan.active_job_count) || 0;
  const scanRunningJobs = Number(scan.running_job_count) || 0;
  const staleJobs = Number(worker.stale_running_job_count) || 0;
  const hasRuntimeActivity = activeTasks.length > 0 || runningJobs > 0 || queuedJobs > 0;
  const taskProgress = hasRuntimeActivity ? avgTaskProgress : 0;
  let runtimeLabel = 'Worker 未连接';
  if (!runtimeSummary && activeTasks.length > 0) {
    runtimeLabel = `运行中 ${activeTasks.length}`;
  } else if (runningJobs > 0 && staleJobs > 0) {
    runtimeLabel = `运行态待恢复 ${staleJobs}`;
  } else if (runningJobs > 0) {
    runtimeLabel = `执行中 ${runningJobs}`;
  } else if (queuedJobs > 0) {
    runtimeLabel = `排队 ${queuedJobs}`;
  } else if (workerCount > 0) {
    runtimeLabel = `Worker ${workerCount} 空闲`;
  }
  const runtimeDetail = !runtimeSummary && activeTasks.length > 0
    ? '任务状态来自兼容接口'
    : workerCount > 0
    ? `Worker ${workerCount}`
    : '无在线 worker';
  const staleDetail = staleJobs > 0 ? ` · 待恢复 ${staleJobs}` : '';

  return (
    <>
      <div className="top-status-bar">
        <div className="status-brand">
          {LOGO_URL && (
            <img
              className="status-brand-logo"
              src={LOGO_URL}
              alt={`${ORGANIZATION_NAME} logo`}
            />
          )}
          <div className="status-brand-copy">
            <div className="brand-org">{ORGANIZATION_NAME}</div>
            <div className="brand-title">{SYSTEM_NAME}</div>
          </div>
        </div>

        <div className="status-system-meta" aria-label="系统状态摘要">
          <span>{SYSTEM_TAGLINE}</span>
          <span className={`status-license-chip ${licenseOk ? 'ok' : 'fail'}`}>
            {licenseOk ? '已授权' : '未授权'}
          </span>
          {licenseStatus?.expires_at && (
            <span className="status-license">
              授权至 {formatUtc(licenseStatus.expires_at, language)}
            </span>
          )}
        </div>

        <div className="status-actions">
          <div className={`status-task ${hasRuntimeActivity ? 'has-active-tasks' : ''}`}>
            <span>{runtimeLabel}</span>
            <small>{runtimeDetail}{staleDetail}{scanJobs > 0 ? ` · 扫描 ${scanRunningJobs}/${scanJobs}` : ''}</small>
            {hasRuntimeActivity && (
              <div className="status-task-bar" aria-hidden="true">
                <div className="status-task-fill" style={{ width: `${taskProgress}%` }} />
              </div>
            )}
          </div>
          <div className={`user-role-chip ${isAdmin ? 'admin' : 'viewer'}`}>
            <span className="user-role-name">{currentUser.username}</span>
            <span className="user-role-divider">·</span>
            <span>{isAdmin ? '管理员' : '只读账号'}</span>
          </div>
          <button
            className="status-refresh"
            type="button"
            onClick={onLogout}
            title="退出登录"
          >
            退出登录
          </button>
        </div>
      </div>
      {isReadOnlyUser && (
        <div className="read-only-banner">
          当前账号为只读权限：可查看数据与状态，但不能执行写操作。
        </div>
      )}
    </>
  );
}

export default memo(AppStatusHeader);
