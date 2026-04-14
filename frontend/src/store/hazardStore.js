import { create } from 'zustand';

const s = (set, key) => (v) =>
    set((state) => ({ [key]: typeof v === 'function' ? v(state[key]) : v }));

export const useHazardStore = create((set) => ({
    hazardPoints: [],
    showHazardPoints: true,
    focusedHazardPoint: null,
    setHazardPoints: s(set, 'hazardPoints'),
    setShowHazardPoints: s(set, 'showHazardPoints'),
    setFocusedHazardPoint: s(set, 'focusedHazardPoint'),
}));
