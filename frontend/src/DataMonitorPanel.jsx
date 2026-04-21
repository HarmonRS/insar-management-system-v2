import React, { useEffect, useRef, useState } from 'react';
import './App.css';
import { useI18n } from './i18n/I18nContext';

const DEFAULT_MONITOR_CONFIG = {
  radar_dirs: [],
  orbit_dir: '',
  dinsar_dirs: [],
  gf3_source_dirs: [],
  gf3_storage_dirs: [],
};

const DEFAULT_UNPACK_CONFIG = {
  source_dirs: [],
  insar_storage_dirs: [],
  min_disk_space_gb: 50,
  max_files_per_run: 0,
  max_runtime_minutes: 0,
  delete_archive: true,
  tmp_suffix: '.unpack_tmp',
  archive_exts: [],
};

const toArray = (value) => (Array.isArray(value) ? value : []);

const createUnpackRunOptions = (config = DEFAULT_UNPACK_CONFIG) => ({
  max_files_per_run: String(config?.max_files_per_run ?? 0),
  max_runtime_minutes: String(config?.max_runtime_minutes ?? 0),
});

const parseUnpackRunValue = (rawValue, label) => {
  const value = String(rawValue ?? '').trim();
  if (!value) {
    return 0;
  }

  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < 0) {
    throw new Error(`${label}必须是大于等于 0 的整数`);
  }
  return parsed;
};

const formatList = (list) => (Array.isArray(list) && list.length ? list.join('; ') : '未配置');

