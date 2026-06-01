const getTaskTypeLabel = (taskType) => {
  if (taskType?.startsWith('WATER_GEOCODE_')) return '水体地理编码';
  if (taskType?.startsWith('WATER_FLOOD_')) return '洪涝检测';
  switch (taskType) {
    case 'SCAN_DATA':
      return '同步源数据';
    case 'SCAN_DINSAR':
      return '扫描结果与自愈';
    case 'AI_TRAIN':
      return '训练AI模型';
    case 'AI_PREDICT':
      return '全量质量评估';
    case 'AI_ANALYZE':
      return 'AI 智能诊断';
    case 'AI_WARMUP':
      return 'AI 模型预热';
    case 'COPY_DATA':
      return '数据分发拷贝';
    case 'SCAN_HAZARD':
      return '灾害点同步';
    case 'UNPACK_ARCHIVES':
      return 'LT-1 解包';
    case 'UNPACK_SENTINEL1':
      return 'Sentinel-1 解包';
    case 'GF3_UNPACK':
      return 'GF3 解包';
    case 'GF3_BATCH_PROCESS':
      return 'GF3 预处理';
    case 'GF3_SARSCAPE_PRODUCE':
      return 'GF3 SARscape 生产';
    case 'GF3_SARSCAPE_SYNC':
      return 'GF3 SARscape 入库';
    case 'GF3_SARSCAPE_CLEAN':
      return 'GF3 中间清理';
    case 'SCAN_ASSET_INVENTORY':
      return '资产库存扫描';
    case 'IDL_IMPORT':
      return 'ENVI 数据导入';
    case 'IDL_DINSAR':
      return 'ENVI D-InSAR 生产';
    default:
      return taskType;
  }
};

export default function ActiveTasksOverlay({
  isVisible,
  activeTasks,
  t,
  isAdmin,
  showForceUnlock,
  forceUnlockPwd,
  onShowForceUnlock,
  onForceUnlockPwdChange,
  onForceUnlockConfirm,
  onCancelForceUnlock,
}) {
  if (!isVisible) {
    return null;
  }

  return (
    <div className="global-task-overlay">
      <div className="overlay-content">
        <div className="loading-spinner-large"></div>
        <h3>系统任务执行中</h3>
        <div className="active-tasks-container">
          {(() => {
            const waterTasks = activeTasks.filter(t =>
              t.task_type?.startsWith('WATER_GEOCODE_') || t.task_type?.startsWith('WATER_FLOOD_')
            );
            const otherTasks = activeTasks.filter(t =>
              !t.task_type?.startsWith('WATER_GEOCODE_') && !t.task_type?.startsWith('WATER_FLOOD_')
            );
            const waterDone = waterTasks.filter(t => t.progress >= 100).length;
            return (
              <>
                {otherTasks.map((task) => (
                  <div key={task.task_id} className="task-progress-item">
                    <div className="task-info-row">
                      <span className="task-label">{getTaskTypeLabel(task.task_type)}</span>
                      <span className="task-percent">{task.progress}%</span>
                    </div>
                    <div className="task-progress-bar">
                      <div className="task-progress-fill" style={{ width: `${task.progress}%` }}></div>
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
        <p className="overlay-footer-hint">为了保证数据一致性，耗时任务执行期间 UI 已锁定。任务完成后将自动刷新页面数据。</p>
        {isAdmin && (
          <div style={{ marginTop: '16px', textAlign: 'center' }}>
            {!showForceUnlock ? (
              <button
                onClick={onShowForceUnlock}
                style={{
                  padding: '6px 16px',
                  borderRadius: '6px',
                  border: '1px solid rgba(255,255,255,0.4)',
                  background: 'rgba(255,255,255,0.1)',
                  color: '#fff',
                  fontSize: '12px',
                  cursor: 'pointer',
                }}
              >
                管理员强制解锁
              </button>
            ) : (
              <div style={{ display: 'inline-flex', alignItems: 'center', gap: '8px' }}>
                <input
                  type="password"
                  placeholder="输入管理员密码"
                  value={forceUnlockPwd}
                  onChange={(e) => onForceUnlockPwdChange(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      onForceUnlockConfirm();
                    }
                  }}
                  style={{
                    padding: '5px 10px',
                    fontSize: '12px',
                    borderRadius: '4px',
                    border: '1px solid rgba(255,255,255,0.4)',
                    background: 'rgba(255,255,255,0.15)',
                    color: '#fff',
                    width: '160px',
                    outline: 'none',
                  }}
                />
                <button
                  disabled={!forceUnlockPwd}
                  onClick={onForceUnlockConfirm}
                  style={{
                    padding: '5px 14px',
                    borderRadius: '4px',
                    border: '1px solid #dc2626',
                    background: '#dc2626',
                    color: '#fff',
                    fontSize: '12px',
                    cursor: forceUnlockPwd ? 'pointer' : 'not-allowed',
                    opacity: forceUnlockPwd ? 1 : 0.5,
                  }}
                >
                  确认解锁
                </button>
                <button
                  onClick={onCancelForceUnlock}
                  style={{
                    padding: '5px 10px',
                    borderRadius: '4px',
                    border: '1px solid rgba(255,255,255,0.4)',
                    background: 'transparent',
                    color: '#fff',
                    fontSize: '12px',
                    cursor: 'pointer',
                  }}
                >
                  取消
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
