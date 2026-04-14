import { create } from 'zustand';

const TILE_LAYER_DEFAULT_KEY = 'gaode_shp';

const s = (set, key) => (v) =>
    set((state) => ({ [key]: typeof v === 'function' ? v(state[key]) : v }));

export const useUiStore = create((set) => ({
    leftPanelTab: 'ingest',
    leftPanelWidth: 380,
    rightPanelWidth: 360,
    isResizing: false,
    showStats: false,
    showDataInfo: false,
    selectedDataInfo: null,
    showDates: false,
    baseLayerKey: TILE_LAYER_DEFAULT_KEY,
    isLoading: false,
    logs: [],
    setLeftPanelTab: s(set, 'leftPanelTab'),
    setLeftPanelWidth: s(set, 'leftPanelWidth'),
    setRightPanelWidth: s(set, 'rightPanelWidth'),
    setIsResizing: s(set, 'isResizing'),
    setShowStats: s(set, 'showStats'),
    setShowDataInfo: s(set, 'showDataInfo'),
    setSelectedDataInfo: s(set, 'selectedDataInfo'),
    setShowDates: s(set, 'showDates'),
    setBaseLayerKey: s(set, 'baseLayerKey'),
    setIsLoading: s(set, 'isLoading'),
    setLogs: s(set, 'logs'),
    addLog: (type, message) =>
        set((state) => ({
            logs: [{ time: new Date().toLocaleTimeString(), type, message }, ...state.logs],
        })),
}));
