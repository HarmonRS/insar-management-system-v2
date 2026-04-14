import { create } from 'zustand';

const s = (set, key) => (v) =>
    set((state) => ({ [key]: typeof v === 'function' ? v(state[key]) : v }));

const DEFAULT_LIMIT = 200;

export const useDinsarStore = create((set) => ({
    dinsarResults: [],
    dinsarPagination: { total: 0, limit: DEFAULT_LIMIT, offset: 0, hasMore: false },
    dinsarPageInput: '1',
    dinsarPageInputTouched: false,
    aiStatus: null,
    scoreFilter: 0,
    traceSearch: '',
    strategyFilter: '__ALL__',
    activeAiReport: null,
    setDinsarResults: s(set, 'dinsarResults'),
    setDinsarPagination: s(set, 'dinsarPagination'),
    setDinsarPageInput: s(set, 'dinsarPageInput'),
    setDinsarPageInputTouched: s(set, 'dinsarPageInputTouched'),
    setAiStatus: s(set, 'aiStatus'),
    setScoreFilter: s(set, 'scoreFilter'),
    setTraceSearch: s(set, 'traceSearch'),
    setStrategyFilter: s(set, 'strategyFilter'),
    setActiveAiReport: s(set, 'activeAiReport'),
}));
