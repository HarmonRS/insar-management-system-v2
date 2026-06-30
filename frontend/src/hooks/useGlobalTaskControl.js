import { useCallback, useEffect, useRef, useState } from 'react';
import apiClient from '../api/client';
import { normalizeTaskStatus } from '../utils/appUiHelpers';

const ACTIVE_TASK_FALLBACK_POLL_MS = 10000;

export default function useGlobalTaskControl({
  currentUser,
  licenseOk,
  activeTasks,
  setActiveTasks,
  setRuntimeSummary,
  pendingTaskIds,
  setPendingTaskIds,
  setIsCheckingTasks,
  handleTaskCompletionRef,
}) {
  const [cancelTaskPwd, setCancelTaskPwd] = useState('');
  const [showCancelTask, setShowCancelTask] = useState(false);

  // Stable refs so SSE handler doesn't need to re-subscribe on every render
  const pendingTaskIdsRef = useRef(pendingTaskIds);
  useEffect(() => { pendingTaskIdsRef.current = pendingTaskIds; }, [pendingTaskIds]);

  const handleTasksUpdate = useCallback(async (tasks, runtimeSummary = null) => {
    setActiveTasks(tasks);
    if (setRuntimeSummary) {
      setRuntimeSummary(runtimeSummary);
    }
    const hasRunningTasks = tasks.length > 0;

    // 首次检查完成，清除检查状态
    setIsCheckingTasks(false);

    const currentPending = pendingTaskIdsRef.current;

    // 如果 pendingTaskIds 为空，但 activeTasks 有任务，说明是刷新后的初始化
    // 需要将 activeTasks 中的任务添加到 pendingTaskIds
    if (currentPending.length === 0 && hasRunningTasks) {
      const activeTaskIds = tasks.map((t) => t.task_id);
      console.log('初始化：将活跃任务添加到 pending 列表:', activeTaskIds);
      setPendingTaskIds(activeTaskIds);
    }

    if (currentPending.length > 0) {
      const currentTaskIds = new Set(tasks.map((t) => t.task_id));
      const finishedTaskIds = currentPending.filter((id) => !currentTaskIds.has(id));
      if (finishedTaskIds.length > 0) {
        console.log('检测到可能已结束的任务:', finishedTaskIds);
        const reallyFinishedIds = [];

        for (const taskId of finishedTaskIds) {
          try {
            const statusRes = await apiClient.get(`/tasks/${taskId}`);
            const taskInfo = statusRes.data;
            const taskStatus = normalizeTaskStatus(taskInfo?.status);

            // 检查任务是否真正完成：解析 message 中的进度信息
            let isReallyFinished = taskStatus === 'COMPLETED' || taskStatus === 'FAILED';

            // 如果任务状态是 PENDING，检查进度信息
            if (taskStatus === 'PENDING' && taskInfo?.message) {
              // 匹配格式：(current/total)
              const match = taskInfo.message.match(/\((\d+)\/(\d+)\)/);
              if (match) {
                const current = parseInt(match[1], 10);
                const total = parseInt(match[2], 10);
                // 如果还没处理完，任务还在运行
                if (current < total) {
                  console.log(`任务 ${taskId} 还在运行，进度: ${current}/${total}`);
                  isReallyFinished = false;
                } else {
                  console.log(`任务 ${taskId} 进度已完成: ${current}/${total}`);
                }
              }
            }

            if (isReallyFinished) {
              reallyFinishedIds.push(taskId);
              if (taskStatus === 'COMPLETED' || taskStatus === 'FAILED') {
                handleTaskCompletionRef.current?.(taskInfo);
              }
            }
          } catch (error) {
            console.error(`获取任务 ${taskId} 结果失败:`, error);
            // 查询失败时，保守处理：认为任务已完成
            reallyFinishedIds.push(taskId);
          }
        }

        if (reallyFinishedIds.length > 0) {
          console.log('真正完成的任务:', reallyFinishedIds);
          setPendingTaskIds((prev) => prev.filter((id) => !reallyFinishedIds.includes(id)));
        }
      }
    }

  }, [
    setActiveTasks,
    setRuntimeSummary,
    setIsCheckingTasks,
    handleTaskCompletionRef,
    setPendingTaskIds,
  ]);

  const normalizeRuntimeSummary = useCallback((payload) => {
    if (!payload || typeof payload !== 'object') return null;
    const items = payload.tasks?.items;
    return {
      ...payload,
      tasks: {
        ...(payload.tasks || {}),
        items: Array.isArray(items) ? items : [],
      },
    };
  }, []);

  // Fallback polling (used when SSE is unavailable)
  const syncActiveTasks = useCallback(async () => {
    try {
      const response = await apiClient.get('/tasks/runtime-summary');
      const summary = normalizeRuntimeSummary(response.data);
      if (summary) {
        await handleTasksUpdate(summary.tasks.items, summary);
        return;
      }
    } catch (error) {
      console.error('同步任务运行概览失败:', error);
    }

    try {
      const response = await apiClient.get('/tasks/active');
      const tasks = Array.isArray(response.data) ? response.data : [];
      await handleTasksUpdate(tasks, null);
    } catch (error) {
      console.error('同步任务状态失败:', error);
    }
  }, [handleTasksUpdate, normalizeRuntimeSummary]);

  useEffect(() => {
    if (!currentUser || !licenseOk) return;

    // Initial fetch
    syncActiveTasks();

    // Try SSE first; fall back to polling on error
    let es = null;
    let fallbackInterval = null;

    const startSSE = () => {
      const baseURL = apiClient.defaults.baseURL || '';
      es = new EventSource(`${baseURL}/tasks/runtime-summary/stream`);

      es.onmessage = (event) => {
        try {
          const summary = normalizeRuntimeSummary(JSON.parse(event.data));
          if (summary) {
            handleTasksUpdate(summary.tasks.items, summary);
          }
        } catch (e) {
          console.error('SSE parse error:', e);
        }
      };

      es.onerror = () => {
        console.warn('SSE 连接断开，降级为轮询模式');
        es.close();
        es = null;
        if (!fallbackInterval) {
          fallbackInterval = setInterval(syncActiveTasks, ACTIVE_TASK_FALLBACK_POLL_MS);
        }
      };
    };

    startSSE();

    return () => {
      if (es) es.close();
      if (fallbackInterval) clearInterval(fallbackInterval);
    };
  }, [currentUser, licenseOk, syncActiveTasks, handleTasksUpdate]);

  const handleCancelActiveTasks = useCallback(() => {
    if (!cancelTaskPwd || activeTasks.length === 0) return;
    Promise.all(activeTasks.map((task) =>
      apiClient.post(`/tasks/${task.task_id}/force-cancel`, { password: cancelTaskPwd }).catch(() => {})
    )).then(() => {
      setCancelTaskPwd('');
      setShowCancelTask(false);
      syncActiveTasks();
    });
  }, [activeTasks, cancelTaskPwd, syncActiveTasks]);

  return {
    cancelTaskPwd,
    setCancelTaskPwd,
    showCancelTask,
    setShowCancelTask,
    handleCancelActiveTasks,
  };
}
