import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTaskStore } from '../store';
import { getRecentTasks, getTaskLogs } from '../api/tasks';

const normalizeList = (value) => (Array.isArray(value) ? value.filter(Boolean) : []);

const matchesTask = (task, taskTypes, taskTypePrefixes, taskIds) => {
  const taskId = String(task?.task_id || '');
  const taskType = String(task?.task_type || '').toUpperCase();
  if (taskIds.length && taskIds.includes(taskId)) return true;
  if (taskTypes.length && taskTypes.includes(taskType)) return true;
  if (taskTypePrefixes.length && taskTypePrefixes.some(prefix => taskType.startsWith(prefix))) return true;
  return !taskTypes.length && !taskTypePrefixes.length && !taskIds.length;
};

export default function useTaskMonitor({
  taskTypes = [],
  taskTypePrefixes = [],
  taskIds = [],
  showRecent = false,
  recentLimit = 5,
  pollRecentMs = 0,
} = {}) {
  const activeTasks = useTaskStore((state) => state.activeTasks);
  const normalizedTaskTypes = useMemo(
    () => normalizeList(taskTypes).map(item => String(item).toUpperCase()),
    [taskTypes],
  );
  const normalizedPrefixes = useMemo(
    () => normalizeList(taskTypePrefixes).map(item => String(item).toUpperCase()),
    [taskTypePrefixes],
  );
  const normalizedTaskIds = useMemo(
    () => normalizeList(taskIds).map(item => String(item)),
    [taskIds],
  );
  const [recentTasks, setRecentTasks] = useState([]);
  const [recentLoading, setRecentLoading] = useState(false);
  const [recentError, setRecentError] = useState('');

  const filteredActiveTasks = useMemo(
    () => activeTasks.filter(task => matchesTask(task, normalizedTaskTypes, normalizedPrefixes, normalizedTaskIds)),
    [activeTasks, normalizedTaskTypes, normalizedPrefixes, normalizedTaskIds],
  );

  const refreshRecentTasks = useCallback(async () => {
    if (!showRecent) return [];
    setRecentLoading(true);
    setRecentError('');
    try {
      if (!normalizedTaskTypes.length) {
        setRecentTasks([]);
        return [];
      }
      const data = await getRecentTasks(normalizedTaskTypes, [], recentLimit, 0);
      const tasks = Array.isArray(data) ? data : (data?.tasks || []);
      const filtered = tasks.filter(task => matchesTask(task, normalizedTaskTypes, normalizedPrefixes, normalizedTaskIds));
      setRecentTasks(filtered);
      return filtered;
    } catch (error) {
      setRecentError(error?.response?.data?.detail || error?.message || '任务记录加载失败');
      setRecentTasks([]);
      return [];
    } finally {
      setRecentLoading(false);
    }
  }, [normalizedTaskTypes, normalizedPrefixes, normalizedTaskIds, recentLimit, showRecent]);

  useEffect(() => {
    if (!showRecent) {
      setRecentTasks([]);
      setRecentError('');
      return undefined;
    }
    void refreshRecentTasks();
    if (!pollRecentMs) return undefined;
    const timer = window.setInterval(() => {
      void refreshRecentTasks();
    }, pollRecentMs);
    return () => window.clearInterval(timer);
  }, [pollRecentMs, refreshRecentTasks, showRecent]);

  const latestTask = filteredActiveTasks[0] || recentTasks[0] || null;
  const isBusy = filteredActiveTasks.length > 0;

  const loadTaskLogs = useCallback((taskId, limit = 50, offset = 0) => (
    getTaskLogs(taskId, limit, offset)
  ), []);

  return useMemo(() => ({
    activeTasks: filteredActiveTasks,
    recentTasks,
    latestTask,
    isBusy,
    recentLoading,
    recentError,
    refreshRecentTasks,
    loadTaskLogs,
  }), [
    filteredActiveTasks,
    recentTasks,
    latestTask,
    isBusy,
    recentLoading,
    recentError,
    refreshRecentTasks,
    loadTaskLogs,
  ]);
}
