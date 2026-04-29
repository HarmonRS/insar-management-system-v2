import { create } from 'zustand';

const s = (set, key) => (v) =>
    set((state) => ({ [key]: typeof v === 'function' ? v(state[key]) : v }));

export const useBatchStore = create((set) => ({
    batchTab: 'dinsar',
    dinsarBatches: [],
    psBatches: [],
    selectedBatchId: '',
    pendingTimeseriesBatchId: '',
    batchItems: [],
    batchLoading: false,
    batchError: '',
    setBatchTab: s(set, 'batchTab'),
    setDinsarBatches: s(set, 'dinsarBatches'),
    setPsBatches: s(set, 'psBatches'),
    setSelectedBatchId: s(set, 'selectedBatchId'),
    setPendingTimeseriesBatchId: s(set, 'pendingTimeseriesBatchId'),
    setBatchItems: s(set, 'batchItems'),
    setBatchLoading: s(set, 'batchLoading'),
    setBatchError: s(set, 'batchError'),
}));
