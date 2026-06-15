import React, { useEffect, useRef, useState } from 'react';
import './App.css';
import { useI18n } from './i18n/I18nContext';
import { getAssetInventoryStatus, scanAssetInventory, unpackSentinel1Batch } from './api/assets';

const DEFAULT_MONITOR_CONFIG = {
  radar_dirs: [],
  orbit_dir: '',
  dinsar_dirs: [],
  gf3_archive_source_dirs: [],
  gf3_source_dirs: [],
  gf3_legacy_gdal_enabled: false,
  gf3_sarscape_native_dirs: [],
  gf3_storage_dirs: [],
  gf3_sarscape_runtime_dir: '',
  gf3_sarscape_wrapper_exe: '',
  gf3_sarscape_idlrt_path: '',
  gf3_sarscape_dem_path: '',
  gf3_sarscape_polarizations: 'HH,HV',
  gf3_sarscape_auto_standardize: true,
  gf3_sarscape_clean_after_success: true,
  s1_source_dirs: [],
  s1_storage_dirs: [],
  s1_orbit_dirs: [],
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

const formatGf3Date = (value) => {
  const text = String(value || '').replace(/\D/g, '');
  if (text.length !== 8) {
    return value || '';
  }
  return `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`;
};

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
const normalizeComparePath = (value) => String(value || '').trim().replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();

const DataMonitorPanel = ({ apiEndpoint, onTaskStart, readOnly = false, enabled = true }) => {
  const { t } = useI18n();
  const [config, setConfig] = useState(DEFAULT_MONITOR_CONFIG);
  const [configLoaded, setConfigLoaded] = useState(false);
  const [logs, setLogs] = useState([]);
  const [activeTasks, setActiveTasks] = useState([]);
  const [unpackConfig, setUnpackConfig] = useState(DEFAULT_UNPACK_CONFIG);
  const [unpackLoading, setUnpackLoading] = useState(false);
  const [unpackMessage, setUnpackMessage] = useState('');
  const [showUnpackDialog, setShowUnpackDialog] = useState(false);
  const [unpackRunOptions, setUnpackRunOptions] = useState(() => createUnpackRunOptions());
  const [unpackDialogError, setUnpackDialogError] = useState('');
  const [unpackTaskId, setUnpackTaskId] = useState('');
  const [unpackTaskTerminal, setUnpackTaskTerminal] = useState(false);
  const [s1Loading, setS1Loading] = useState(false);
  const [s1ScanLoading, setS1ScanLoading] = useState(false);
  const [s1Message, setS1Message] = useState('');
  const [gf3UnpackLoading, setGf3UnpackLoading] = useState(false);
  const [gf3ProcessLoading, setGf3ProcessLoading] = useState(false);
  const [gf3SarscapeProduceLoading, setGf3SarscapeProduceLoading] = useState(false);
  const [gf3SarscapeSyncLoading, setGf3SarscapeSyncLoading] = useState(false);
  const [gf3SarscapeCleanLoading, setGf3SarscapeCleanLoading] = useState(false);
  const [gf3ScanLoading, setGf3ScanLoading] = useState(false);
  const [gf3DateLoading, setGf3DateLoading] = useState(false);
  const [gf3SarscapeDates, setGf3SarscapeDates] = useState([]);
  const [gf3SelectedDate, setGf3SelectedDate] = useState('');
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
  const s1ActiveTask = displayActiveTasks.find((task) => task.task_type === 'UNPACK_SENTINEL1');
  const gf3ActiveTask = displayActiveTasks.find((task) =>
    ['GF3_UNPACK', 'GF3_BATCH_PROCESS', 'GF3_SARSCAPE_PRODUCE', 'GF3_SARSCAPE_SYNC', 'GF3_SARSCAPE_CLEAN'].includes(task.task_type)
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
          s1_source_dirs: toArray(data?.s1_source_dirs),
          s1_storage_dirs: toArray(data?.s1_storage_dirs),
          s1_orbit_dirs: toArray(data?.s1_orbit_dirs),
          gf3_archive_source_dirs: toArray(data?.gf3_archive_source_dirs),
          gf3_source_dirs: toArray(data?.gf3_source_dirs),
          gf3_sarscape_native_dirs: toArray(data?.gf3_sarscape_native_dirs),
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
  const hasS1SourceDirs = config.s1_source_dirs.length > 0;
  const hasS1StorageDirs = config.s1_storage_dirs.length > 0;
  const hasS1OrbitDirs = config.s1_orbit_dirs.length > 0;
  const hasGf3ArchiveSourceDirs = config.gf3_archive_source_dirs.length > 0;
  const hasGf3SourceDirs = config.gf3_source_dirs.length > 0;
  const hasGf3SarscapeNativeDirs = config.gf3_sarscape_native_dirs.length > 0;
  const hasGf3StorageDirs = config.gf3_storage_dirs.length > 0;
  const gf3LegacyGdalEnabled = Boolean(config.gf3_legacy_gdal_enabled);
  const hasGf3SarscapeWrapper = typeof config.gf3_sarscape_wrapper_exe === 'string' && config.gf3_sarscape_wrapper_exe.trim() !== '';
  const hasGf3SarscapeDem = typeof config.gf3_sarscape_dem_path === 'string' && config.gf3_sarscape_dem_path.trim() !== '';

  const canRunRadar = !readOnly && configLoaded && hasRadarDirs;
  const canRunOrbit = !readOnly && configLoaded && hasOrbitDir;
  const canRunS1 = !readOnly && configLoaded && (hasS1SourceDirs || hasS1StorageDirs || hasS1OrbitDirs);
  const canRunS1Scan = !readOnly && configLoaded && hasS1SourceDirs;
  const canRunS1OrbitScan = !readOnly && configLoaded && hasS1OrbitDirs;
  const canRunGf3Scan = !readOnly && configLoaded && hasGf3StorageDirs;
  const canRunGf3Unpack = !readOnly && configLoaded && gf3LegacyGdalEnabled && hasGf3ArchiveSourceDirs && hasGf3SourceDirs;
  const canRunGf3Process = !readOnly && configLoaded && gf3LegacyGdalEnabled && hasGf3SourceDirs;
  const canRunGf3SarscapeProduce = !readOnly && configLoaded && hasGf3ArchiveSourceDirs && hasGf3SarscapeNativeDirs && hasGf3StorageDirs && hasGf3SarscapeWrapper && hasGf3SarscapeDem;
  const canRunGf3SarscapeSync = !readOnly && configLoaded && hasGf3SarscapeNativeDirs && hasGf3StorageDirs;
  const canRunGf3SarscapeClean = !readOnly && configLoaded && hasGf3SarscapeNativeDirs && hasGf3StorageDirs;
  const canOpenUnpackDialog = !readOnly && unpackConfig.source_dirs.length > 0;

  useEffect(() => {
    if (!enabled || !configLoaded || !hasGf3ArchiveSourceDirs) {
      setGf3SarscapeDates([]);
      setGf3SelectedDate('');
      return;
    }

    let canceled = false;
    const fetchGf3Dates = async () => {
      setGf3DateLoading(true);
      try {
        const res = await fetch(`${apiEndpoint}/monitor/gf3-sarscape-dates`, { credentials: 'include' });
        const data = await parseJsonSafe(res, {});
        if (canceled) {
          return;
        }
        if (!res.ok) {
          throw new Error(data?.detail || `HTTP ${res.status}`);
        }
        const nextDates = toArray(data?.dates);
        setGf3SarscapeDates(nextDates);
        setGf3SelectedDate((prev) => (
          prev && nextDates.some((item) => String(item?.date || '') === prev) ? prev : ''
        ));
      } catch (err) {
        if (!canceled) {
          console.error('Failed to fetch GF3 SARscape dates:', err);
          setGf3SarscapeDates([]);
        }
      } finally {
        if (!canceled) {
          setGf3DateLoading(false);
        }
      }
    };

    fetchGf3Dates();

    return () => {
      canceled = true;
    };
  }, [apiEndpoint, enabled, configLoaded, hasGf3ArchiveSourceDirs]);

  const handleS1Run = async () => {
    if (readOnly) {
      setS1Message('当前账户为只读模式，无法触发 Sentinel-1 任务。');
      return;
    }
    setS1Loading(true);
    setS1Message('Sentinel-1 任务启动中...');
    try {
      const res = await unpackSentinel1Batch({
        scan_before_unpack: true,
        overwrite: false,
      });
      setS1Message(res.message || 'Sentinel-1 任务已启动');
      if (onTaskStart) {
        onTaskStart(res.task_id, 'Sentinel-1 任务已启动。', {
          nonBlocking: true,
          taskType: 'UNPACK_SENTINEL1',
        });
      }
    } catch (err) {
      setS1Message(`失败：${err?.response?.data?.detail || err.message || '未知错误'}`);
    } finally {
      setS1Loading(false);
    }
  };

  const handleS1Scan = async () => {
    if (readOnly) {
      setS1Message('当前账户为只读模式，无法触发 Sentinel-1 扫描。');
      return;
    }
    setS1ScanLoading(true);
    setS1Message('Sentinel-1 源数据扫描启动中...');
    try {
      const inventoryStatus = await getAssetInventoryStatus();
      const sourcePathSet = new Set(config.s1_source_dirs.map(normalizeComparePath));
      const rootIds = (inventoryStatus?.states || [])
        .filter((item) => item?.inventory_type === 'source_product' && sourcePathSet.has(normalizeComparePath(item?.root_path)))
        .map((item) => item.root_ref_id)
        .filter((value, index, array) => value && array.indexOf(value) === index);
      const res = await scanAssetInventory({
        inventory_types: ['source_product'],
        root_ids: rootIds,
        bind_orbits: true,
      });
      setS1Message(res.message || 'Sentinel-1 源数据扫描任务已启动');
      onTaskStart?.(res.task_id, 'Sentinel-1 源数据扫描任务已启动。', {
        nonBlocking: true,
        taskType: 'SCAN_ASSET_INVENTORY',
      });
    } catch (err) {
      setS1Message(`失败：${err?.response?.data?.detail || err.message || '未知错误'}`);
    } finally {
      setS1ScanLoading(false);
    }
  };

  const handleS1OrbitScan = async () => {
    if (readOnly) {
      setS1Message('当前账户为只读模式，无法触发 Sentinel-1 精轨扫描。');
      return;
    }
    setS1ScanLoading(true);
    setS1Message('Sentinel-1 精轨扫描启动中...');
    try {
      const inventoryStatus = await getAssetInventoryStatus();
      const orbitPathSet = new Set(config.s1_orbit_dirs.map(normalizeComparePath));
      const rootIds = (inventoryStatus?.states || [])
        .filter((item) => item?.inventory_type === 'orbit_asset' && orbitPathSet.has(normalizeComparePath(item?.root_path)))
        .map((item) => item.root_ref_id)
        .filter((value, index, array) => value && array.indexOf(value) === index);
      const res = await scanAssetInventory({
        inventory_types: ['orbit_asset'],
        root_ids: rootIds,
        bind_orbits: true,
      });
      setS1Message(res.message || 'Sentinel-1 精轨扫描任务已启动');
      onTaskStart?.(res.task_id, 'Sentinel-1 精轨扫描任务已启动。', {
        nonBlocking: true,
        taskType: 'SCAN_ASSET_INVENTORY',
      });
    } catch (err) {
      setS1Message(`失败：${err?.response?.data?.detail || err.message || '未知错误'}`);
    } finally {
      setS1ScanLoading(false);
    }
  };

  const handleGf3BatchProcess = async () => {
    if (readOnly) {
      setGf3Message('当前账户为只读模式，无法触发 GF3 预处理。');
      return;
    }
    setGf3ProcessLoading(true);
    setGf3Message('GF3 预处理启动中...');
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
      setGf3Message(`失败：${err.message || '未知错误'}`);
    } finally {
      setGf3ProcessLoading(false);
    }
  };

  const handleGf3Unpack = async () => {
    if (readOnly) {
      setGf3Message('当前账户为只读模式，无法触发 GF3 解包。');
      return;
    }
    setGf3UnpackLoading(true);
    setGf3Message('GF3 解包启动中...');
    try {
      const res = await fetch(`${apiEndpoint}/monitor/gf3-unpack`, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({}),
      });
      const data = await parseJsonSafe(res, {});
      if (res.ok) {
        setGf3Message(data.message || 'GF3 解包任务已启动');
        if (onTaskStart) {
          onTaskStart(data.task_id, 'GF3 解包任务已启动。', {
            nonBlocking: true,
            taskType: 'GF3_UNPACK',
          });
        }
      } else {
        setGf3Message(`失败：${data.detail || '未知错误'}`);
      }
    } catch (err) {
      setGf3Message(`失败：${err.message || '未知错误'}`);
    } finally {
      setGf3UnpackLoading(false);
    }
  };

  const handleGf3SarscapeProduce = async () => {
    if (readOnly) {
      setGf3Message('当前账户为只读模式，无法触发 GF3 SARscape 生产。');
      return;
    }
    setGf3SarscapeProduceLoading(true);
    setGf3Message('GF3 SARscape 生产链路启动中...');
    try {
      const payload = gf3SelectedDate ? { selected_dates: [gf3SelectedDate] } : {};
      const res = await fetch(`${apiEndpoint}/monitor/gf3-sarscape-produce`, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      });
      const data = await parseJsonSafe(res, {});
      if (res.ok) {
        setGf3Message(data.message || 'GF3 SARscape 生产任务已启动');
        if (onTaskStart) {
          onTaskStart(data.task_id, 'GF3 SARscape 生产链路已启动。', {
            nonBlocking: true,
            taskType: 'GF3_SARSCAPE_PRODUCE',
          });
        }
      } else {
        setGf3Message(`失败：${data.detail || '未知错误'}`);
      }
    } catch (err) {
      setGf3Message(`失败：${err.message || '未知错误'}`);
    } finally {
      setGf3SarscapeProduceLoading(false);
    }
  };

  const handleRefreshGf3Dates = async () => {
    if (!hasGf3ArchiveSourceDirs) {
      setGf3Message('GF3_ARCHIVE_SOURCE_DIRS 未配置。');
      return;
    }
    setGf3DateLoading(true);
    setGf3Message('正在刷新 GF3 影像日期...');
    try {
      const res = await fetch(`${apiEndpoint}/monitor/gf3-sarscape-dates`, { credentials: 'include' });
      const data = await parseJsonSafe(res, {});
      if (!res.ok) {
        throw new Error(data?.detail || `HTTP ${res.status}`);
      }
      const nextDates = toArray(data?.dates);
      setGf3SarscapeDates(nextDates);
      setGf3SelectedDate((prev) => (
        prev && nextDates.some((item) => String(item?.date || '') === prev) ? prev : ''
      ));
      setGf3Message(`GF3 影像日期已刷新：${nextDates.length} 个日期。`);
    } catch (err) {
      setGf3Message(`失败：${err.message || '未知错误'}`);
    } finally {
      setGf3DateLoading(false);
    }
  };

  const handleGf3SarscapeSync = async () => {
    if (readOnly) {
      setGf3Message('当前账户为只读模式，无法触发 GF3 SARscape 标准化。');
      return;
    }
    setGf3SarscapeSyncLoading(true);
    setGf3Message('GF3 SARscape 原生结果标准化启动中...');
    try {
      const res = await fetch(`${apiEndpoint}/monitor/gf3-sarscape-sync`, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({}),
      });
      const data = await parseJsonSafe(res, {});
      if (res.ok) {
        setGf3Message(data.message || 'GF3 SARscape 标准化任务已启动');
        if (onTaskStart) {
          onTaskStart(data.task_id, 'GF3 SARscape 原生结果标准化已启动。', {
            nonBlocking: true,
            taskType: 'GF3_SARSCAPE_SYNC',
          });
        }
      } else {
        setGf3Message(`失败：${data.detail || '未知错误'}`);
      }
    } catch (err) {
      setGf3Message(`失败：${err.message || '未知错误'}`);
    } finally {
      setGf3SarscapeSyncLoading(false);
    }
  };

  const handleGf3SarscapeClean = async () => {
    if (readOnly) {
      setGf3Message('当前账户为只读模式，无法触发 GF3 SARscape 清理。');
      return;
    }
    setGf3SarscapeCleanLoading(true);
    setGf3Message('GF3 SARscape 中间数据清理启动中...');
    try {
      const res = await fetch(`${apiEndpoint}/monitor/gf3-sarscape-clean`, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ dry_run: false, require_standardized: true }),
      });
      const data = await parseJsonSafe(res, {});
      if (res.ok) {
        setGf3Message(data.message || 'GF3 SARscape 清理任务已启动');
        if (onTaskStart) {
          onTaskStart(data.task_id, 'GF3 SARscape 中间数据清理已启动。', {
            nonBlocking: true,
            taskType: 'GF3_SARSCAPE_CLEAN',
          });
        }
      } else {
        setGf3Message(`失败：${data.detail || '未知错误'}`);
      }
    } catch (err) {
      setGf3Message(`失败：${err.message || '未知错误'}`);
    } finally {
      setGf3SarscapeCleanLoading(false);
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

  const handleRadarScan = async () => {
    if (readOnly) {
      setUnpackMessage('当前账户为只读模式，无法触发扫描。');
      return;
    }
    setUnpackMessage('LT-1 扫描启动中...');
    try {
      const res = await fetch(`${apiEndpoint}/monitor/run-now?target=radar`, {
        method: 'POST',
        credentials: 'include',
      });
      const data = await parseJsonSafe(res, {});
      if (res.ok) {
        setUnpackMessage(data.message || 'LT-1 扫描任务已启动');
        if (onTaskStart) {
          onTaskStart(data.task_id, '已触发 LT-1 手动扫描...');
        }
      } else {
        setUnpackMessage(`失败：${data.detail || '未知错误'}`);
      }
    } catch (err) {
      setUnpackMessage(`失败：${err.message || '未知错误'}`);
    }
  };

  const handleOrbitScan = async () => {
    if (readOnly) {
      setUnpackMessage('当前账户为只读模式，无法触发扫描。');
      return;
    }
    setUnpackMessage('精轨扫描启动中...');
    try {
      const res = await fetch(`${apiEndpoint}/monitor/run-now?target=orbit`, {
        method: 'POST',
        credentials: 'include',
      });
      const data = await parseJsonSafe(res, {});
      if (res.ok) {
        setUnpackMessage(data.message || '精轨扫描任务已启动');
        if (onTaskStart) {
          onTaskStart(data.task_id, '已触发精轨手动扫描...');
        }
      } else {
        setUnpackMessage(`失败：${data.detail || '未知错误'}`);
      }
    } catch (err) {
      setUnpackMessage(`失败：${err.message || '未知错误'}`);
    }
  };

  const handleGf3Scan = async () => {
    if (readOnly) {
      setGf3Message('当前账户为只读模式，无法触发扫描。');
      return;
    }
    setGf3ScanLoading(true);
    setGf3Message('GF3 资产扫描启动中...');
    try {
      const inventoryStatus = await getAssetInventoryStatus();
      const gf3SourcePathSet = new Set(config.gf3_archive_source_dirs.map(normalizeComparePath));
      const rootIds = (inventoryStatus?.states || [])
        .filter((item) => item?.inventory_type === 'source_product' && gf3SourcePathSet.has(normalizeComparePath(item?.root_path)))
        .map((item) => item.root_ref_id)
        .filter((value, index, array) => value && array.indexOf(value) === index);
      const data = await scanAssetInventory({
        inventory_types: ['source_product'],
        root_ids: rootIds,
        bind_orbits: false,
      });
      setGf3Message(data.message || 'GF3 资产扫描任务已启动');
      if (onTaskStart) {
        onTaskStart(data.task_id, '已触发 GF3 资产扫描...', {
          nonBlocking: true,
          taskType: 'SCAN_ASSET_INVENTORY',
        });
      }
    } catch (err) {
      setGf3Message(`失败：${err?.response?.data?.detail || err.message || '未知错误'}`);
    } finally {
      setGf3ScanLoading(false);
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
            <div style={rowStyle}><span style={labelStyle}>S1 源数据</span><span style={{ wordBreak: 'break-all' }}>{formatList(config.s1_source_dirs)}</span></div>
            <div style={rowStyle}><span style={labelStyle}>S1 存储</span><span style={{ wordBreak: 'break-all' }}>{formatList(config.s1_storage_dirs)}</span></div>
            <div style={rowStyle}><span style={labelStyle}>S1 精轨</span><span style={{ wordBreak: 'break-all' }}>{formatList(config.s1_orbit_dirs)}</span></div>
            <div style={rowStyle}><span style={labelStyle}>GF3 压缩包</span><span style={{ wordBreak: 'break-all' }}>{formatList(config.gf3_archive_source_dirs)}</span></div>
            <div style={rowStyle}><span style={labelStyle}>GF3 legacy L1A</span><span style={{ wordBreak: 'break-all' }}>{formatList(config.gf3_source_dirs)}</span></div>
            <div style={rowStyle}><span style={labelStyle}>GF3 原生</span><span style={{ wordBreak: 'break-all' }}>{formatList(config.gf3_sarscape_native_dirs)}</span></div>
            <div style={rowStyle}><span style={labelStyle}>GF3 存储</span><span style={{ wordBreak: 'break-all' }}>{formatList(config.gf3_storage_dirs)}</span></div>
            <div style={rowStyle}><span style={labelStyle}>GF3 runtime</span><span style={{ wordBreak: 'break-all' }}>{config.gf3_sarscape_runtime_dir || '未配置'}</span></div>
            <div style={rowStyle}><span style={labelStyle}>D-InSAR 结果</span><span style={{ wordBreak: 'break-all' }}>{formatList(config.dinsar_dirs)}</span></div>
          </div>
        </div>

        <div style={sectionStyle}>
          <div style={{ fontWeight: 'bold', marginBottom: '8px', color: 'var(--color-text-primary)' }}>LT-1 归档解包 / 扫描</div>
          <div style={{ ...gridStyle, marginBottom: '8px' }}>
            <div style={rowStyle}><span style={labelStyle}>来源目录</span><span style={{ wordBreak: 'break-all' }}>{formatList(unpackConfig.source_dirs)}</span></div>
            <div style={rowStyle}><span style={labelStyle}>LT-1 存储</span><span style={{ wordBreak: 'break-all' }}>{formatList(unpackConfig.insar_storage_dirs)}</span></div>
            <div style={rowStyle}><span style={labelStyle}>单次上限</span><span>{unpackConfig.max_files_per_run > 0 ? `${unpackConfig.max_files_per_run} 个压缩包` : '不限'}</span></div>
            <div style={rowStyle}><span style={labelStyle}>最长运行</span><span>{unpackConfig.max_runtime_minutes > 0 ? `${unpackConfig.max_runtime_minutes} 分钟` : '不限'}</span></div>
          </div>
          <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
            <button
              onClick={handleOpenUnpackDialog}
              disabled={unpackLoading || !canOpenUnpackDialog}
              style={actionBtnStyle(unpackLoading, !canOpenUnpackDialog)}
            >
              {unpackLoading ? '运行中...' : (readOnly ? '只读模式' : 'LT-1 解包')}
            </button>
            <button
              onClick={handleRadarScan}
              disabled={readOnly || !canRunRadar}
              style={actionBtnStyle(false, !canRunRadar)}
            >
              {readOnly ? '只读模式' : '扫描 LT-1'}
            </button>
            <button
              onClick={handleOrbitScan}
              disabled={readOnly || !canRunOrbit}
              style={actionBtnStyle(false, !canRunOrbit)}
            >
              {readOnly ? '只读模式' : '扫描精轨'}
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
          <div style={{ fontWeight: 'bold', marginBottom: '8px', color: 'var(--color-text-primary)' }}>Sentinel-1 解包 / 扫描</div>
          <div style={{ ...gridStyle, marginBottom: '8px' }}>
            <div style={rowStyle}><span style={labelStyle}>S1 源数据</span><span style={{ wordBreak: 'break-all' }}>{formatList(config.s1_source_dirs)}</span></div>
            <div style={rowStyle}><span style={labelStyle}>S1 存储</span><span style={{ wordBreak: 'break-all' }}>{formatList(config.s1_storage_dirs)}</span></div>
            <div style={rowStyle}><span style={labelStyle}>S1 精轨</span><span style={{ wordBreak: 'break-all' }}>{formatList(config.s1_orbit_dirs)}</span></div>
          </div>
          <div style={{ display: 'flex', gap: '10px' }}>
            <button
              onClick={handleS1Run}
              disabled={s1Loading || s1ScanLoading || !canRunS1}
              style={actionBtnStyle(s1Loading, !canRunS1)}
            >
              {s1Loading ? '运行中...' : (readOnly ? '只读模式' : 'Sentinel-1 解包')}
            </button>
            <button
              onClick={handleS1Scan}
              disabled={s1ScanLoading || s1Loading || !canRunS1Scan}
              style={actionBtnStyle(s1ScanLoading, !canRunS1Scan)}
            >
              {s1ScanLoading ? '运行中...' : (readOnly ? '只读模式' : '扫描 S1 源数据')}
            </button>
            <button
              onClick={handleS1OrbitScan}
              disabled={s1ScanLoading || s1Loading || !canRunS1OrbitScan}
              style={actionBtnStyle(s1ScanLoading, !canRunS1OrbitScan)}
            >
              {s1ScanLoading ? '运行中...' : (readOnly ? '只读模式' : '扫描 S1 精轨')}
            </button>
            <div
              style={{
                fontSize: '0.85em',
                color: s1Message.includes('失败') ? 'var(--color-danger)' : 'var(--color-text-muted)',
                alignSelf: 'center',
              }}
            >
              {s1ActiveTask ? (s1ActiveTask.message || 'Sentinel-1 任务运行中...') : s1Message}
            </div>
          </div>
        </div>

        <div style={sectionStyle}>
          <div style={{ fontWeight: 'bold', marginBottom: '8px', color: 'var(--color-text-primary)' }}>GF3 SARscape 生产</div>
          <div style={{ ...gridStyle, marginBottom: '8px' }}>
            <div style={rowStyle}><span style={labelStyle}>压缩包来源</span><span style={{ wordBreak: 'break-all' }}>{formatList(config.gf3_archive_source_dirs)}</span></div>
            <div style={rowStyle}><span style={labelStyle}>SARscape 原生</span><span style={{ wordBreak: 'break-all' }}>{formatList(config.gf3_sarscape_native_dirs)}</span></div>
            <div style={rowStyle}><span style={labelStyle}>L2 存储</span><span style={{ wordBreak: 'break-all' }}>{formatList(config.gf3_storage_dirs)}</span></div>
            <div style={rowStyle}><span style={labelStyle}>Runtime</span><span style={{ wordBreak: 'break-all' }}>{config.gf3_sarscape_runtime_dir || '未配置'}</span></div>
            <div style={rowStyle}><span style={labelStyle}>Wrapper</span><span style={{ wordBreak: 'break-all' }}>{config.gf3_sarscape_wrapper_exe || '未配置'}</span></div>
            <div style={rowStyle}><span style={labelStyle}>SARscape DEM</span><span style={{ wordBreak: 'break-all' }}>{config.gf3_sarscape_dem_path || '未配置'}</span></div>
            <div style={rowStyle}><span style={labelStyle}>极化</span><span>{config.gf3_sarscape_polarizations || 'HH,HV'}</span></div>
            <div style={rowStyle}><span style={labelStyle}>Legacy GDAL</span><span>{gf3LegacyGdalEnabled ? '启用' : '关闭'}</span></div>
            <div style={rowStyle}>
              <span style={labelStyle}>影像日期</span>
              <span style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
                <select
                  value={gf3SelectedDate}
                  onChange={(event) => setGf3SelectedDate(event.target.value)}
                  disabled={gf3DateLoading || gf3SarscapeProduceLoading || readOnly || !canRunGf3SarscapeProduce}
                  style={{ minWidth: '180px', padding: '4px 6px' }}
                >
                  <option value="">全部可用日期</option>
                  {gf3SarscapeDates.map((item) => (
                    <option key={item.date} value={item.date}>
                      {formatGf3Date(item.date)} ({item.scene_count || 0})
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={handleRefreshGf3Dates}
                  disabled={gf3DateLoading || readOnly || !hasGf3ArchiveSourceDirs}
                  style={actionBtnStyle(gf3DateLoading, readOnly || !hasGf3ArchiveSourceDirs)}
                >
                  {gf3DateLoading ? '加载中...' : '刷新日期'}
                </button>
              </span>
            </div>
          </div>
          <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
            {gf3LegacyGdalEnabled && (
              <>
                <button
                  onClick={handleGf3Unpack}
                  disabled={gf3UnpackLoading || readOnly || !canRunGf3Unpack}
                  style={actionBtnStyle(gf3UnpackLoading, readOnly || !canRunGf3Unpack)}
                >
                  {gf3UnpackLoading ? '运行中...' : (readOnly ? '只读模式' : 'GF3 legacy 解包')}
                </button>
                <button
                  onClick={handleGf3BatchProcess}
                  disabled={gf3ProcessLoading || readOnly || !canRunGf3Process}
                  style={actionBtnStyle(gf3ProcessLoading, readOnly || !canRunGf3Process)}
                >
                  {gf3ProcessLoading ? '运行中...' : (readOnly ? '只读模式' : 'GF3 legacy 预处理')}
                </button>
              </>
            )}
            <button
              onClick={handleGf3SarscapeProduce}
              disabled={gf3SarscapeProduceLoading || readOnly || !canRunGf3SarscapeProduce}
              style={actionBtnStyle(gf3SarscapeProduceLoading, readOnly || !canRunGf3SarscapeProduce)}
            >
              {gf3SarscapeProduceLoading ? '运行中...' : (readOnly ? '只读模式' : 'GF3 SARscape 生产')}
            </button>
            <button
              onClick={handleGf3SarscapeSync}
              disabled={gf3SarscapeSyncLoading || readOnly || !canRunGf3SarscapeSync}
              style={actionBtnStyle(gf3SarscapeSyncLoading, readOnly || !canRunGf3SarscapeSync)}
            >
              {gf3SarscapeSyncLoading ? '运行中...' : (readOnly ? '只读模式' : 'GF3 SARscape 入库')}
            </button>
            <button
              onClick={handleGf3SarscapeClean}
              disabled={gf3SarscapeCleanLoading || readOnly || !canRunGf3SarscapeClean}
              style={actionBtnStyle(gf3SarscapeCleanLoading, readOnly || !canRunGf3SarscapeClean)}
            >
              {gf3SarscapeCleanLoading ? '运行中...' : (readOnly ? '只读模式' : '清理 GF3 中间')}
            </button>
            <button
              onClick={handleGf3Scan}
              disabled={gf3ScanLoading || readOnly || !canRunGf3Scan}
              style={actionBtnStyle(gf3ScanLoading, readOnly || !canRunGf3Scan)}
            >
              {gf3ScanLoading ? '运行中...' : (readOnly ? '只读模式' : '扫描 GF3')}
            </button>
            <div
              style={{
                fontSize: '0.85em',
                color: gf3Message.includes('失败') ? 'var(--color-danger)' : 'var(--color-text-muted)',
                alignSelf: 'center',
              }}
            >
              {gf3ActiveTask ? (gf3ActiveTask.message || 'GF3 任务运行中...') : gf3Message}
            </div>
          </div>
        </div>
      </div>

      <div style={{ flexShrink: 0, borderTop: '1px solid var(--color-border)', paddingTop: '10px', marginTop: '6px' }}>
        <div style={{ fontWeight: 'bold', marginBottom: '6px', color: 'var(--color-text-primary)' }}>实时日志</div>
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
