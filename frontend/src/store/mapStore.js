import { create } from 'zustand';

const s = (set, key) => (v) =>
    set((state) => ({ [key]: typeof v === 'function' ? v(state[key]) : v }));

export const useMapStore = create((set) => ({
    aoiLayer: null,
    showMapRegionLocator: false,
    mapRegionOptions: { provinces: [], cities: [] },
    mapRegionSelection: { province: '', city: '' },
    mapRegionLoading: false,
    mapRegionLocating: false,
    mapRegionError: '',
    mapRegionLocatedName: '',
    setAoiLayer: s(set, 'aoiLayer'),
    setShowMapRegionLocator: s(set, 'showMapRegionLocator'),
    setMapRegionOptions: s(set, 'mapRegionOptions'),
    setMapRegionSelection: s(set, 'mapRegionSelection'),
    setMapRegionLoading: s(set, 'mapRegionLoading'),
    setMapRegionLocating: s(set, 'mapRegionLocating'),
    setMapRegionError: s(set, 'mapRegionError'),
    setMapRegionLocatedName: s(set, 'mapRegionLocatedName'),
}));
