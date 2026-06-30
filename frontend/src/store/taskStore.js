import { create } from 'zustand';

const s = (set, key) => (v) =>
    set((state) => ({ [key]: typeof v === 'function' ? v(state[key]) : v }));

export const useTaskStore = create((set) => ({
    activeTasks: [],
    runtimeSummary: null,
    isCheckingTasks: true, // 初始化时假设正在检查任务，避免闪烁
    pendingTaskIds: [],
    setActiveTasks: s(set, 'activeTasks'),
    setRuntimeSummary: s(set, 'runtimeSummary'),
    setIsCheckingTasks: s(set, 'isCheckingTasks'),
    setPendingTaskIds: s(set, 'pendingTaskIds'),
}));
