import { create } from 'zustand';

const s = (set, key) => (v) =>
    set((state) => ({ [key]: typeof v === 'function' ? v(state[key]) : v }));

export const useTaskStore = create((set) => ({
    activeTasks: [],
    isGlobalLocked: false,
    isCheckingTasks: true, // 初始化时假设正在检查任务，避免闪烁
    pendingTaskIds: [],
    nonBlockingTaskIds: [],
    setActiveTasks: s(set, 'activeTasks'),
    setIsGlobalLocked: s(set, 'isGlobalLocked'),
    setIsCheckingTasks: s(set, 'isCheckingTasks'),
    setPendingTaskIds: s(set, 'pendingTaskIds'),
    setNonBlockingTaskIds: s(set, 'nonBlockingTaskIds'),
}));
