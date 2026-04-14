import { useCallback, useEffect, useRef, useState } from 'react';
import apiClient from '../api/client';
import { normalizeTaskStatus } from '../utils/appUiHelpers';

export default function useGlobalTaskControl({
  currentUser,
  licenseOk,
  activeTasks,
  setActiveTasks,
  pendingTaskIds,
  setPendingTaskIds,
  isGlobalLocked,
  setIsGlobalLocked,
  setIsCheckingTasks,
  handleTaskCompletionRef,
  initializeAppDataRef,
  addLog,
}) {
  const [forceUnlockPwd, setForceUnlockPwd] = useState('');
  const [showForceUnlock, setShowForceUnlock] = useState(false);
  const lastLockTimeRef = useRef(null);

  const isGlobalLockedRef = useRef(isGlobalLocked);
  useEffect(() => {
    isGlobalLockedRef.current = isGlobalLocked;
    if (isGlobalLocked) {
      lastLockTimeRef.current = Date.now();
    }
  }, [isGlobalLocked]);

  // Stable refs so SSE handler doesn't need to re-subscribe on every render
  const pendingTaskIdsRef = useRef(pendingTaskIds);
  useEffect(() => { pendingTaskIdsRef.current = pendingTaskIds; }, [pendingTaskIds]);

  const handleTasksUpdate = useCallback(async (tasks) => {
    setActiveTasks(tasks);
    const hasRunningTasks = tasks.length > 0;

    // 首次检查完成，清除检查状态
    setIsCheckingTasks(false);

    const currentPending = pendingTaskIdsRef.current;
    let updatedPending = currentPending;

    // 如果 pendingTaskIds 为空，但 activeTasks 有任务，说明是刷新后的初始化
    // 需要将 activeTasks 中的任务添加到 pendingTaskIds
    if (currentPending.length === 0 && hasRunningTasks) {
      const activeTaskIds = tasks.map((t) => t.task_id);
      console.log('初始化：将活跃任务添加到 pending 列表:', activeTaskIds);
      setPendingTaskIds(activeTaskIds);
      updatedPending = activeTaskIds;
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
          setPendingTaskIds((prev) => {
            const newPending = prev.filter((id) => !reallyFinishedIds.includes(id));
            updatedPending = newPending;
            return newPending;
          });
        } else {
          // 没有真正完成的任务，保持 updatedPending 不变
          updatedPending = currentPending;
        }
      }
    }

    // 只有当没有运行中的任务且没有待处理的任务时，才解锁
    const shouldBeLocked = hasRunningTasks || updatedPending.length > 0;

    if (shouldBeLocked !== isGlobalLockedRef.current) {
      setIsGlobalLocked(shouldBeLocked);
      if (!shouldBeLocked) {
        addLog('success', '后台任务已完成，正在同步最新数据...');
        setTimeout(() => {
          initializeAppDataRef.current?.({ refreshRadarSearch: true });
        }, 500);
      }
    }
  }, [
    setActiveTasks,
    setIsCheckingTasks,
    handleTaskCompletionRef,
    setPendingTaskIds,
    setIsGlobalLocked,
    addLog,
    initializeAppDataRef,
  ]);

  // Fallback polling (used when SSE is unavailable)
  const syncActiveTasks = useCallback(async () => {
    try {
      const response = await apiClient.get('/tasks/active');
      const tasks = Array.isArray(response.data) ? response.data : [];
      await handleTasksUpdate(tasks);
    } catch (error) {
      console.error('同步任务状态失败:', error);
    }
  }, [handleTasksUpdate]);

  useEffect(() => {
    if (!currentUser || !licenseOk) return;

    // Initial fetch
    syncActiveTasks();

    // Try SSE first; fall back to polling on error
    let es = null;
    let fallbackInterval = null;

    const startSSE = () => {
      const baseURL = apiClient.defaults.baseURL || '';
      es = new EventSource(`${baseURL}/tasks/active/stream`);

      es.onmessage = (event) => {
        try {
          const tasks = JSON.parse(event.data);
          handleTasksUpdate(Array.isArray(tasks) ? tasks : []);
        } catch (e) {
          console.error('SSE parse error:', e);
        }
      };

      es.onerror = () => {
        console.warn('SSE 连接断开，降级为轮询模式');
        es.close();
        es = null;
        if (!fallbackInterval) {
          fallbackInterval = setInterval(syncActiveTasks, 5000);
        }
      };
    };

    startSSE();

    return () => {
      if (es) es.close();
      if (fallbackInterval) clearInterval(fallbackInterval);
    };
  }, [currentUser, licenseOk, syncActiveTasks, handleTasksUpdate]);

  const handleForceUnlock = useCallback(() => {
    if (!forceUnlockPwd || activeTasks.length === 0) return;
    Promise.all(activeTasks.map((task) =>
      apiClient.post(`/tasks/${task.task_id}/force-cancel`, { password: forceUnlockPwd }).catch(() => {})
    )).then(() => {
      setForceUnlockPwd('');
      setShowForceUnlock(false);
      syncActiveTasks();
    });
  }, [activeTasks, forceUnlockPwd, syncActiveTasks]);

  return {
    forceUnlockPwd,
    setForceUnlockPwd,
    showForceUnlock,
    setShowForceUnlock,
    handleForceUnlock,
  };
}
