import { memo } from 'react';
import defaultLogoUrl from '../../logo.jpg';
import { formatUtc } from '../../utils/appUiHelpers';

const ORGANIZATION_NAME = import.meta.env.VITE_APP_ORG_NAME || '黑龙江省自然资源卫星应用技术中心';
const SYSTEM_NAME = import.meta.env.VITE_APP_SYSTEM_NAME || 'InSAR 自动化管理系统';
const SYSTEM_TAGLINE = import.meta.env.VITE_APP_SYSTEM_TAGLINE || '科研工程生产平台';
const LOGO_URL = import.meta.env.VITE_APP_LOGO_URL || defaultLogoUrl;

function AppStatusHeader({
  language,
  currentUser,
  isAdmin,
  isReadOnlyUser,
  activeTasks,
  avgTaskProgress,
  licenseStatus,
  onLogout,
}) {
  const licenseOk = !!licenseStatus?.ok;
  const hasActiveTasks = activeTasks.length > 0;

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
          <div className={`status-task ${hasActiveTasks ? 'has-active-tasks' : ''}`}>
            <span>{hasActiveTasks ? `运行中 ${activeTasks.length}` : '任务空闲'}</span>
            {hasActiveTasks && (
              <div className="status-task-bar" aria-hidden="true">
                <div className="status-task-fill" style={{ width: `${avgTaskProgress}%` }} />
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
