import { useState } from 'react';
import { getTaskTypeLabel } from '../config/taskUiPolicies';

export default function GlobalTaskCenter({
  isVisible,
  activeTasks,
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
  if (!isVisible || activeTasks.length === 0) {
    return null;
  }

  const activeCount = activeTasks.length;
  const avgProgress = Math.round(
    activeTasks.reduce((sum, task) => sum + (Number(task.progress) || 0), 0) / Math.max(1, activeCount)
  );

  return (
    <div className="global-task-overlay">
      {!expanded && (
        <button className="task-center-button" onClick={() => setExpanded(true)}>
          <span className="task-center-dot" />
          <span>后台任务 {activeCount}</span>
          <strong>{avgProgress}%</strong>
        </button>
      )}
      {expanded && (
        <div className="overlay-content">
          <div className="task-center-header">
            <div>
              <h3>后台任务</h3>
              <p>任务正在执行，你可以继续使用其他功能；同类重复提交由系统限制。</p>
            </div>
            <button className="task-center-close" onClick={() => setExpanded(false)} aria-label="关闭任务中心">
              ×
            </button>
          </div>
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
                </>
              );
            })()}
          </div>
          <p className="overlay-footer-hint">任务中心只展示状态，不再锁定整个界面。需要互斥的操作由功能页按钮和后端任务冲突检查处理。</p>
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
