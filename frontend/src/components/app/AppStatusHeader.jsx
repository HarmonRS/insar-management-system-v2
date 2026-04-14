import { memo } from 'react';
import { formatUtc, getStatusClass } from '../../utils/appUiHelpers';

function AppStatusHeader({
    language,
    setLanguage,
    currentUser,
    isAdmin,
    isReadOnlyUser,
    activeTasks,
    avgTaskProgress,
    licenseStatus,
    healthStatus,
    healthLoading,
    healthError,
    onRefreshHealth,
    onLogout,
}) {
    const hasHealthStatus = !!healthStatus;
    const licenseOk = !!licenseStatus?.ok;
    const dbOk = !!(healthStatus?.database?.ok && healthStatus?.database?.schema_ok && healthStatus?.database?.postgis_ok);
    const workerOk = !!healthStatus?.worker?.ok;
    const idlOk = !!healthStatus?.idl?.ok;
    const aiOk = !!healthStatus?.ollama?.ok;
    const nginxOk = !!healthStatus?.nginx?.ok;

    return (
        <>
            <div className="top-status-bar">
                <div className="status-brand">
                    <div className="brand-title">InSAR 自动化管理系统</div>
                    <div className="brand-subtitle">科研工程模式 · {licenseOk ? '已授权' : '未授权'}</div>
                </div>
                <div className="status-items">
                    <div className="status-item">
                        <span className={`status-dot ${getStatusClass(dbOk, hasHealthStatus)}`}></span>DB
                    </div>
                    <div className="status-item">
                        <span className={`status-dot ${getStatusClass(workerOk, hasHealthStatus)}`}></span>Worker
                    </div>
                    <div className="status-item">
                        <span className={`status-dot ${getStatusClass(idlOk, hasHealthStatus)}`}></span>IDL
                    </div>
                    <div className="status-item">
                        <span className={`status-dot ${getStatusClass(aiOk, hasHealthStatus)}`}></span>Ollama
                    </div>
                    <div className="status-item">
                        <span className={`status-dot ${getStatusClass(nginxOk, hasHealthStatus)}`}></span>Nginx
                    </div>
                </div>
                <div className="status-actions">
                    <div className="status-lang-switch" data-no-i18n="true">
                        <button
                            className={`status-lang-btn ${language === 'zh' ? 'active' : ''}`}
                            onClick={() => setLanguage('zh')}
                            title="切换到中文"
                        >
                            中文
                        </button>
                        <button
                            className={`status-lang-btn ${language === 'en' ? 'active' : ''}`}
                            onClick={() => setLanguage('en')}
                            title="Switch to English"
                        >
                            EN
                        </button>
                    </div>
                    <div className={`user-role-chip ${isAdmin ? 'admin' : 'viewer'}`}>
                        <span className="user-role-name">{currentUser.username}</span>
                        <span className="user-role-divider">·</span>
                        <span>{isAdmin ? '管理员' : '只读账号'}</span>
                    </div>
                    <div className={`status-task ${activeTasks.length > 0 ? 'has-active-tasks' : ''}`}>
                        <span>{activeTasks.length > 0 ? `⚙️ 运行中 ${activeTasks.length}` : '✓ 空闲'}</span>
                        {activeTasks.length > 0 && (
                            <div className="status-task-bar">
                                <div className="status-task-fill" style={{ width: `${avgTaskProgress}%` }}></div>
                            </div>
                        )}
                    </div>
                    {licenseStatus?.expires_at && (
                        <div className="status-license">
                            授权至 {formatUtc(licenseStatus.expires_at, language)}
                        </div>
                    )}
                    <button
                        className="status-refresh"
                        onClick={onRefreshHealth}
                        disabled={healthLoading}
                        title={healthError || '刷新自检状态'}
                    >
                        {healthLoading ? '自检中...' : '刷新自检'}
                    </button>
                    <button
                        className="status-refresh"
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
