/**
 * useRadarSearch — radar data search logic extracted from App.jsx
 *
 * Contains: fetchRadarImagingDates, fetchRadarSearchOptions, fetchAllData,
 * applyRadarSearch, resetRadarSearch, searchAllRadarData,
 * refreshCurrentRadarSearch, processAndSetAllData
 */
import { useCallback } from 'react';
import apiClient from '../api/client';
import { useRadarStore } from '../store';
import {
    DEFAULT_LIST_PAGE_SIZE,
    RADAR_SEARCH_DEFAULTS,
    RADAR_SEARCH_OPTIONS_DEFAULTS,
    BATCH_API_PAGE_LIMIT,
    BATCH_API_MAX_PAGES,
    SATELLITE_GROUPS,
} from '../config/appConstants';
import {
    normalizePreviewStatus,
    buildRadarSearchFormData,
    normalizeRadarSearchCriteria,
    getSelectedRegionTreeId,
} from '../utils/appUiHelpers';
import { normalizePagePayload } from '../utils/appHelpers';

export default function useRadarSearch({
    addLog,
    setIsLoading,
    setAllData,
    setRadarImagingDates,
    setRadarSearchOptions,
    setRadarSearchOptionsLoading,
    radarPagination,
    setRadarPagination,
    radarSearchDraft,
    setRadarSearchDraft,
    radarSearchApplied,
    setRadarSearchApplied,
    radarSearchAoiMode,
    setRadarSearchAoiMode,
    setRadarSearchAppliedAoiMode,
    radarSearchAppliedAoiMode,
    radarSearchFiles,
    setRadarSearchFiles,
    setRadarSearchRegionOptions,
    radarSearchRegionSelection,
    setRadarSearchRegionSelection,
    radarSearchAppliedRegionTreeId,
    setRadarSearchAppliedRegionTreeId,
    setRadarSearchRegionError,
    radarSearchAoiToken,
    setRadarSearchAoiToken,
    hasRadarSearched,
    setHasRadarSearched,
    selectedSatelliteGroup,
    setSelectedSatelliteGroup,
    radarSearchRequestSeqRef,
    clearRadarSearchResults,
    clearRadarMapLayers,
}) {
    const getSatelliteCatalog = () => {
        const satellites = useRadarStore.getState().radarSearchOptions?.satellite;
        return Array.isArray(satellites) ? satellites.filter(Boolean) : [];
    };

    const getSatellitesForGroup = (groupKey, satellites = getSatelliteCatalog()) => {
        const group = SATELLITE_GROUPS.find((item) => item.key === groupKey);
        if (!group) return [];
        return satellites.filter((sat) =>
            group.prefixes.some((prefix) => String(sat || '').startsWith(prefix))
        );
    };

    const fetchRadarImagingDates = useCallback(async () => {
        try {
            const response = await apiClient.get('/radar-data/imaging-dates');
            const dates = Array.isArray(response?.data?.dates) ? response.data.dates : [];
            setRadarImagingDates(dates);
        } catch (error) {
            console.error("获取成像日期列表失败:", error);
            setRadarImagingDates([]);
        }
    }, [setRadarImagingDates]);

    const fetchRadarSearchOptions = useCallback(async (satelliteFilter) => {
        try {
            setRadarSearchOptionsLoading(true);
            const params = {};
            const storeState = useRadarStore.getState();
            const satelliteCatalog = getSatelliteCatalog();
            const hasExplicitSatelliteFilter = Array.isArray(satelliteFilter);
            let resolvedSatelliteFilter = hasExplicitSatelliteFilter
                ? satelliteFilter.filter(Boolean)
                : [];

            if (!hasExplicitSatelliteFilter) {
                const groupKey = storeState.selectedSatelliteGroup;
                if (groupKey && groupKey !== 'all') {
                    resolvedSatelliteFilter = getSatellitesForGroup(groupKey, satelliteCatalog);
                }
            }

            if (resolvedSatelliteFilter.length > 0) {
                params.satellite = resolvedSatelliteFilter;
            }
            const response = await apiClient.get('/radar-data/search/options', { params });
            const payload = response?.data && typeof response.data === 'object' ? response.data : {};
            const payloadSatellites = Array.isArray(payload.satellite) ? payload.satellite.filter(Boolean) : [];
            const nextSatelliteCatalog = satelliteCatalog.length > payloadSatellites.length
                ? satelliteCatalog
                : payloadSatellites;
            setRadarSearchOptions({
                satellite: nextSatelliteCatalog,
                satellite_mode: Array.isArray(payload.satellite_mode) ? payload.satellite_mode : [],
                receiving_station: Array.isArray(payload.receiving_station) ? payload.receiving_station : [],
                imaging_mode: Array.isArray(payload.imaging_mode) ? payload.imaging_mode : [],
                orbit_circle: Array.isArray(payload.orbit_circle) ? payload.orbit_circle : [],
                acquisition_time_utc: Array.isArray(payload.acquisition_time_utc) ? payload.acquisition_time_utc : [],
                product_type: Array.isArray(payload.product_type) ? payload.product_type : [],
                polarization: Array.isArray(payload.polarization) ? payload.polarization : [],
                product_level: Array.isArray(payload.product_level) ? payload.product_level : [],
                product_unique_id: Array.isArray(payload.product_unique_id) ? payload.product_unique_id : [],
                orbit_direction: Array.isArray(payload.orbit_direction) ? payload.orbit_direction : [],
                imaging_dates: Array.isArray(payload.imaging_dates) ? payload.imaging_dates : [],
            });
        } catch (error) {
            console.error('获取源数据检索选项失败:', error);
            setRadarSearchOptions(RADAR_SEARCH_OPTIONS_DEFAULTS);
        } finally {
            setRadarSearchOptionsLoading(false);
        }
    }, [setRadarSearchOptions, setRadarSearchOptionsLoading]);

    const changeSatelliteGroup = useCallback((groupKey) => {
        setSelectedSatelliteGroup(groupKey);
        // Clear sub-filters that may be invalid for the new satellite group
        setRadarSearchDraft((prev) => ({
            ...prev,
            satellite: '',
            imaging_mode: '',
            polarization: '',
            satellite_mode: '',
            receiving_station: '',
            orbit_circle: '',
            acquisition_time_utc: '',
            product_type: '',
            product_level: '',
            product_unique_id: '',
            orbit_direction: '',
        }));
        if (groupKey === 'all') {
            fetchRadarSearchOptions([]);
        } else {
            const matched = getSatellitesForGroup(groupKey);
            if (matched.length > 0) {
                fetchRadarSearchOptions(matched);
            }
        }
    }, [setSelectedSatelliteGroup, setRadarSearchDraft, fetchRadarSearchOptions]);

    const processAndSetAllData = useCallback((data) => {
        const nameCounts = {};
        const dataWithDisplayNames = data.map(item => {
            const baseName = `${item.satellite}_${item.imaging_mode}_${item.imaging_date}`;
            nameCounts[baseName] = (nameCounts[baseName] || 0) + 1;
            const count = nameCounts[baseName];
            const displayName = count > 1 ? `${baseName}_${count - 1}` : baseName;
            const previewStatus = normalizePreviewStatus(item.preview_cache_status);
            return {
                ...item,
                isVisible: false,
                isPreviewVisible: false,
                displayName,
                previewStatus,
                previewFallbackInUse: false,
                previewHasGeoCache: previewStatus === 'READY',
                previewHasRawCache: false,
                previewSourceFound: false,
                previewMessage: '',
                previewError: item.preview_cache_error || '',
                previewCacheKey: item.preview_cache_updated_at || `${previewStatus}-${item.id}`,
            };
        });
        setAllData(dataWithDisplayNames);
    }, [setAllData]);

    const fetchAllData = useCallback(async (options = {}) => {
        const requestedLimit = Math.max(
            1,
            Math.min(
                Number(options.limit ?? radarPagination.limit ?? DEFAULT_LIST_PAGE_SIZE) || DEFAULT_LIST_PAGE_SIZE,
                2000
            )
        );
        const requestedOffset = Math.max(
            0,
            Number(options.offset ?? radarPagination.offset ?? 0) || 0
        );
        const requestId = Number.isFinite(Number(options.requestId))
            ? Number(options.requestId)
            : (radarSearchRequestSeqRef.current + 1);
        if (!Number.isFinite(Number(options.requestId))) {
            radarSearchRequestSeqRef.current = requestId;
        }
        const isStaleRequest = () => requestId !== radarSearchRequestSeqRef.current;
        const effectiveCriteria = options.criteria ?? radarSearchApplied;
        const effectiveAoiMode = options.aoiMode ?? radarSearchAppliedAoiMode;
        const effectiveRegionTreeId = options.regionTreeId ?? radarSearchAppliedRegionTreeId;
        const effectiveFiles = options.files ?? null;
        const effectiveAoiToken = options.aoiToken ?? radarSearchAoiToken;
        addLog('info', `正在从后端获取源数据（offset=${requestedOffset}, limit=${requestedLimit}）...`);
        try {
            const formData = buildRadarSearchFormData({
                limit: requestedLimit,
                offset: requestedOffset,
                criteria: effectiveCriteria,
                aoiMode: effectiveAoiMode,
                regionTreeId: effectiveRegionTreeId,
                aoiToken: effectiveAoiToken,
                files: effectiveFiles,
            });
            const response = await apiClient.post('/radar-data/search', formData);
            if (isStaleRequest()) {
                return false;
            }
            const pagePayload = normalizePagePayload(response.data, requestedLimit, requestedOffset);
            if (
                pagePayload.items.length === 0 &&
                pagePayload.total > 0 &&
                requestedOffset >= pagePayload.total &&
                requestedOffset > 0
            ) {
                const fallbackOffset = Math.max(0, requestedOffset - requestedLimit);
                await fetchAllData({
                    limit: requestedLimit,
                    offset: fallbackOffset,
                    criteria: effectiveCriteria,
                    aoiMode: effectiveAoiMode,
                    regionTreeId: effectiveRegionTreeId,
                    aoiToken: effectiveAoiToken,
                    files: effectiveFiles,
                    requestId,
                });
                return true;
            }
            const returnedAoiToken = typeof response?.data?.aoi_token === 'string'
                ? response.data.aoi_token
                : '';
            if (isStaleRequest()) {
                return false;
            }
            if (effectiveAoiMode === 'none') {
                setRadarSearchAoiToken('');
            } else if (returnedAoiToken) {
                setRadarSearchAoiToken(returnedAoiToken);
            } else if (!effectiveFiles) {
                setRadarSearchAoiToken(effectiveAoiToken || '');
            }
            setRadarPagination({
                total: pagePayload.total,
                limit: pagePayload.limit,
                offset: pagePayload.offset,
                hasMore: pagePayload.hasMore,
            });

            clearRadarMapLayers();
            processAndSetAllData(pagePayload.items);

            const currentPage = Math.floor(pagePayload.offset / pagePayload.limit) + 1;
            const totalPages = Math.max(1, Math.ceil(pagePayload.total / pagePayload.limit));
            addLog('success', `源数据已加载：第 ${currentPage}/${totalPages} 页，当前页 ${pagePayload.items.length} 条，总计 ${pagePayload.total} 条。`);
            return true;
        } catch (error) {
            if (isStaleRequest()) {
                return false;
            }
            console.error("获取全部数据失败:", error);
            addLog('error', '无法连接到后端或获取数据失败。');
            return false;
        }
    }, [
        radarPagination.limit, radarPagination.offset,
        radarSearchApplied, radarSearchAppliedAoiMode,
        radarSearchAppliedRegionTreeId, radarSearchAoiToken,
        radarSearchRequestSeqRef,
        addLog, setRadarSearchAoiToken, setRadarPagination,
        clearRadarMapLayers, processAndSetAllData,
    ]);

    const applyRadarSearch = useCallback(async () => {
        const draftWithSatelliteGroup = { ...radarSearchDraft };
        if (selectedSatelliteGroup && selectedSatelliteGroup !== 'all') {
            const matched = getSatellitesForGroup(selectedSatelliteGroup);
            if (matched.length > 0) {
                draftWithSatelliteGroup.satellite = matched.join(',');
            }
        }
        const normalizedCriteria = normalizeRadarSearchCriteria(draftWithSatelliteGroup, RADAR_SEARCH_DEFAULTS);
        const selectedRegionTreeId = getSelectedRegionTreeId(radarSearchRegionSelection);
        const hasUploadedFiles = !!(radarSearchFiles && radarSearchFiles.length > 0);
        const requestAoiToken = radarSearchAoiMode === 'shp' && !hasUploadedFiles
            ? radarSearchAoiToken
            : '';

        if (radarSearchAoiMode === 'region' && !selectedRegionTreeId) {
            addLog('warn', '请先选择行政区。');
            return;
        }
        if (radarSearchAoiMode === 'shp' && !hasUploadedFiles && !radarSearchAoiToken) {
            addLog('warn', '请先选择包含 .shp 的 AOI 文件。');
            return;
        }

        setRadarSearchApplied(normalizedCriteria);
        setRadarSearchAppliedAoiMode(radarSearchAoiMode);
        setRadarSearchAppliedRegionTreeId(radarSearchAoiMode === 'region' ? selectedRegionTreeId : '');
        if (radarSearchAoiMode !== 'shp') {
            setRadarSearchAoiToken('');
        } else if (hasUploadedFiles) {
            setRadarSearchAoiToken('');
        }

        const requestId = radarSearchRequestSeqRef.current + 1;
        radarSearchRequestSeqRef.current = requestId;
        setHasRadarSearched(true);
        setIsLoading(true);
        addLog('info', '开始检索源数据...');
        clearRadarSearchResults({ limit: radarPagination.limit });
        try {
            await fetchAllData({
                limit: radarPagination.limit,
                offset: 0,
                criteria: normalizedCriteria,
                aoiMode: radarSearchAoiMode,
                regionTreeId: radarSearchAoiMode === 'region' ? selectedRegionTreeId : '',
                files: hasUploadedFiles ? radarSearchFiles : null,
                aoiToken: requestAoiToken,
                requestId,
            });
        } finally {
            setIsLoading(false);
            if (hasUploadedFiles) {
                setRadarSearchFiles(null);
            }
        }
    }, [
        radarSearchDraft, radarSearchRegionSelection, radarSearchFiles,
        radarSearchAoiMode, radarSearchAoiToken, radarPagination.limit,
        selectedSatelliteGroup, radarSearchRequestSeqRef,
        addLog, setIsLoading, setRadarSearchApplied, setRadarSearchAppliedAoiMode,
        setRadarSearchAppliedRegionTreeId, setRadarSearchAoiToken,
        setHasRadarSearched, setRadarSearchFiles,
        clearRadarSearchResults, fetchAllData,
    ]);

    const resetRadarSearch = useCallback(() => {
        setRadarSearchDraft(RADAR_SEARCH_DEFAULTS);
        setRadarSearchApplied(RADAR_SEARCH_DEFAULTS);
        setRadarSearchAoiMode('none');
        setRadarSearchAppliedAoiMode('none');
        setRadarSearchFiles(null);
        setRadarSearchRegionOptions({ provinces: [], cities: [] });
        setRadarSearchRegionSelection({ province: '', city: '' });
        setRadarSearchAppliedRegionTreeId('');
        setRadarSearchRegionError('');
        setRadarSearchAoiToken('');
        setSelectedSatelliteGroup('all');
        radarSearchRequestSeqRef.current += 1;
        setHasRadarSearched(false);
        clearRadarSearchResults({ limit: radarPagination.limit });
        fetchRadarSearchOptions([]);
        addLog('info', '已清除检索条件，请点击"搜索"或"搜索全部"获取数据。');
    }, [
        radarPagination.limit, radarSearchRequestSeqRef,
        addLog, setRadarSearchDraft, setRadarSearchApplied,
        setRadarSearchAoiMode, setRadarSearchAppliedAoiMode,
        setRadarSearchFiles, setRadarSearchRegionOptions,
        setRadarSearchRegionSelection, setRadarSearchAppliedRegionTreeId,
        setRadarSearchRegionError, setRadarSearchAoiToken,
        setSelectedSatelliteGroup, setHasRadarSearched,
        clearRadarSearchResults, fetchRadarSearchOptions,
    ]);

    const searchAllRadarData = useCallback(async () => {
        const requestId = radarSearchRequestSeqRef.current + 1;
        radarSearchRequestSeqRef.current = requestId;

        setRadarSearchDraft(RADAR_SEARCH_DEFAULTS);
        setRadarSearchApplied(RADAR_SEARCH_DEFAULTS);
        setRadarSearchAoiMode('none');
        setRadarSearchAppliedAoiMode('none');
        setRadarSearchFiles(null);
        setRadarSearchRegionOptions({ provinces: [], cities: [] });
        setRadarSearchRegionSelection({ province: '', city: '' });
        setRadarSearchAppliedRegionTreeId('');
        setRadarSearchRegionError('');
        setRadarSearchAoiToken('');
        setSelectedSatelliteGroup('all');
        fetchRadarSearchOptions([]);

        setHasRadarSearched(true);
        setIsLoading(true);
        addLog('info', '开始执行无条件检索（搜索全部源数据）...');
        clearRadarSearchResults({ limit: radarPagination.limit });
        try {
            await fetchAllData({
                limit: radarPagination.limit,
                offset: 0,
                criteria: RADAR_SEARCH_DEFAULTS,
                aoiMode: 'none',
                regionTreeId: '',
                files: null,
                aoiToken: '',
                requestId,
            });
        } finally {
            setIsLoading(false);
        }
    }, [
        radarPagination.limit, radarSearchRequestSeqRef,
        addLog, setIsLoading, setRadarSearchDraft, setRadarSearchApplied,
        setRadarSearchAoiMode, setRadarSearchAppliedAoiMode,
        setRadarSearchFiles, setRadarSearchRegionOptions,
        setRadarSearchRegionSelection, setRadarSearchAppliedRegionTreeId,
        setRadarSearchRegionError, setRadarSearchAoiToken,
        setSelectedSatelliteGroup, setHasRadarSearched,
        clearRadarSearchResults, fetchAllData, fetchRadarSearchOptions,
    ]);

    const refreshCurrentRadarSearch = useCallback(async () => {
        if (!hasRadarSearched) {
            addLog('warn', '请先执行一次源数据检索。');
            return;
        }
        const requestId = radarSearchRequestSeqRef.current + 1;
        radarSearchRequestSeqRef.current = requestId;
        setIsLoading(true);
        addLog('info', '正在刷新当前源数据检索结果...');
        try {
            await fetchAllData({
                limit: radarPagination.limit,
                offset: radarPagination.offset,
                criteria: radarSearchApplied,
                aoiMode: radarSearchAppliedAoiMode,
                regionTreeId: radarSearchAppliedRegionTreeId,
                files: null,
                aoiToken: radarSearchAoiToken,
                requestId,
            });
        } finally {
            setIsLoading(false);
        }
    }, [
        hasRadarSearched, radarPagination.limit, radarPagination.offset,
        radarSearchApplied, radarSearchAppliedAoiMode,
        radarSearchAppliedRegionTreeId, radarSearchAoiToken,
        radarSearchRequestSeqRef,
        addLog, setIsLoading, fetchAllData,
    ]);

    return {
        fetchRadarImagingDates,
        fetchRadarSearchOptions,
        fetchAllData,
        applyRadarSearch,
        resetRadarSearch,
        searchAllRadarData,
        refreshCurrentRadarSearch,
        processAndSetAllData,
        changeSatelliteGroup,
    };
}
