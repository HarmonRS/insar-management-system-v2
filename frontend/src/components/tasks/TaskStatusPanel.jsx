import { getTaskTypeLabel } from '../../config/taskUiPolicies';

const toneColor = {
  active: {
    border: '#f59e0b',
    bg: '#fffbeb',
    text: '#92400e',
    fill: '#f59e0b',
  },
  recent: {
    border: '#93c5fd',
    bg: '#eff6ff',
    text: '#1d4ed8',
    fill: '#3b82f6',
  },
  idle: {
    border: '#e2e8f0',
    bg: '#f8fafc',
    text: '#64748b',
    fill: '#94a3b8',
  },
  error: {
    border: '#fecaca',
    bg: '#fef2f2',
    text: '#b91c1c',
    fill: '#dc2626',
  },
};

const normalizeProgress = (value) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return null;
  return Math.max(0, Math.min(100, numeric));
};

const isFailed = (task) => String(task?.status || '').toUpperCase() === 'FAILED';

export default function TaskStatusPanel({
  title = '任务状态',
  activeTasks = [],
  recentTasks = [],
  latestTask = null,
  isBusy = false,
  idleText = '当前没有正在执行的相关任务。',
  compact = false,
  action = null,
  footer = null,
}) {
  const task = latestTask || activeTasks[0] || recentTasks[0] || null;
  const showingRecent = !isBusy && !!task;
  const tone = task ? (isFailed(task) ? 'error' : (showingRecent ? 'recent' : 'active')) : 'idle';
  const colors = toneColor[tone];
  const progress = normalizeProgress(task?.progress);

  return (
    <div
      className="task-status-panel"
      style={{
        padding: compact ? 10 : 12,
        borderRadius: 8,
        border: `1px solid ${colors.border}`,
        background: colors.bg,
        marginBottom: 12,
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10 }}>
        <div>
          <strong style={{ color: colors.text, fontSize: compact ? 13 : 14 }}>{title}</strong>
          {task && (
            <span style={{ marginLeft: 8, color: colors.text, fontSize: 12 }}>
              {showingRecent ? '最近一次' : '运行中'}
            </span>
          )}
        </div>
        {action}
      </div>

      {!task ? (
        <div style={{ marginTop: 7, color: colors.text, fontSize: 12 }}>{idleText}</div>
      ) : (
        <>
          <div style={{ marginTop: 8, color: colors.text, fontSize: 12, wordBreak: 'break-all' }}>
            <span style={{ fontWeight: 700 }}>{getTaskTypeLabel(task.task_type)}</span>
            <span style={{ margin: '0 6px' }}>·</span>
            <span>{String(task.status || '-').toUpperCase()}</span>
            {task.task_id && (
              <>
                <span style={{ margin: '0 6px' }}>·</span>
                <code style={{ fontSize: 11 }}>{task.task_id}</code>
              </>
            )}
          </div>
          <div style={{ marginTop: 5, color: colors.text, fontSize: 12, wordBreak: 'break-word' }}>
            {task.message || '-'}
          </div>
          {progress !== null && (
            <div style={{ marginTop: 7, display: 'flex', alignItems: 'center', gap: 8 }}>
              <div style={{ flex: 1, height: 7, background: '#ffffff', borderRadius: 999, overflow: 'hidden' }}>
                <div
                  style={{
                    height: '100%',
                    width: `${progress}%`,
                    background: colors.fill,
                    transition: 'width 0.25s ease',
                  }}
                />
              </div>
              <span style={{ color: colors.text, fontSize: 11, fontVariantNumeric: 'tabular-nums' }}>
                {Math.round(progress)}%
              </span>
            </div>
          )}
        </>
      )}
      {footer && <div style={{ marginTop: 7 }}>{footer}</div>}
    </div>
  );
}
