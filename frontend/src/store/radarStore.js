import { create } from 'zustand';

const s = (set, key) => (v) =>
    set((state) => ({ [key]: typeof v === 'function' ? v(state[key]) : v }));

const DEFAULT_LIMIT = 200;

const RADAR_SEARCH_DEFAULTS = {
    satellite: '', satellite_mode: '', receiving_station: '', imaging_mode: '',
    orbit_circle: '', acquisition_time_utc: '', product_type: '', polarization: '',
    product_level: '', product_unique_id: '', orbit_direction: '',
    has_orbit_data: '', imaging_date_from: '', imaging_date_to: '',
};

const RADAR_SEARCH_OPTIONS_DEFAULTS = {
    satellite: [], satellite_mode: [], receiving_station: [], imaging_mode: [],
    orbit_circle: [], acquisition_time_utc: [], product_type: [], polarization: [],
    product_level: [], product_unique_id: [], orbit_direction: [], imaging_dates: [],
};

export const useRadarStore = create((set) => ({
    allData: [],
    radarPagination: { total: 0, limit: DEFAULT_LIMIT, offset: 0, hasMore: false },
    radarPageInput: '1',
    radarPageInputTouched: false,
    hasRadarSearched: false,
    radarImagingDates: [],
    radarSearchDraft: RADAR_SEARCH_DEFAULTS,
    radarSearchApplied: RADAR_SEARCH_DEFAULTS,
    radarSearchOptions: RADAR_SEARCH_OPTIONS_DEFAULTS,
    radarSearchOptionsLoading: false,
    radarSearchAoiMode: 'none',
    radarSearchAppliedAoiMode: 'none',
    radarSearchFiles: null,
    radarSearchRegionOptions: { provinces: [], cities: [] },
    radarSearchRegionSelection: { province: '', city: '' },
    radarSearchAppliedRegionTreeId: '',
    radarSearchRegionLoading: false,
    radarSearchRegionError: '',
    radarSearchAoiToken: '',
    selectedSatelliteGroup: 'all',
    rebuildingPreviewIds: {},
    setAllData: s(set, 'allData'),
    setRadarPagination: s(set, 'radarPagination'),
    setRadarPageInput: s(set, 'radarPageInput'),
    setRadarPageInputTouched: s(set, 'radarPageInputTouched'),
    setHasRadarSearched: s(set, 'hasRadarSearched'),
    setRadarImagingDates: s(set, 'radarImagingDates'),
    setRadarSearchDraft: s(set, 'radarSearchDraft'),
    setRadarSearchApplied: s(set, 'radarSearchApplied'),
    setRadarSearchOptions: s(set, 'radarSearchOptions'),
    setRadarSearchOptionsLoading: s(set, 'radarSearchOptionsLoading'),
    setRadarSearchAoiMode: s(set, 'radarSearchAoiMode'),
    setRadarSearchAppliedAoiMode: s(set, 'radarSearchAppliedAoiMode'),
    setRadarSearchFiles: s(set, 'radarSearchFiles'),
    setRadarSearchRegionOptions: s(set, 'radarSearchRegionOptions'),
    setRadarSearchRegionSelection: s(set, 'radarSearchRegionSelection'),
    setRadarSearchAppliedRegionTreeId: s(set, 'radarSearchAppliedRegionTreeId'),
    setRadarSearchRegionLoading: s(set, 'radarSearchRegionLoading'),
    setRadarSearchRegionError: s(set, 'radarSearchRegionError'),
    setRadarSearchAoiToken: s(set, 'radarSearchAoiToken'),
    setSelectedSatelliteGroup: s(set, 'selectedSatelliteGroup'),
    setRebuildingPreviewIds: s(set, 'rebuildingPreviewIds'),
}));
