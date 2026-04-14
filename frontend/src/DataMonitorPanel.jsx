import React, { useState, useEffect, useRef } from 'react';
import './App.css'; // 复用现有样式
import { useI18n } from './i18n/I18nContext';

const DEFAULT_MONITOR_CONFIG = {
  radar_dirs: [],
  orbit_dir: '',
  dinsar_dirs: [],
  gf3_source_dirs: [],
  gf3_storage_dirs: []
};

const DEFAULT_UNPACK_CONFIG = {
  source_dirs: [],
  insar_storage_dirs: [],
  min_disk_space_gb: 50,
  delete_archive: true,
  tmp_suffix: '.unpack_tmp',
  archive_exts: []
};

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
  const [gf3Loading, setGf3Loading] = useState(false);
  const [gf3Message, setGf3Message] = useState('');
  const logEndRef = useRef(null);
  const toArray = (value) => (Array.isArray(value) ? value : []);
  const parseJsonSafe = async (response, fallback) => {
    try {
      return await response.json();
    } catch {
      return fallback;
    }
  };
  const displayLogs = toArray(logs);
  const displayActiveTasks = toArray(activeTasks);

  const formatList = (list) => (list && list.length ? list.join('; ') : '未配置');

  // 获取初始状态
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
      .then(data => {
        if (canceled) return;
        setConfig({
          ...DEFAULT_MONITOR_CONFIG,
          ...data,
          radar_dirs: toArray(data?.radar_dirs),
          dinsar_dirs: toArray(data?.dinsar_dirs),
          gf3_source_dirs: toArray(data?.gf3_source_dirs),
          gf3_storage_dirs: toArray(data?.gf3_storage_dirs)
        });
        setConfigLoaded(true);
      })
      .catch(err => {
        if (canceled) return;
        console.error("Failed to fetch monitor status:", err);
        setConfigLoaded(false);
      });

    return () => {
      canceled = true;
    };
  }, [apiEndpoint, enabled]);

  useEffect(() => {
    if (!enabled) {
      setUnpackConfig(DEFAULT_UNPACK_CONFIG);
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
      .then(data => {
        if (canceled) return;
        setUnpackConfig({
          ...DEFAULT_UNPACK_CONFIG,
          ...data,
          source_dirs: toArray(data?.source_dirs),
          insar_storage_dirs: toArray(data?.insar_storage_dirs)
        });
      })
      .catch(err => {
        if (canceled) return;
        console.error("Failed to fetch unpack config:", err);
      });

    return () => {
      canceled = true;
    };
  }, [apiEndpoint, enabled]);

  // 轮询日志
  useEffect(() => {
    if (!enabled) {
      setLogs([]);
      setActiveTasks([]);
      return;
    }

    let canceled = false;
    const fetchLogsAndTasks = async () => {
      try {
        const [logsRes, tasksRes] = await Promise.all([
          fetch(`${apiEndpoint}/monitor/logs`, { credentials: 'include' }),
          fetch(`${apiEndpoint}/tasks/active`, { credentials: 'include' })
        ]);
        const [logsData, tasksData] = await Promise.all([
          parseJsonSafe(logsRes, {}),
          parseJsonSafe(tasksRes, [])
        ]);

        if (canceled) return;
        setLogs(logsRes.ok ? toArray(logsData?.logs) : []);
        setActiveTasks(tasksRes.ok ? toArray(tasksData) : []);
      } catch (err) {
        if (canceled) return;
        console.error("Failed to fetch logs or tasks:", err);
      }
    };

    fetchLogsAndTasks();
    const intervalId = setInterval(fetchLogsAndTasks, 2000);
    return () => {
      canceled = true;
      clearInterval(intervalId);
    };
  }, [apiEndpoint, enabled]);

  // 自动滚动到底部
  useEffect(() => {
    if (logEndRef.current) {
      logEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [logs]);

  const hasRadarDirs = Array.isArray(config.radar_dirs) && config.radar_dirs.length > 0;
  const hasOrbitDir = typeof config.orbit_dir === 'string' && config.orbit_dir.trim() !== '';
  const hasDinsarDirs = Array.isArray(config.dinsar_dirs) && config.dinsar_dirs.length > 0;
  const hasGf3SourceDirs = Array.isArray(config.gf3_source_dirs) && config.gf3_source_dirs.length > 0;
  const hasGf3StorageDirs = Array.isArray(config.gf3_storage_dirs) && config.gf3_storage_dirs.length > 0;
  const canRunRadar = !readOnly && configLoaded && hasRadarDirs;
  const canRunOrbit = !readOnly && configLoaded && hasOrbitDir;
  const canRunDinsar = !readOnly && configLoaded && hasDinsarDirs;
  const canRunGf3Scan = !readOnly && configLoaded && hasGf3StorageDirs;
  const canRunGf3Process = !readOnly && configLoaded && hasGf3SourceDirs;

  const handleRunNow = async (target) => {
    if (readOnly) {
      setMessage('当前账号为只读模式，无法触发扫描。');
      return;
    }
    setLoading(true);
    const targetMap = {
      'radar': 'LT-1 数据',
      'orbit': '精轨数据',
      'dinsar': 'D-InSAR 结果',
      'gf3': 'GF3 数据'
    };
    setMessage(`正在触发${targetMap[target] || '全部'}手动扫描...`);

    try {
      const url = target ? `${apiEndpoint}/monitor/run-now?target=${target}` : `${apiEndpoint}/monitor/run-now`;
      const res = await fetch(url, {
        method: 'POST',
        credentials: 'include'
      });
      const data = await res.json();
      if (res.ok) {
        setMessage(data.message);
        if (onTaskStart) onTaskStart(data.task_id, `已触发${targetMap[target] || '全部'}手动扫描...`);
      } else {
        setMessage(`触发失败: ${data.detail || '未知错误'}`);
      }
    } catch (err) {
      setMessage(`触发失败: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleUnpackRun = async () => {
    if (readOnly) {
      setUnpackMessage('当前账号为只读模式，无法触发解包任务。');
      return;
    }
    setUnpackLoading(true);
    setUnpackMessage('LT-1 解包任务启动中...');
    try {
      const res = await fetch(`${apiEndpoint}/unpack/run`, {
        method: 'POST',
        credentials: 'include'
      });
      const data = await res.json();
      if (res.ok) {
        setUnpackMessage(data.message || 'LT-1 解包任务已启动');
        if (onTaskStart) onTaskStart(data.task_id, 'LT-1 解包任务已启动。');
      } else {
        setUnpackMessage(`失败：${data.detail || '未知错误'}`);
      }
    } catch (err) {
      setUnpackMessage(`失败：${err.message}`);
    } finally {
      setUnpackLoading(false);
    }
  };

  const handleGf3BatchProcess = async () => {
    if (readOnly) {
      setGf3Message('当前账号为只读模式，无法触发 GF3 处理。');
      return;
    }
    setGf3Loading(true);
    setGf3Message('GF3 批量处理启动中...');
    try {
      const res = await fetch(`${apiEndpoint}/monitor/gf3-process`, {
        method: 'POST',
        credentials: 'include'
      });
      const data = await res.json();
      if (res.ok) {
        setGf3Message(data.message || 'GF3 批量处理任务已启动');
        if (onTaskStart) onTaskStart(data.task_id, 'GF3 L1A→L2 批量处理已启动。');
      } else {
        setGf3Message(`失败：${data.detail || '未知错误'}`);
      }
    } catch (err) {
      setGf3Message(`失败：${err.message}`);
    } finally {
      setGf3Loading(false);
    }
  };

  const sectionStyle = { marginBottom: '12px', padding: '10px 12px', borderRadius: '10px', background: 'var(--color-panel-bg)', border: '1px solid var(--color-border)', boxShadow: 'var(--shadow-soft)' };
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
    cursor: (loading || !canRun) ? 'not-allowed' : 'pointer',
    fontSize: '0.85em'
  });

  const actionBtnStyle = (isLoading, isReadOnly) => ({
    padding: '6px 10px',
    backgroundColor: 'var(--color-accent)',
    color: 'white',
    border: 'none',
    borderRadius: '4px',
    cursor: (isLoading || isReadOnly) ? 'not-allowed' : 'pointer',
    fontSize: '0.85em'
  });

  return (
    <div className="monitor-panel" style={{ padding: '15px', backgroundColor: 'var(--color-panel-bg)', borderTop: '1px solid var(--color-border)', display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      <h3 style={{ marginTop: 0, marginBottom: '8px', fontSize: '1.1em', flexShrink: 0 }}>数据监控面板</h3>

      {/* 可滚动内容区 */}
      <div style={{ flex: 1, overflowY: 'auto', minHeight: 0, paddingRight: '4px' }}>
        <div style={{ margin: '0 0 12px', padding: '10px 12px', borderRadius: '8px', background: 'linear-gradient(90deg, var(--color-accent-soft) 0%, #fff 70%)', border: '1px solid #c7ddff', color: 'var(--color-accent-strong)', fontSize: '0.9em' }}>
          {configLoaded
            ? '仅手动模式。路径从 .env 读取；如需修改请更新 .env 并重启后端。'
            : '未加载到监控状态，请检查后端 /api/monitor/status。'}
        </div>

        {/* 路径摘要 — 按卫星分组 */}
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

        {/* LT-1 归档解包 */}
        <div style={sectionStyle}>
          <div style={{ fontWeight: 'bold', marginBottom: '8px', color: 'var(--color-text-primary)' }}>LT-1 归档解包</div>
          <div style={{ ...gridStyle, marginBottom: '8px' }}>
            <div style={rowStyle}><span style={labelStyle}>来源目录</span><span style={{ wordBreak: 'break-all' }}>{formatList(unpackConfig.source_dirs)}</span></div>
            <div style={rowStyle}><span style={labelStyle}>LT-1 存储</span><span style={{ wordBreak: 'break-all' }}>{formatList(unpackConfig.insar_storage_dirs)}</span></div>
          </div>
          <div style={{ display: 'flex', gap: '10px' }}>
            <button
              onClick={handleUnpackRun}
              disabled={unpackLoading || readOnly}
              style={actionBtnStyle(unpackLoading, readOnly)}
            >
              {unpackLoading ? '运行中...' : (readOnly ? '只读模式' : 'LT-1 解包')}
            </button>
            <div style={{ fontSize: '0.85em', color: unpackMessage.includes('失败') ? 'var(--color-danger)' : 'var(--color-text-muted)', alignSelf: 'center' }}>
              {unpackMessage}
            </div>
          </div>
        </div>

        {/* GF3 L1A→L2 处理 */}
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
            <div style={{ fontSize: '0.85em', color: gf3Message.includes('失败') ? 'var(--color-danger)' : 'var(--color-text-muted)', alignSelf: 'center' }}>
              {gf3Message}
            </div>
          </div>
        </div>

        {/* 活动任务 */}
        <div style={sectionStyle}>
          <div style={{ fontWeight: 'bold', marginBottom: '8px', color: 'var(--color-text-primary)' }}>活动任务</div>
          {displayActiveTasks.length === 0 ? (
            <div style={{ fontSize: '0.85em', color: 'var(--color-text-muted)' }}>当前无活动任务。</div>
          ) : (
            <div style={{ display: 'grid', rowGap: '8px' }}>
              {displayActiveTasks.slice(0, 4).map(task => (
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

        {/* 实时日志 */}
        <div style={{ marginBottom: '4px' }}>
          <h4 style={{ margin: '0 0 5px 0', fontSize: '1em' }}>实时日志</h4>
          <div style={{
            height: '160px',
            overflowY: 'auto',
            backgroundColor: '#0f172a',
            color: '#22c55e',
            padding: '10px',
            fontFamily: 'monospace',
            fontSize: '0.85em',
            borderRadius: '4px'
          }}>
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

      {/* 扫描按钮 — 固定在底部 */}
      <div style={{ flexShrink: 0, borderTop: '1px solid var(--color-border)', paddingTop: '10px', marginTop: '6px' }}>
        <div style={{ display: 'flex', gap: '8px', marginBottom: '6px' }}>
          <button onClick={() => handleRunNow('radar')} disabled={loading || !canRunRadar} style={scanBtnStyle(canRunRadar)}>扫描 LT-1</button>
          <button onClick={() => handleRunNow('gf3')} disabled={loading || !canRunGf3Scan} style={scanBtnStyle(canRunGf3Scan)}>扫描 GF3</button>
          <button onClick={() => handleRunNow('orbit')} disabled={loading || !canRunOrbit} style={scanBtnStyle(canRunOrbit)}>扫描精轨</button>
          <button onClick={() => handleRunNow('dinsar')} disabled={loading || !canRunDinsar} style={scanBtnStyle(canRunDinsar)}>扫描 D-InSAR</button>
        </div>
        {message && <div style={{ color: message.includes('失败') ? 'red' : 'green', fontSize: '0.9em' }}>{message}</div>}
      </div>
    </div>
  );
};

export default DataMonitorPanel;