const DataMonitorPanel = ({ apiEndpoint, onTaskStart, readOnly = false, enabled = true }) => {
  const { t } = useI18n();
  const [config, setConfig] = useState(DEFAULT_MONITOR_CONFIG);
  const [configLoaded, setConfigLoaded] = useState(false);
  const [logs, setLogs] = useState([]);
  const [activeTasks, setActiveTasks] = useState([]);
  const [unpackConfig, setUnpackConfig] = useState(DEFAULT_UNPACK_CONFIG);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [unpackLoading, setUnpackLoading] = useState(false);
  const [unpackMessage, setUnpackMessage] = useState('');
  const [showUnpackDialog, setShowUnpackDialog] = useState(false);
  const [unpackRunOptions, setUnpackRunOptions] = useState(() => createUnpackRunOptions());
  const [unpackDialogError, setUnpackDialogError] = useState('');
  const [unpackTaskId, setUnpackTaskId] = useState('');
  const [unpackTaskTerminal, setUnpackTaskTerminal] = useState(false);
  const [gf3Loading, setGf3Loading] = useState(false);
  const [gf3Message, setGf3Message] = useState('');
  const logEndRef = useRef(null);

  const parseJsonSafe = async (response, fallback) => {
    try {
      return await response.json();
    } catch {
      return fallback;
    }
  };

  const displayLogs = toArray(logs);
  const displayActiveTasks = toArray(activeTasks);
  const unpackActiveTask = displayActiveTasks.find((task) =>
    task.task_id === unpackTaskId || task.task_type === 'UNPACK_ARCHIVES'
  );

  useEffect(() => {
    if (!enabled) {
      setConfig(DEFAULT_MONITOR_CONFIG);
      setConfigLoaded(false);
      return;
    }

    let canceled = false;
    fetch(`${apiEndpoint}/monitor/status`, { credentials: 'include' })
      .then(async (res) => {
        const data = await parseJsonSafe(res, {});
        if (!res.ok) {
          throw new Error(data?.detail || `HTTP ${res.status}`);
        }
        return data;
      })
      .then((data) => {
        if (canceled) {
          return;
        }
        setConfig({
          ...DEFAULT_MONITOR_CONFIG,
          ...data,
          radar_dirs: toArray(data?.radar_dirs),
          dinsar_dirs: toArray(data?.dinsar_dirs),
          gf3_source_dirs: toArray(data?.gf3_source_dirs),
          gf3_storage_dirs: toArray(data?.gf3_storage_dirs),
        });
        setConfigLoaded(true);
      })
      .catch((err) => {
        if (canceled) {
          return;
        }
        console.error('Failed to fetch monitor status:', err);
        setConfigLoaded(false);
      });

    return () => {
      canceled = true;
    };
  }, [apiEndpoint, enabled]);

  useEffect(() => {
    if (!enabled) {
      setUnpackConfig(DEFAULT_UNPACK_CONFIG);
      setShowUnpackDialog(false);
      setUnpackRunOptions(createUnpackRunOptions());
      setUnpackDialogError('');
      return;
    }

    let canceled = false;
    fetch(`${apiEndpoint}/unpack/config`, { credentials: 'include' })
      .then(async (res) => {
        const data = await parseJsonSafe(res, {});
        if (!res.ok) {
          throw new Error(data?.detail || `HTTP ${res.status}`);
        }
        return data;
      })
      .then((data) => {
        if (canceled) {
          return;
        }
        setUnpackConfig({
          ...DEFAULT_UNPACK_CONFIG,
          ...data,
          source_dirs: toArray(data?.source_dirs),
          insar_storage_dirs: toArray(data?.insar_storage_dirs),
        });
      })
      .catch((err) => {
        if (canceled) {
          return;
        }
        console.error('Failed to fetch unpack config:', err);
      });

    return () => {
      canceled = true;
    };
  }, [apiEndpoint, enabled]);

  useEffect(() => {
    if (!enabled) {
      setLogs([]);
      setActiveTasks([]);
      setUnpackTaskId('');
      setUnpackTaskTerminal(false);
      return;
    }

    let canceled = false;
    const fetchLogsAndTasks = async () => {
      try {
        const [logsRes, tasksRes] = await Promise.all([
          fetch(`${apiEndpoint}/monitor/logs`, { credentials: 'include' }),
          fetch(`${apiEndpoint}/tasks/active`, { credentials: 'include' }),
        ]);
        const [logsData, tasksData] = await Promise.all([
          parseJsonSafe(logsRes, {}),
          parseJsonSafe(tasksRes, []),
        ]);

        if (canceled) {
          return;
        }
        setLogs(logsRes.ok ? toArray(logsData?.logs) : []);
        setActiveTasks(tasksRes.ok ? toArray(tasksData) : []);
      } catch (err) {
        if (canceled) {
          return;
        }
        console.error('Failed to fetch logs or tasks:', err);
      }
    };

    fetchLogsAndTasks();
    const intervalId = setInterval(fetchLogsAndTasks, 2000);

    return () => {
      canceled = true;
      clearInterval(intervalId);
    };
  }, [apiEndpoint, enabled]);

  useEffect(() => {
    if (!enabled) {
      setUnpackTaskId('');
      setUnpackTaskTerminal(false);
      return;
    }

    if (unpackActiveTask) {
      if (!unpackTaskId) {
        setUnpackTaskId(unpackActiveTask.task_id);
      }
      setUnpackTaskTerminal(false);
      setUnpackMessage(
        `运行中 (${unpackActiveTask.progress || 0}%): ${unpackActiveTask.message || '正在解包 LT-1 归档...'}`
      );
      return;
    }

    if (!unpackTaskId || unpackTaskTerminal) {
      return;
    }

    let canceled = false;
    fetch(`${apiEndpoint}/tasks/${unpackTaskId}`, { credentials: 'include' })
      .then(async (res) => {
        const data = await parseJsonSafe(res, null);
        if (!res.ok) {
          throw new Error(data?.detail || `HTTP ${res.status}`);
        }
        return data;
      })
      .then((task) => {
        if (canceled || !task) {
          return;
        }
        if (task.status === 'COMPLETED') {
          setUnpackMessage(task.message || 'LT-1 解包已完成。');
          setUnpackTaskTerminal(true);
        } else if (task.status === 'FAILED') {
          setUnpackMessage(`失败：${task.message || 'LT-1 解包任务失败'}`);
          setUnpackTaskTerminal(true);
        }
      })
      .catch((err) => {
        if (canceled) {
          return;
        }
        console.error('Failed to fetch unpack task status:', err);
      });

    return () => {
      canceled = true;
    };
  }, [apiEndpoint, enabled, unpackActiveTask, unpackTaskId, unpackTaskTerminal]);

  useEffect(() => {
    if (logEndRef.current) {
      logEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs]);

  const hasRadarDirs = config.radar_dirs.length > 0;
  const hasOrbitDir = typeof config.orbit_dir === 'string' && config.orbit_dir.trim() !== '';
  const hasDinsarDirs = config.dinsar_dirs.length > 0;
  const hasGf3SourceDirs = config.gf3_source_dirs.length > 0;
  const hasGf3StorageDirs = config.gf3_storage_dirs.length > 0;

  const canRunRadar = !readOnly && configLoaded && hasRadarDirs;
  const canRunOrbit = !readOnly && configLoaded && hasOrbitDir;
  const canRunDinsar = !readOnly && configLoaded && hasDinsarDirs;
  const canRunGf3Scan = !readOnly && configLoaded && hasGf3StorageDirs;
  const canRunGf3Process = !readOnly && configLoaded && hasGf3SourceDirs;
  const canOpenUnpackDialog = !readOnly && unpackConfig.source_dirs.length > 0;

  const handleRunNow = async (target) => {
    if (readOnly) {
      setMessage('当前账户为只读模式，无法触发扫描。');
      return;
    }

    setLoading(true);
    const targetMap = {
      radar: 'LT-1 数据',
      orbit: '精轨数据',
      dinsar: 'D-InSAR 结果',
      gf3: 'GF3 数据',
    };
    setMessage(`正在触发${targetMap[target] || '全部'}手动扫描...`);

    try {
      const url = target ? `${apiEndpoint}/monitor/run-now?target=${target}` : `${apiEndpoint}/monitor/run-now`;
      const res = await fetch(url, {
        method: 'POST',
        credentials: 'include',
      });
      const data = await parseJsonSafe(res, {});
      if (res.ok) {
        setMessage(data.message || '扫描任务已启动。');
        if (onTaskStart) {
          onTaskStart(data.task_id, `已触发${targetMap[target] || '全部'}手动扫描...`);
        }
      } else {
        setMessage(`触发失败: ${data.detail || '未知错误'}`);
      }
    } catch (err) {
      setMessage(`触发失败: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleOpenUnpackDialog = () => {
    if (readOnly) {
      setUnpackMessage('当前账户为只读模式，无法触发解包任务。');
      return;
    }
    setUnpackDialogError('');
    setUnpackRunOptions(createUnpackRunOptions(unpackConfig));
    setShowUnpackDialog(true);
  };

  const handleCloseUnpackDialog = () => {
    if (unpackLoading) {
      return;
    }
    setShowUnpackDialog(false);
    setUnpackDialogError('');
  };

  const handleUnpackOptionChange = (field, value) => {
    setUnpackRunOptions((prev) => ({
      ...prev,
      [field]: value,
    }));
  };

  const handleUnpackRun = async () => {
    if (readOnly) {
      setUnpackMessage('当前账户为只读模式，无法触发解包任务。');
      return;
    }

    let payload;
    try {
      payload = {
        max_files_per_run: parseUnpackRunValue(unpackRunOptions.max_files_per_run, '单次解包数量'),
        max_runtime_minutes: parseUnpackRunValue(unpackRunOptions.max_runtime_minutes, '最长运行时间'),
      };
    } catch (err) {
      const errorText = err instanceof Error ? err.message : '解包参数校验失败';
      setUnpackDialogError(errorText);
      setUnpackMessage(`失败：${errorText}`);
      return;
    }

    setUnpackDialogError('');
    setUnpackLoading(true);
    setUnpackMessage('LT-1 解包任务启动中...');
    try {
      const res = await fetch(`${apiEndpoint}/unpack/run`, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      });
      const data = await parseJsonSafe(res, {});
      if (res.ok) {
        setShowUnpackDialog(false);
        setUnpackTaskId(data.task_id || '');
        setUnpackTaskTerminal(false);
        setUnpackMessage(data.message || 'LT-1 解包任务已启动');
        if (onTaskStart) {
          onTaskStart(data.task_id, 'LT-1 解包任务已启动。', {
            nonBlocking: true,
            taskType: 'UNPACK_ARCHIVES',
          });
        }
      } else {
        const errorText = data.detail || '未知错误';
        setUnpackDialogError(errorText);
        setUnpackMessage(`失败：${errorText}`);
      }
    } catch (err) {
      const errorText = err.message || '未知错误';
      setUnpackDialogError(errorText);
      setUnpackMessage(`失败：${errorText}`);
    } finally {
      setUnpackLoading(false);
    }
  };

  const handleGf3BatchProcess = async () => {
    if (readOnly) {
      setGf3Message('当前账户为只读模式，无法触发 GF3 处理。');
      return;
    }
    setGf3Loading(true);
    setGf3Message('GF3 批量处理启动中...');
    try {
      const res = await fetch(`${apiEndpoint}/monitor/gf3-process`, {
        method: 'POST',
        credentials: 'include',
      });
      const data = await parseJsonSafe(res, {});
      if (res.ok) {
        setGf3Message(data.message || 'GF3 批量处理任务已启动');
        if (onTaskStart) {
          onTaskStart(data.task_id, 'GF3 L1A→L2 批量处理已启动。');
        }
      } else {
        setGf3Message(`失败：${data.detail || '未知错误'}`);
      }
    } catch (err) {
      setGf3Message(`失败：${err.message}`);
    } finally {
      setGf3Loading(false);
    }
  };

  const sectionStyle = {
    marginBottom: '12px',
    padding: '10px 12px',
    borderRadius: '10px',
    background: 'var(--color-panel-bg)',
    border: '1px solid var(--color-border)',
    boxShadow: 'var(--shadow-soft)',
  };
  const labelStyle = { minWidth: '100px', color: 'var(--color-text-muted)', flexShrink: 0 };
  const rowStyle = { display: 'flex', gap: '8px' };
  const gridStyle = { display: 'grid', rowGap: '6px', fontSize: '0.9em', color: 'var(--color-text-secondary)' };

  const scanBtnStyle = (canRun) => ({
    flex: 1,
    padding: '8px 5px',
    backgroundColor: 'var(--color-info)',
    color: 'white',
    border: 'none',
    borderRadius: '4px',
    cursor: loading || !canRun ? 'not-allowed' : 'pointer',
    fontSize: '0.85em',
  });

  const actionBtnStyle = (isLoading, isDisabled) => ({
    padding: '6px 10px',
    backgroundColor: 'var(--color-accent)',
    color: 'white',
    border: 'none',
    borderRadius: '4px',
    cursor: isLoading || isDisabled ? 'not-allowed' : 'pointer',
    fontSize: '0.85em',
  });

  return (
    <div
      className="monitor-panel"
      style={{
        padding: '15px',
        backgroundColor: 'var(--color-panel-bg)',
        borderTop: '1px solid var(--color-border)',
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        overflow: 'hidden',
      }}
    >
      <h3 style={{ marginTop: 0, marginBottom: '8px', fontSize: '1.1em', flexShrink: 0 }}>数据监控面板</h3>

      <div style={{ flex: 1, overflowY: 'auto', minHeight: 0, paddingRight: '4px' }}>
        <div
          style={{
            margin: '0 0 12px',
            padding: '10px 12px',
            borderRadius: '8px',
            background: 'linear-gradient(90deg, var(--color-accent-soft) 0%, #fff 70%)',
            border: '1px solid #c7ddff',
            color: 'var(--color-accent-strong)',
            fontSize: '0.9em',
          }}
        >
          {configLoaded
            ? '仅手动模式。路径从 .env 读取；如需修改请更新 .env 并重启后端。'
            : '未加载到监控状态，请检查后端 /api/monitor/status。'}
        </div>

        <div style={sectionStyle}>
          <div style={{ fontWeight: 'bold', marginBottom: '8px', color: 'var(--color-text-primary)' }}>路径摘要</div>
          <div style={gridStyle}>
            <div style={rowStyle}><span style={labelStyle}>LT-1 存储</span><span style={{ wordBreak: 'break-all' }}>{formatList(config.radar_dirs)}</span></div>
            <div style={rowStyle}><span style={labelStyle}>LT-1 精轨</span><span style={{ wordBreak: 'break-all' }}>{config.orbit_dir || '未配置'}</span></div>
            <div style={rowStyle}><span style={labelStyle}>GF3 来源</span><span style={{ wordBreak: 'break-all' }}>{formatList(config.gf3_source_dirs)}</span></div>
            <div style={rowStyle}><span style={labelStyle}>GF3 存储</span><span style={{ wordBreak: 'break-all' }}>{formatList(config.gf3_storage_dirs)}</span></div>
            <div style={rowStyle}><span style={labelStyle}>D-InSAR 结果</span><span style={{ wordBreak: 'break-all' }}>{formatList(config.dinsar_dirs)}</span></div>
          </div>
        </div>

        <div style={sectionStyle}>
          <div style={{ fontWeight: 'bold', marginBottom: '8px', color: 'var(--color-text-primary)' }}>LT-1 归档解包</div>
          <div style={{ ...gridStyle, marginBottom: '8px' }}>
            <div style={rowStyle}><span style={labelStyle}>来源目录</span><span style={{ wordBreak: 'break-all' }}>{formatList(unpackConfig.source_dirs)}</span></div>
            <div style={rowStyle}><span style={labelStyle}>LT-1 存储</span><span style={{ wordBreak: 'break-all' }}>{formatList(unpackConfig.insar_storage_dirs)}</span></div>
            <div style={rowStyle}><span style={labelStyle}>单次上限</span><span>{unpackConfig.max_files_per_run > 0 ? `${unpackConfig.max_files_per_run} 个压缩包` : '不限'}</span></div>
            <div style={rowStyle}><span style={labelStyle}>最长运行</span><span>{unpackConfig.max_runtime_minutes > 0 ? `${unpackConfig.max_runtime_minutes} 分钟` : '不限'}</span></div>
          </div>
          <div style={{ display: 'flex', gap: '10px' }}>
            <button
              onClick={handleOpenUnpackDialog}
              disabled={unpackLoading || !canOpenUnpackDialog}
              style={actionBtnStyle(unpackLoading, !canOpenUnpackDialog)}
            >
              {unpackLoading ? '运行中...' : (readOnly ? '只读模式' : 'LT-1 解包')}
            </button>
            <div
              style={{
                fontSize: '0.85em',
                color: unpackMessage.includes('失败') ? 'var(--color-danger)' : 'var(--color-text-muted)',
                alignSelf: 'center',
              }}
            >
              {unpackMessage}
            </div>
          </div>
        </div>

        <div style={sectionStyle}>
          <div style={{ fontWeight: 'bold', marginBottom: '8px', color: 'var(--color-text-primary)' }}>GF3 L1A → L2 处理</div>
          <div style={{ ...gridStyle, marginBottom: '8px' }}>
            <div style={rowStyle}><span style={labelStyle}>L1A 来源</span><span style={{ wordBreak: 'break-all' }}>{formatList(config.gf3_source_dirs)}</span></div>
            <div style={rowStyle}><span style={labelStyle}>L2 存储</span><span style={{ wordBreak: 'break-all' }}>{formatList(config.gf3_storage_dirs)}</span></div>
          </div>
          <div style={{ display: 'flex', gap: '10px' }}>
            <button
              onClick={handleGf3BatchProcess}
              disabled={gf3Loading || readOnly || !canRunGf3Process}
              style={actionBtnStyle(gf3Loading, readOnly || !canRunGf3Process)}
            >
              {gf3Loading ? '运行中...' : (readOnly ? '只读模式' : 'GF3 L1A→L2')}
            </button>
            <div
              style={{
                fontSize: '0.85em',
                color: gf3Message.includes('失败') ? 'var(--color-danger)' : 'var(--color-text-muted)',
                alignSelf: 'center',
              }}
            >
              {gf3Message}
            </div>
          </div>
        </div>

        <div style={sectionStyle}>
          <div style={{ fontWeight: 'bold', marginBottom: '8px', color: 'var(--color-text-primary)' }}>活动任务</div>
          {displayActiveTasks.length === 0 ? (
            <div style={{ fontSize: '0.85em', color: 'var(--color-text-muted)' }}>当前无活动任务。</div>
          ) : (
            <div style={{ display: 'grid', rowGap: '8px' }}>
              {displayActiveTasks.slice(0, 4).map((task) => (
                <div key={task.task_id} style={{ fontSize: '0.85em', color: 'var(--color-text-secondary)' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '4px' }}>
                    <span>{task.task_type}</span>
                    <span>{task.progress}%</span>
                  </div>
                  <div style={{ height: '6px', background: 'var(--color-panel-muted)', borderRadius: '3px', overflow: 'hidden' }}>
                    <div style={{ width: `${task.progress}%`, height: '100%', background: 'var(--color-info)' }} />
                  </div>
                  <div style={{ color: 'var(--color-text-muted)', marginTop: '4px', wordBreak: 'break-all' }}>{t(task.message || '')}</div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div style={{ marginBottom: '4px' }}>
          <h4 style={{ margin: '0 0 5px 0', fontSize: '1em' }}>实时日志</h4>
          <div
            style={{
              height: '160px',
              overflowY: 'auto',
              backgroundColor: '#0f172a',
              color: '#22c55e',
              padding: '10px',
              fontFamily: 'monospace',
              fontSize: '0.85em',
              borderRadius: '4px',
            }}
          >
            {displayLogs.length === 0 ? (
              <div style={{ color: 'var(--color-text-muted)' }}>暂无日志...</div>
            ) : (
              displayLogs.map((log, index) => (
                <div key={index} style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{t(log)}</div>
              ))
            )}
            <div ref={logEndRef} />
          </div>
        </div>
      </div>

      <div style={{ flexShrink: 0, borderTop: '1px solid var(--color-border)', paddingTop: '10px', marginTop: '6px' }}>
        <div style={{ display: 'flex', gap: '8px', marginBottom: '6px' }}>
          <button onClick={() => handleRunNow('radar')} disabled={loading || !canRunRadar} style={scanBtnStyle(canRunRadar)}>扫描 LT-1</button>
          <button onClick={() => handleRunNow('gf3')} disabled={loading || !canRunGf3Scan} style={scanBtnStyle(canRunGf3Scan)}>扫描 GF3</button>
          <button onClick={() => handleRunNow('orbit')} disabled={loading || !canRunOrbit} style={scanBtnStyle(canRunOrbit)}>扫描精轨</button>
          <button onClick={() => handleRunNow('dinsar')} disabled={loading || !canRunDinsar} style={scanBtnStyle(canRunDinsar)}>扫描 D-InSAR</button>
        </div>
        {message && <div style={{ color: message.includes('失败') ? 'red' : 'green', fontSize: '0.9em' }}>{message}</div>}
      </div>

      {showUnpackDialog && (
        <div className="modal-overlay visible" onClick={handleCloseUnpackDialog}>
          <div className="modal-content" onClick={(event) => event.stopPropagation()}>
            <h3 style={{ marginTop: 0 }}>LT-1 解包任务参数</h3>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                handleUnpackRun();
              }}
            >
              <div
                style={{
                  marginBottom: '14px',
                  padding: '10px 12px',
                  borderRadius: '8px',
                  background: 'var(--color-panel-muted)',
                  border: '1px solid var(--color-border)',
                  fontSize: '0.9em',
                  color: 'var(--color-text-secondary)',
                  lineHeight: 1.7,
                }}
              >
                本次填写的参数只覆盖当前这一次解包任务，不会修改 `.env` 默认值。输入 `0` 表示不限。
              </div>

              <div style={{ ...gridStyle, marginBottom: '14px' }}>
                <div style={rowStyle}><span style={labelStyle}>来源目录</span><span style={{ wordBreak: 'break-all' }}>{formatList(unpackConfig.source_dirs)}</span></div>
                <div style={rowStyle}><span style={labelStyle}>LT-1 存储</span><span style={{ wordBreak: 'break-all' }}>{formatList(unpackConfig.insar_storage_dirs)}</span></div>
              </div>

              <div className="form-group">
                <label>单次最多解包数量</label>
                <input
                  type="number"
                  min="0"
                  step="1"
                  value={unpackRunOptions.max_files_per_run}
                  onChange={(event) => handleUnpackOptionChange('max_files_per_run', event.target.value)}
                  disabled={unpackLoading}
                />
              </div>

              <div className="form-group">
                <label>最长运行时间（分钟）</label>
                <input
                  type="number"
                  min="0"
                  step="1"
                  value={unpackRunOptions.max_runtime_minutes}
                  onChange={(event) => handleUnpackOptionChange('max_runtime_minutes', event.target.value)}
                  disabled={unpackLoading}
                />
              </div>

              {unpackDialogError && (
                <div
                  style={{
                    marginTop: '6px',
                    color: 'var(--color-danger)',
                    fontSize: '0.9em',
                    wordBreak: 'break-all',
                  }}
                >
                  {unpackDialogError}
                </div>
              )}

              <div className="modal-actions">
                <button type="button" onClick={handleCloseUnpackDialog} disabled={unpackLoading}>
                  取消
                </button>
                <button type="submit" disabled={unpackLoading}>
                  {unpackLoading ? '启动中...' : '启动解包任务'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

export default DataMonitorPanel;
