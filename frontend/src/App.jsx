import { useEffect, useRef, useCallback } from 'react';
import L from 'leaflet';
import { useShallow } from 'zustand/react/shallow';
import 'leaflet/dist/leaflet.css';
import './App.css';
import LoginPage from './LoginPage';
import AppMapWorkspace from './components/app/AppMapWorkspace';
import AppOverlays from './components/app/AppOverlays';
import AppSidePanel from './components/app/AppSidePanel';
import AppStatusHeader from './components/app/AppStatusHeader';
import { useI18n } from './i18n/I18nContext';
import apiClient from './api/client';
import { getSbasInsarProductAssetUrl } from './api/sbasInsarProducts';
import {
    useAuthStore, useTaskStore, useUiStore, useRadarStore,
    useDinsarStore, useBatchStore, usePairingStore, useHazardStore, useMapStore,
} from './store';
import useAppAuthLifecycle from './hooks/useAppAuthLifecycle';
import useGlobalTaskControl from './hooks/useGlobalTaskControl';
import useRegionAoiHandlers from './hooks/useRegionAoiHandlers';
import usePaginationControls from './hooks/usePaginationControls';
import useRadarSearch from './hooks/useRadarSearch';
import useBatchOperations from './hooks/useBatchOperations';
import useDinsarOperations from './hooks/useDinsarOperations';
import usePairingLogic from './hooks/usePairingLogic';
import useMapExport from './hooks/useMapExport';
import {
    TILE_LAYER_OPTIONS,
    TILE_LAYER_BOUNDS,
    NATIONAL_BOUNDARY_GEOJSON_URL,
    getBaseLayerConfig,
    DEFAULT_LIST_PAGE_SIZE,
    FULL_WIDTH_LEFT_TABS,
} from './config/appConstants';
import { escapeHtml, formatCoordinate } from './utils/appHelpers';
import {
    normalizePreviewStatus,
    formatYmd,
    parseDatesFromName,
} from './utils/appUiHelpers';
import {
    DINSAR_STRATEGY_ALL,
    filterDinsarResults,
} from './utils/dinsarResultFilters';
import { DINSAR_ENGINE_ALL, getDinsarEngineMeta } from './utils/dinsarEngines';

const NATIONAL_BOUNDARY_STATIC_URL = '/geojson/\u5168\u56fd\u884c\u653f\u533a.geojson';

const toFiniteNumber = (value) => {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : null;
};

const formatMapNumber = (value, digits = 2) => {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric.toFixed(digits) : '-';
};

const getSbasProductBounds = (product) => {
    const coverage = product?.geographic_coverage || {};
    const bbox = coverage.bbox || {};
    const minLon = toFiniteNumber(product?.min_lon ?? bbox.min_lon);
    const minLat = toFiniteNumber(product?.min_lat ?? bbox.min_lat);
    const maxLon = toFiniteNumber(product?.max_lon ?? bbox.max_lon);
    const maxLat = toFiniteNumber(product?.max_lat ?? bbox.max_lat);
    if ([minLon, minLat, maxLon, maxLat].some(value => value === null)) return null;
    if (minLon >= maxLon || minLat >= maxLat) return null;
    return [[minLat, minLon], [maxLat, maxLon]];
};

const findSbasAsset = (detail, roles) => {
    const roleSet = new Set(roles);
    return (detail?.assets || []).find(asset => roleSet.has(asset.asset_role) && asset.exists_flag);
};

const sbasAssetCacheKey = (asset) => (
    [asset?.id, asset?.file_size, asset?.updated_at || asset?.created_at || asset?.relative_path]
        .filter(Boolean)
        .join(':')
);

const sbasRateColor = (rate) => {
    const numeric = Number(rate);
    if (!Number.isFinite(numeric)) return '#64748b';
    if (numeric <= -30) return '#1d4ed8';
    if (numeric < -5) return '#38bdf8';
    if (numeric <= 5) return '#16a34a';
    if (numeric < 30) return '#f59e0b';
    return '#dc2626';
};

const SBAS_OVERVIEW_COLORS = ['#1d4ed8', '#dc2626', '#059669', '#7c3aed', '#d97706', '#0f766e'];

const normalizeSbasDisplacements = (rows) => (Array.isArray(rows) ? rows : [])
    .map((item) => {
        const date = String(item?.date || '').trim();
        const time = Date.parse(`${date}T00:00:00Z`);
        const displacement = Number(item?.displacement_mm ?? item?.displacement ?? item?.value);
        if (!date || !Number.isFinite(time) || !Number.isFinite(displacement)) return null;
        return { date, time, displacement };
    })
    .filter(Boolean)
    .sort((left, right) => left.time - right.time);

const buildSbasSparklineSvg = (rows) => {
    const values = normalizeSbasDisplacements(rows);
    if (values.length < 2) return '';
    const width = 220;
    const height = 72;
    const padX = 12;
    const padY = 10;
    const minTime = Math.min(...values.map(item => item.time));
    const maxTime = Math.max(...values.map(item => item.time));
    const minValue = Math.min(...values.map(item => item.displacement), 0);
    const maxValue = Math.max(...values.map(item => item.displacement), 0);
    const timeSpan = Math.max(1, maxTime - minTime);
    const valueSpan = Math.max(1e-9, maxValue - minValue);
    const xScale = (time) => padX + ((time - minTime) / timeSpan) * (width - padX * 2);
    const yScale = (value) => height - padY - ((value - minValue) / valueSpan) * (height - padY * 2);
    const points = values.map(item => `${xScale(item.time).toFixed(1)},${yScale(item.displacement).toFixed(1)}`).join(' ');
    const zeroY = yScale(0).toFixed(1);
    const minLabel = escapeHtml(formatMapNumber(minValue, 1));
    const maxLabel = escapeHtml(formatMapNumber(maxValue, 1));
    return `
        <svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-label="SBAS displacement sparkline">
            <rect x="0" y="0" width="${width}" height="${height}" fill="#f8fafc" rx="6"></rect>
            <line x1="${padX}" x2="${width - padX}" y1="${zeroY}" y2="${zeroY}" stroke="#94a3b8" stroke-width="1" stroke-dasharray="3 3"></line>
            <polyline points="${points}" fill="none" stroke="#1d4ed8" stroke-width="2"></polyline>
            ${values.map(item => `<circle cx="${xScale(item.time).toFixed(1)}" cy="${yScale(item.displacement).toFixed(1)}" r="2.4" fill="#1d4ed8"></circle>`).join('')}
            <text x="${padX}" y="10" font-size="9" fill="#64748b">${maxLabel} mm</text>
            <text x="${padX}" y="${height - 4}" font-size="9" fill="#64748b">${minLabel} mm</text>
        </svg>
    `;
};

const buildSbasPointPopupHtml = (point, options = {}) => {
    const matched = point?.matched || {};
    const pointId = escapeHtml(point?.point_id || options.pointId || 'SBAS point');
    const label = escapeHtml(point?.selection_label || options.label || pointId);
    const rate = point?.deformation_rate_mm_per_year ?? point?.los_rate_mm_per_year ?? matched.los_rate_mm_per_year;
    const lon = point?.lon ?? matched.lon;
    const lat = point?.lat ?? matched.lat;
    const nearestNote = matched.used_nearest
        ? `<div><strong>匹配:</strong> 最近有效像元，距离 ${escapeHtml(formatMapNumber(matched.distance_m, 1))} m</div>`
        : '';
    return `
        <div class="sbas-popup" style="min-width:240px">
            <div style="font-weight:800;margin-bottom:6px">${label}</div>
            <div><strong>ID:</strong> <span class="mono">${pointId}</span></div>
            <div><strong>经纬度:</strong> ${escapeHtml(formatMapNumber(lon, 6))}, ${escapeHtml(formatMapNumber(lat, 6))}</div>
            <div><strong>LOS速率:</strong> ${escapeHtml(formatMapNumber(rate, 2))} mm/yr</div>
            ${nearestNote}
            <div style="margin-top:8px">${buildSbasSparklineSvg(point?.displacements || options.displacements || [])}</div>
        </div>
    `;
};

const buildSbasOverviewPopupHtml = (product) => {
    const title = escapeHtml(product?.display_name || product?.stack_key || product?.run_key || `SBAS #${product?.id ?? '-'}`);
    const dateStart = escapeHtml(String(product?.date_start || '-').slice(0, 10));
    const dateEnd = escapeHtml(String(product?.date_end || '-').slice(0, 10));
    const stackSize = escapeHtml(product?.stack_size ?? product?.stack_dates?.length ?? '-');
    const status = escapeHtml(product?.status || '-');
    const health = escapeHtml(product?.health_status || '-');
    const runKey = escapeHtml(product?.run_key || '-');
    const stackKey = escapeHtml(product?.stack_key || '-');
    const region = product?.admin_region?.display_name || product?.admin_region?.name || product?.admin_region?.tree_id || '-';
    return `
        <div class="sbas-popup" style="min-width:260px">
            <div style="font-weight:850;margin-bottom:7px">${title}</div>
            <div><strong>时间:</strong> ${dateStart} → ${dateEnd}</div>
            <div><strong>栈期数:</strong> ${stackSize}</div>
            <div><strong>状态:</strong> ${status} / ${health}</div>
            <div><strong>区域:</strong> ${escapeHtml(region)}</div>
            <div><strong>stack:</strong> <span class="mono">${stackKey}</span></div>
            <div><strong>run:</strong> <span class="mono">${runKey}</span></div>
        </div>
    `;
};

function App() {
    const { language, setLanguage } = useI18n();

    // --- Zustand stores ---
    const {
        currentUser, setCurrentUser, authChecked, setAuthChecked,
        licenseStatus, setLicenseStatus, licenseLoading, setLicenseLoading,
        licenseUploadStatus, setLicenseUploadStatus, licenseFileName, setLicenseFileName,
        healthStatus, setHealthStatus, healthLoading, setHealthLoading, healthError, setHealthError,
    } = useAuthStore(useShallow((state) => ({
        currentUser: state.currentUser,
        setCurrentUser: state.setCurrentUser,
        authChecked: state.authChecked,
        setAuthChecked: state.setAuthChecked,
        licenseStatus: state.licenseStatus,
        setLicenseStatus: state.setLicenseStatus,
        licenseLoading: state.licenseLoading,
        setLicenseLoading: state.setLicenseLoading,
        licenseUploadStatus: state.licenseUploadStatus,
        setLicenseUploadStatus: state.setLicenseUploadStatus,
        licenseFileName: state.licenseFileName,
        setLicenseFileName: state.setLicenseFileName,
        healthStatus: state.healthStatus,
        setHealthStatus: state.setHealthStatus,
        healthLoading: state.healthLoading,
        setHealthLoading: state.setHealthLoading,
        healthError: state.healthError,
        setHealthError: state.setHealthError,
    })));
    const {
        activeTasks, setActiveTasks, runtimeSummary, setRuntimeSummary,
        isCheckingTasks, setIsCheckingTasks,
        pendingTaskIds, setPendingTaskIds,
    } = useTaskStore(useShallow((state) => ({
        activeTasks: state.activeTasks,
        setActiveTasks: state.setActiveTasks,
        runtimeSummary: state.runtimeSummary,
        setRuntimeSummary: state.setRuntimeSummary,
        isCheckingTasks: state.isCheckingTasks,
        setIsCheckingTasks: state.setIsCheckingTasks,
        pendingTaskIds: state.pendingTaskIds,
        setPendingTaskIds: state.setPendingTaskIds,
    })));
    const {
        leftPanelTab, setLeftPanelTab, leftPanelWidth,
        setShowDataInfo, setSelectedDataInfo, showDates,
        baseLayerKey, setBaseLayerKey, isLoading, setIsLoading, addLog,
    } = useUiStore(useShallow((state) => ({
        leftPanelTab: state.leftPanelTab,
        setLeftPanelTab: state.setLeftPanelTab,
        leftPanelWidth: state.leftPanelWidth,
        setShowDataInfo: state.setShowDataInfo,
        setSelectedDataInfo: state.setSelectedDataInfo,
        showDates: state.showDates,
        baseLayerKey: state.baseLayerKey,
        setBaseLayerKey: state.setBaseLayerKey,
        isLoading: state.isLoading,
        setIsLoading: state.setIsLoading,
        addLog: state.addLog,
    })));
    const {
        allData, setAllData, radarPagination, setRadarPagination,
        radarPageInput, setRadarPageInput, radarPageInputTouched, setRadarPageInputTouched,
        hasRadarSearched, setHasRadarSearched, radarImagingDates, setRadarImagingDates,
        radarSearchDraft, setRadarSearchDraft, radarSearchApplied, setRadarSearchApplied,
        setRadarSearchOptions, setRadarSearchOptionsLoading,
        radarSearchAoiMode, setRadarSearchAoiMode, radarSearchAppliedAoiMode, setRadarSearchAppliedAoiMode,
        radarSearchFiles, setRadarSearchFiles,
        radarSearchRegionOptions, setRadarSearchRegionOptions,
        radarSearchRegionSelection, setRadarSearchRegionSelection,
        radarSearchAppliedRegionTreeId, setRadarSearchAppliedRegionTreeId,
        setRadarSearchRegionLoading, setRadarSearchRegionError,
        radarSearchAoiToken, setRadarSearchAoiToken,
        selectedSatelliteGroup, setSelectedSatelliteGroup,
        rebuildingPreviewIds, setRebuildingPreviewIds,
    } = useRadarStore(useShallow((state) => ({
        allData: state.allData,
        setAllData: state.setAllData,
        radarPagination: state.radarPagination,
        setRadarPagination: state.setRadarPagination,
        radarPageInput: state.radarPageInput,
        setRadarPageInput: state.setRadarPageInput,
        radarPageInputTouched: state.radarPageInputTouched,
        setRadarPageInputTouched: state.setRadarPageInputTouched,
        hasRadarSearched: state.hasRadarSearched,
        setHasRadarSearched: state.setHasRadarSearched,
        radarImagingDates: state.radarImagingDates,
        setRadarImagingDates: state.setRadarImagingDates,
        radarSearchDraft: state.radarSearchDraft,
        setRadarSearchDraft: state.setRadarSearchDraft,
        radarSearchApplied: state.radarSearchApplied,
        setRadarSearchApplied: state.setRadarSearchApplied,
        setRadarSearchOptions: state.setRadarSearchOptions,
        setRadarSearchOptionsLoading: state.setRadarSearchOptionsLoading,
        radarSearchAoiMode: state.radarSearchAoiMode,
        setRadarSearchAoiMode: state.setRadarSearchAoiMode,
        radarSearchAppliedAoiMode: state.radarSearchAppliedAoiMode,
        setRadarSearchAppliedAoiMode: state.setRadarSearchAppliedAoiMode,
        radarSearchFiles: state.radarSearchFiles,
        setRadarSearchFiles: state.setRadarSearchFiles,
        radarSearchRegionOptions: state.radarSearchRegionOptions,
        setRadarSearchRegionOptions: state.setRadarSearchRegionOptions,
        radarSearchRegionSelection: state.radarSearchRegionSelection,
        setRadarSearchRegionSelection: state.setRadarSearchRegionSelection,
        radarSearchAppliedRegionTreeId: state.radarSearchAppliedRegionTreeId,
        setRadarSearchAppliedRegionTreeId: state.setRadarSearchAppliedRegionTreeId,
        setRadarSearchRegionLoading: state.setRadarSearchRegionLoading,
        setRadarSearchRegionError: state.setRadarSearchRegionError,
        radarSearchAoiToken: state.radarSearchAoiToken,
        setRadarSearchAoiToken: state.setRadarSearchAoiToken,
        selectedSatelliteGroup: state.selectedSatelliteGroup,
        setSelectedSatelliteGroup: state.setSelectedSatelliteGroup,
        rebuildingPreviewIds: state.rebuildingPreviewIds,
        setRebuildingPreviewIds: state.setRebuildingPreviewIds,
    })));
    const {
        dinsarResults, setDinsarResults, dinsarPagination,
        dinsarPageInput, setDinsarPageInput, dinsarPageInputTouched, setDinsarPageInputTouched,
        aiStatus, scoreFilter, setScoreFilter, engineFilter, traceSearch, strategyFilter,
    } = useDinsarStore(useShallow((state) => ({
        dinsarResults: state.dinsarResults,
        setDinsarResults: state.setDinsarResults,
        dinsarPagination: state.dinsarPagination,
        dinsarPageInput: state.dinsarPageInput,
        setDinsarPageInput: state.setDinsarPageInput,
        dinsarPageInputTouched: state.dinsarPageInputTouched,
        setDinsarPageInputTouched: state.setDinsarPageInputTouched,
        aiStatus: state.aiStatus,
        scoreFilter: state.scoreFilter,
        setScoreFilter: state.setScoreFilter,
        engineFilter: state.engineFilter,
        traceSearch: state.traceSearch,
        strategyFilter: state.strategyFilter,
    })));
    const {
        batchTab, setDinsarBatches, setPsBatches,
        selectedBatchId, setBatchItems,
        setBatchLoading, setBatchError,
    } = useBatchStore(useShallow((state) => ({
        batchTab: state.batchTab,
        setDinsarBatches: state.setDinsarBatches,
        setPsBatches: state.setPsBatches,
        selectedBatchId: state.selectedBatchId,
        setBatchItems: state.setBatchItems,
        setBatchLoading: state.setBatchLoading,
        setBatchError: state.setBatchError,
    })));
    const {
        foundPairs, setFoundPairs,
        setShowPairingModal,
        pairingAoiMode, setPairingAoiMode,
        pairingRegionOptions, setPairingRegionOptions,
        setPairingRegionSelection,
        setPairingRegionLoading, setPairingRegionError,
        setShowPsModal,
        psAoiMode, setPsAoiMode,
        psRegionOptions, setPsRegionOptions,
        setPsRegionSelection,
        setPsRegionLoading, setPsRegionError,
    } = usePairingStore(useShallow((state) => ({
        foundPairs: state.foundPairs,
        setFoundPairs: state.setFoundPairs,
        setShowPairingModal: state.setShowPairingModal,
        pairingAoiMode: state.pairingAoiMode,
        setPairingAoiMode: state.setPairingAoiMode,
        pairingRegionOptions: state.pairingRegionOptions,
        setPairingRegionOptions: state.setPairingRegionOptions,
        setPairingRegionSelection: state.setPairingRegionSelection,
        setPairingRegionLoading: state.setPairingRegionLoading,
        setPairingRegionError: state.setPairingRegionError,
        setShowPsModal: state.setShowPsModal,
        psAoiMode: state.psAoiMode,
        setPsAoiMode: state.setPsAoiMode,
        psRegionOptions: state.psRegionOptions,
        setPsRegionOptions: state.setPsRegionOptions,
        setPsRegionSelection: state.setPsRegionSelection,
        setPsRegionLoading: state.setPsRegionLoading,
        setPsRegionError: state.setPsRegionError,
    })));
    const {
        hazardPoints, showHazardPoints, setShowHazardPoints,
        focusedHazardPoint, setFocusedHazardPoint,
    } = useHazardStore(useShallow((state) => ({
        hazardPoints: state.hazardPoints,
        showHazardPoints: state.showHazardPoints,
        setShowHazardPoints: state.setShowHazardPoints,
        focusedHazardPoint: state.focusedHazardPoint,
        setFocusedHazardPoint: state.setFocusedHazardPoint,
    })));
    const {
        aoiLayer, setAoiLayer, showMapRegionLocator, setShowMapRegionLocator,
        mapRegionOptions, setMapRegionOptions, mapRegionSelection, setMapRegionSelection,
        mapRegionLoading, setMapRegionLoading, mapRegionLocating, setMapRegionLocating,
        mapRegionError, setMapRegionError, mapRegionLocatedName, setMapRegionLocatedName,
    } = useMapStore(useShallow((state) => ({
        aoiLayer: state.aoiLayer,
        setAoiLayer: state.setAoiLayer,
        showMapRegionLocator: state.showMapRegionLocator,
        setShowMapRegionLocator: state.setShowMapRegionLocator,
        mapRegionOptions: state.mapRegionOptions,
        setMapRegionOptions: state.setMapRegionOptions,
        mapRegionSelection: state.mapRegionSelection,
        setMapRegionSelection: state.setMapRegionSelection,
        mapRegionLoading: state.mapRegionLoading,
        setMapRegionLoading: state.setMapRegionLoading,
        mapRegionLocating: state.mapRegionLocating,
        setMapRegionLocating: state.setMapRegionLocating,
        mapRegionError: state.mapRegionError,
        setMapRegionError: state.setMapRegionError,
        mapRegionLocatedName: state.mapRegionLocatedName,
        setMapRegionLocatedName: state.setMapRegionLocatedName,
    })));

    const mapRef = useRef(null);
    const tileLayerRef = useRef(null);
    const activeLayersRef = useRef({});
    const radarPreviewLayersRef = useRef({});
    const pairLayersRef = useRef({});
    const psStackPreviewLayerRef = useRef(null);
    const psStackPreviewStateRef = useRef({ previousVisibilityById: new Map() });
    const hazardLayersGroupRef = useRef(null);
    const aoeLayerRef = useRef(null);
    const waterSceneLayersRef = useRef({});
    const floodEventLayersRef = useRef({});  // { eventId: { pre, post, classified } }
    const floodPairPreviewLayerRef = useRef(null);
    const floodVectorLayersRef = useRef({});
    const mapRegionLayerRef = useRef(null);
    const prevLicenseOkRef = useRef(false);
    const initializeAppDataRef = useRef(null);
    const addLogRef = useRef(addLog);
    const handleTaskCompletionRef = useRef(null);
    const updateLayerTooltipRef = useRef(null);
    const radarSearchRequestSeqRef = useRef(0);
    const licenseFileRef = useRef(null);
    const foundPairsRef = useRef(foundPairs);
    const hazardLayersRef = useRef({});
    const dinsarResultLayersRef = useRef({});
    const sbasAnalysisLayersRef = useRef({});
    const allDataRef = useRef(allData);
    const dinsarResultsRef = useRef(dinsarResults);
    const mapBatchRef = useRef({ frameId: null, token: 0 });
    const isAdmin = currentUser?.role === 'admin';
    const isReadOnlyUser = !!currentUser && !isAdmin;
    const isStandaloneLeftPage = FULL_WIDTH_LEFT_TABS.has(leftPanelTab);

    useEffect(() => {
        if (isStandaloneLeftPage || !mapRef.current) {
            return undefined;
        }

        const invalidateMap = () => {
            mapRef.current?.invalidateSize(false);
        };
        const frameId = requestAnimationFrame(invalidateMap);
        const timeoutId = window.setTimeout(invalidateMap, 120);

        return () => {
            cancelAnimationFrame(frameId);
            window.clearTimeout(timeoutId);
        };
    }, [isStandaloneLeftPage, leftPanelWidth]);

    const getVisibleLayerRefs = useCallback(() => ({
        activeLayersRef: activeLayersRef.current,
        hazardLayersGroupRef: hazardLayersGroupRef.current,
        dinsarResultLayersRef: dinsarResultLayersRef.current,
        sbasAnalysisLayersRef: sbasAnalysisLayersRef.current,
        waterSceneLayersRef: waterSceneLayersRef.current,
        radarPreviewLayersRef: radarPreviewLayersRef.current,
        pairLayersRef: pairLayersRef.current,
        aoeLayerRef: aoeLayerRef.current,
        floodEventLayersRef: floodEventLayersRef.current,
        floodVectorLayersRef: floodVectorLayersRef.current,
    }), []);

    const mapExport = useMapExport({ mapRef, getVisibleLayerRefs, addLog, language });

    const cancelMapBatch = useCallback(() => {
        if (mapBatchRef.current.frameId !== null) {
            cancelAnimationFrame(mapBatchRef.current.frameId);
            mapBatchRef.current.frameId = null;
        }
        mapBatchRef.current.token += 1;
    }, []);

    const runMapBatch = useCallback((items, processItem, onComplete) => {
        cancelMapBatch();

        if (!Array.isArray(items) || items.length === 0) {
            onComplete?.();
            return;
        }

        const token = mapBatchRef.current.token;
        let cursor = 0;

        const flushFrame = () => {
            if (token !== mapBatchRef.current.token || !mapRef.current) {
                return;
            }

            const frameStart = typeof performance !== 'undefined' ? performance.now() : Date.now();
            while (cursor < items.length) {
                processItem(items[cursor], cursor);
                cursor += 1;

                const now = typeof performance !== 'undefined' ? performance.now() : Date.now();
                if ((now - frameStart) >= 8) {
                    break;
                }
            }

            if (cursor < items.length) {
                mapBatchRef.current.frameId = requestAnimationFrame(flushFrame);
                return;
            }

            mapBatchRef.current.frameId = null;
            onComplete?.();
        };

        mapBatchRef.current.frameId = requestAnimationFrame(flushFrame);
    }, [cancelMapBatch]);

    const ensureCanOperate = useCallback(() => {
        if (!isAdmin) {
            addLog('warn', '当前账号为只读用户，无法执行写操作。');
            return false;
        }
        return true;
    }, [addLog, isAdmin]);

    const clearRadarMapLayers = () => {
        cancelMapBatch();
        if (aoeLayerRef.current) {
            aoeLayerRef.current.remove();
            aoeLayerRef.current = null;
        }
        if (psStackPreviewLayerRef.current) {
            psStackPreviewLayerRef.current.remove();
            psStackPreviewLayerRef.current = null;
        }
        psStackPreviewStateRef.current = { previousVisibilityById: new Map() };
        setAoiLayer(null);

        Object.values(activeLayersRef.current).forEach(layer => layer.remove());
        activeLayersRef.current = {};
        Object.values(radarPreviewLayersRef.current).forEach(layer => layer.remove());
        radarPreviewLayersRef.current = {};
    };

    const clearSbasAnalysisLayers = useCallback(() => {
        Object.values(sbasAnalysisLayersRef.current).forEach((entry) => {
            const layer = entry?.layer || entry;
            if (layer?.remove) {
                layer.remove();
            }
        });
        sbasAnalysisLayersRef.current = {};
    }, []);

    const clearRadarSearchResults = (options = {}) => {
        const nextLimit = Math.max(
            1,
            Math.min(Number(options.limit ?? radarPagination.limit ?? DEFAULT_LIST_PAGE_SIZE) || DEFAULT_LIST_PAGE_SIZE, 2000)
        );
        clearRadarMapLayers();
        allDataRef.current = [];
        setAllData([]);
        setRadarPagination({
            total: 0,
            limit: nextLimit,
            offset: 0,
            hasMore: false,
        });
    };

    const {
        fetchRadarImagingDates,
        fetchRadarSearchOptions,
        fetchAllData,
        applyRadarSearch,
        resetRadarSearch,
        searchAllRadarData,
        refreshCurrentRadarSearch,
        changeSatelliteGroup,
    } = useRadarSearch({
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
    });

    const {
        refreshBatchList,
        fetchBatchItems,
    } = useBatchOperations({
        addLog,
        ensureCanOperate,
        batchTab,
        selectedBatchId,
        setDinsarBatches,
        setPsBatches,
        setBatchItems,
        setBatchLoading,
        setBatchError,
    });

    useEffect(() => {
        addLogRef.current = addLog;
    }, [addLog]);

    useEffect(() => {
        allDataRef.current = allData;
    }, [allData]);

    useEffect(() => {
        dinsarResultsRef.current = dinsarResults;
    }, [dinsarResults]);

    useEffect(() => {
        foundPairsRef.current = foundPairs;
    }, [foundPairs]);

    useEffect(() => () => {
        cancelMapBatch();
    }, [cancelMapBatch]);

    const applyPreviewStatus = useCallback((itemId, payload) => {
        if (!payload) return;
        setAllData(prev => prev.map(row => (
            row.id === itemId
                ? {
                    ...row,
                    previewStatus: normalizePreviewStatus(payload.status),
                    previewFallbackInUse: !!payload.fallback_in_use,
                    previewHasGeoCache: !!payload.has_geo_cache,
                    previewHasRawCache: !!payload.has_raw_cache,
                    previewSourceFound: !!payload.source_found,
                    previewMessage: payload.message || '',
                    previewError: payload.error || '',
                    previewCacheKey: payload.cache_updated_at || row.previewCacheKey || `${Date.now()}-${itemId}`,
                }
                : row
        )));
    }, [setAllData]);

    const {
        fetchRegionGeometry,
        handlePairingAoiModeChange,
        handlePairingProvinceChange,
        handlePairingCityChange,
        toggleMapRegionLocator,
        handleMapRegionProvinceChange,
        handleMapRegionCityChange,
        handleRadarSearchAoiModeChange,
        handleRadarSearchProvinceChange,
        handleRadarSearchCityChange,
        updateRadarSearchDraft,
        locateSelectedRegionOnMap,
        clearMapRegionHighlight,
        openPairingModal,
    } = useRegionAoiHandlers({
        setPairingRegionLoading,
        setPairingRegionError,
        setPairingRegionOptions,
        setPsRegionLoading,
        setPsRegionError,
        setPsRegionOptions,
        setPairingAoiMode,
        pairingRegionOptions,
        setPsAoiMode,
        psRegionOptions,
        setPairingRegionSelection,
        setPsRegionSelection,
        setMapRegionLoading,
        setMapRegionError,
        setMapRegionOptions,
        showMapRegionLocator,
        setShowMapRegionLocator,
        mapRegionOptions,
        setMapRegionSelection,
        setMapRegionLocatedName,
        setMapRegionLocating,
        setRadarSearchRegionLoading,
        setRadarSearchRegionError,
        setRadarSearchRegionOptions,
        setRadarSearchAoiMode,
        setRadarSearchFiles,
        setRadarSearchRegionSelection,
        radarSearchRegionOptions,
        setRadarSearchDraft,
        mapRef,
        mapRegionLayerRef,
        mapRegionSelection,
        addLog,
        pairingAoiMode,
        setShowPairingModal,
        psAoiMode,
        setShowPsModal,
    });

    const {
        handleLoginSuccess,
        handleLogout,
        fetchLicenseStatus,
        fetchHealthStatus,
        handleLicenseUpload,
    } = useAppAuthLifecycle({
        ensureCanOperate,
        clearRadarSearchResults,
        radarSearchRequestSeqRef,
        prevLicenseOkRef,
        aoeLayerRef,
        activeLayersRef,
        radarPreviewLayersRef,
        setHasRadarSearched,
        setCurrentUser,
        setAuthChecked,
        setPendingTaskIds,
        setLicenseLoading,
        setLicenseStatus,
        setHealthLoading,
        setHealthError,
        setHealthStatus,
        setLicenseFileName,
        setLicenseUploadStatus,
        setAoiLayer,
        setAllData,
        setRadarPagination,
    });

    // --- Refactored Initialization ---
    useEffect(() => {
        if (!currentUser?.id) {
            return;
        }
        let isDisposed = false;

        addLogRef.current('info', '应用初始化...');
        mapRef.current = L.map('map', { zoomControl: false });

        // 底图图层将在 baseLayerKey effect 中加载

        // 设置地图初始视图以匹配切片范围
        if (TILE_LAYER_BOUNDS) {
            mapRef.current.fitBounds(TILE_LAYER_BOUNDS);
        }

        const loadNationalBoundary = async () => {
            const fetchBoundaryGeoJson = async url => {
                const response = await fetch(url);
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                return response.json();
            };

            try {
                let geojsonData;
                let loadedFromFallback = false;

                try {
                    geojsonData = await fetchBoundaryGeoJson(NATIONAL_BOUNDARY_GEOJSON_URL);
                } catch (primaryError) {
                    console.warn('全国行政区边界远程加载失败，尝试本地静态文件兜底。', primaryError);
                    geojsonData = await fetchBoundaryGeoJson(NATIONAL_BOUNDARY_STATIC_URL);
                    loadedFromFallback = true;
                }

                if (isDisposed || !mapRef.current) return;
                L.geoJSON(geojsonData, {
                    style: { color: "#64748b", weight: 1, opacity: 0.35, fillOpacity: 0 },
                    interactive: false,
                }).addTo(mapRef.current);
                addLogRef.current(
                    'info',
                    loadedFromFallback ? '全国行政区边界已通过本地静态文件加载。' : '成功加载全国行政区边界。'
                );
            } catch (error) {
                addLogRef.current('warn', '未能加载全国行政区边界，继续使用切片底图。');
                console.error('加载全国行政区边界失败:', error);
                if (!TILE_LAYER_BOUNDS && mapRef.current) {
                    mapRef.current.setView([35.8617, 104.1954], 5);
                }
            }
        };
        void loadNationalBoundary();

        L.control.zoom({ position: 'topright' }).addTo(mapRef.current);
        
        hazardLayersGroupRef.current = L.layerGroup().addTo(mapRef.current);
        
        addLogRef.current('info', '地图初始化完成。');

        return () => {
            isDisposed = true;
            if (mapRef.current) {
                if (mapRegionLayerRef.current) {
                    mapRegionLayerRef.current.remove();
                    mapRegionLayerRef.current = null;
                }
                mapRef.current.remove();
                mapRef.current = null;
            }
            tileLayerRef.current = null;
        };
    }, [currentUser?.id]);

    useEffect(() => {
        if (!mapRef.current) return;
        const config = getBaseLayerConfig(baseLayerKey);
        const isInitial = !tileLayerRef.current;

        if (tileLayerRef.current) {
            mapRef.current.removeLayer(tileLayerRef.current);
        }

        try {
            tileLayerRef.current = L.tileLayer(config.url, {
                ...TILE_LAYER_OPTIONS,
                attribution: config.attribution,
            }).addTo(mapRef.current);
            addLogRef.current('info', `${isInitial ? '加载' : '切换'}底图：${config.label}`);
        } catch (e) {
            addLogRef.current('error', '加载切片底图失败，请检查配置。');
            console.error('加载切片底图失败:', e);
        }
    }, [baseLayerKey, currentUser?.id]);

    useEffect(() => {
        if (licenseLoading || !authChecked) return;
        const isOk = !!licenseStatus?.ok && !!currentUser;
        const prevOk = prevLicenseOkRef.current;
        if (isOk && !prevOk) {
            initializeAppDataRef.current?.();
        }
        prevLicenseOkRef.current = isOk;
    }, [licenseLoading, licenseStatus?.ok, authChecked, currentUser]);

    const {
        fetchDinsarResults,
        fetchHazardPoints,
        handleTaskCompletion,
        handleLabelResult,
        handleTrainAi,
        handlePredictAll,
        handleAnalyzeResult,
        handleTaskStart,
        initializeAppData,
    } = useDinsarOperations({
        onCleanupDinsarLayers: () => {
            Object.values(dinsarResultLayersRef.current).forEach(layerData => {
                if (layerData?.layer && mapRef.current?.hasLayer(layerData.layer)) {
                    layerData.layer.remove();
                }
            });
            dinsarResultLayersRef.current = {};
        },
        fetchRadarImagingDates,
        fetchRadarSearchOptions,
        fetchAllData,
        radarSearchRequestSeqRef,
    });
    initializeAppDataRef.current = initializeAppData;

    const {
        radarCurrentPage,
        radarTotalPages,
        dinsarCurrentPage,
        dinsarTotalPages,
        radarPageInputValidationError,
        dinsarPageInputValidationError,
        showRadarPageInputError,
        showDinsarPageInputError,
        handleRadarPageSizeChange,
        handleDinsarPageSizeChange,
        goToRadarPage,
        goToDinsarPage,
        changeRadarPage,
        changeDinsarPage,
    } = usePaginationControls({
        language,
        hasRadarSearched,
        addLog,
        radarPagination,
        dinsarPagination,
        radarPageInput,
        dinsarPageInput,
        radarPageInputTouched,
        dinsarPageInputTouched,
        setRadarPageInput,
        setRadarPageInputTouched,
        setDinsarPageInput,
        setDinsarPageInputTouched,
        setIsLoading,
        fetchAllData,
        fetchDinsarResults,
        radarSearchRequestSeqRef,
    });
    handleTaskCompletionRef.current = handleTaskCompletion;

    const {
        cancelTaskPwd,
        setCancelTaskPwd,
        showCancelTask,
        setShowCancelTask,
        handleCancelActiveTasks,
    } = useGlobalTaskControl({
        currentUser,
        licenseOk: !!licenseStatus?.ok,
        activeTasks,
        setActiveTasks,
        setRuntimeSummary,
        pendingTaskIds,
        setPendingTaskIds,
        setIsCheckingTasks,
        handleTaskCompletionRef,
    });

    const fetchRadarPreviewStatus = useCallback(async (itemId, options = {}) => {
        const { silent = false } = options;
        try {
            const response = await apiClient.get(`/radar-data/${itemId}/preview-status`);
            applyPreviewStatus(itemId, response.data);
            return response.data;
        } catch {
            if (!silent) {
                addLog('warn', `获取影像预览状态失败 (ID: ${itemId})`);
            }
            return null;
        }
    }, [addLog, applyPreviewStatus]);

    const rebuildRadarPreviewCache = useCallback(async (itemId, event) => {
        if (event) event.stopPropagation();
        if (!ensureCanOperate()) return;
        if (rebuildingPreviewIds[itemId]) return;

        addLog('info', `正在生成影像 ${itemId} 的预览缓存...`);

        setRebuildingPreviewIds(prev => ({ ...prev, [itemId]: true }));
        try {
            const response = await apiClient.post(`/radar-data/${itemId}/rebuild-preview-cache`);
            const payload = response.data;
            applyPreviewStatus(itemId, payload);
            if (payload?.has_geo_cache) {
                addLog('success', `影像 ${itemId} 纠正缓存重建完成。`);
            } else if (payload?.has_raw_cache) {
                addLog('warn', `影像 ${itemId} 已回退原图缓存，请检查纠正参数。`);
            } else {
                addLog('error', `影像 ${itemId} 预览缓存重建失败。`);
            }
        } catch {
            addLog('error', `影像 ${itemId} 预览缓存重建失败。`);
        } finally {
            setRebuildingPreviewIds(prev => {
                const next = { ...prev };
                delete next[itemId];
                return next;
            });
        }
    }, [addLog, applyPreviewStatus, ensureCanOperate, handleTaskStart, rebuildingPreviewIds, setRebuildingPreviewIds]);

    useEffect(() => {
        if (!mapRef.current || !hazardLayersGroupRef.current) return;

        hazardLayersGroupRef.current.clearLayers();
        hazardLayersRef.current = {};

        if (showHazardPoints) {
            hazardPoints.forEach(point => {
                const marker = L.circleMarker([point.latitude, point.longitude], {
                    radius: 6,
                    fillColor: "#e53e3e",
                    color: "#fff",
                    weight: 2,
                    opacity: 1,
                    fillOpacity: 0.8
                });

                const hazardName = escapeHtml(point.hazard_name);
                const hazardId = escapeHtml(point.tybh);
                const hazardType = escapeHtml(point.hazard_type);
                const hazardCity = escapeHtml(point.city);
                const hazardCounty = escapeHtml(point.county);
                const hazardTownship = escapeHtml(point.township);
                const hazardLongitude = formatCoordinate(point.longitude);
                const hazardLatitude = formatCoordinate(point.latitude);

                marker.bindPopup(`
                    <div class="hazard-popup">
                        <h4 style="margin:0 0 8px 0; color:#e53e3e;">${hazardName}</h4>
                        <table style="font-size:12px; border-collapse:collapse; width:100%;">
                            <tr><td style="color:#718096; padding:2px 0;">编号:</td><td>${hazardId}</td></tr>
                            <tr><td style="color:#718096; padding:2px 0;">类型:</td><td>${hazardType}</td></tr>
                            <tr><td style="color:#718096; padding:2px 0;">位置:</td><td>${hazardCity} ${hazardCounty} ${hazardTownship}</td></tr>
                            <tr><td style="color:#718096; padding:2px 0;">经纬度:</td><td>${hazardLongitude}, ${hazardLatitude}</td></tr>
                        </table>
                    </div>
                `);

                hazardLayersGroupRef.current.addLayer(marker);
                hazardLayersRef.current[point.tybh] = marker;
            });
        }
    }, [hazardPoints, showHazardPoints]);

    const flyToHazardPoint = (point) => {
        setFocusedHazardPoint(point);
        // 切换到 D-InSAR 结果标签页，方便用户查看关联结果
        setLeftPanelTab('dinsar_results');
        addLog('info', `定位到灾害点: ${point.hazard_name} (${point.tybh})，已自动筛选覆盖该点的 D-InSAR 结果。`);

        if (mapRef.current) {
            mapRef.current.flyTo([point.latitude, point.longitude], 14, {
                duration: 1.5
            });
            setTimeout(() => {
                if (hazardLayersRef.current[point.tybh]) {
                    hazardLayersRef.current[point.tybh].openPopup();
                }
            }, 1600);
        }
    };



    useEffect(() => {
        if (aoiLayer) {
            if (aoeLayerRef.current) {
                aoeLayerRef.current.remove();
            }
            const aoiGeoJsonLayer = L.geoJSON(aoiLayer, {
                style: {
                    color: '#f6e05e',
                    weight: 2,
                    opacity: 1,
                    dashArray: '5, 5',
                    fill: false,
                }
            }).addTo(mapRef.current);
            aoeLayerRef.current = aoiGeoJsonLayer;
            mapRef.current.fitBounds(aoiGeoJsonLayer.getBounds(), { padding: [50, 50] });
        }
    }, [aoiLayer]);

    const restorePsStackPreviewVisibility = () => {
        const previousVisibilityById = psStackPreviewStateRef.current?.previousVisibilityById;
        if (!(previousVisibilityById instanceof Map) || previousVisibilityById.size === 0) {
            psStackPreviewStateRef.current = { previousVisibilityById: new Map() };
            return false;
        }

        let changed = false;
        const currentData = allDataRef.current;
        const restoredData = currentData.map(item => {
            if (!previousVisibilityById.has(item.id)) {
                return item;
            }
            const shouldBeVisible = previousVisibilityById.get(item.id);
            if (item.isVisible !== shouldBeVisible) {
                updateLayerVisibility(item, shouldBeVisible);
                changed = true;
                return { ...item, isVisible: shouldBeVisible };
            }
            return item;
        });

        if (changed) {
            allDataRef.current = restoredData;
            setAllData(restoredData);
        }
        psStackPreviewStateRef.current = { previousVisibilityById: new Map() };
        return changed;
    };

    const clearPsStackPreview = ({ silent = false } = {}) => {
        const hadLayer = !!psStackPreviewLayerRef.current;
        if (psStackPreviewLayerRef.current) {
            psStackPreviewLayerRef.current.remove();
            psStackPreviewLayerRef.current = null;
        }
        const restoredVisibility = restorePsStackPreviewVisibility();
        if (silent) {
            return;
        }
        if (!hadLayer && !restoredVisibility) {
            addLog('info', '当前没有打开的时序候选栈预览范围。');
            return;
        }
        addLog('info', '已关闭时序候选栈预览范围。');
    };

    const previewPsStack = (stack) => {
        cancelMapBatch();
        clearPsStackPreview({ silent: true });
        const stackIds = new Set(stack.map(img => img.id));
        const currentData = allDataRef.current;
        const previousVisibilityById = new Map(currentData.map(item => [item.id, item.isVisible]));
        const newAllData = currentData.map(item => {
            const shouldBeVisible = stackIds.has(item.id);
            if (item.isVisible !== shouldBeVisible) {
                updateLayerVisibility(item, shouldBeVisible);
                return { ...item, isVisible: shouldBeVisible };
            }
            return item;
        });
        allDataRef.current = newAllData;
        setAllData(newAllData);
        psStackPreviewStateRef.current = { previousVisibilityById };

        if (!mapRef.current) {
            clearPsStackPreview({ silent: true });
            addLog('warn', '地图尚未就绪，无法预览时序候选栈范围。');
            return;
        }

        const previewGroup = L.layerGroup();
        const allLatLngs = [];
        const palette = ['#f59e0b', '#06b6d4', '#a855f7', '#22c55e', '#ef4444', '#3b82f6'];
        let validSceneCount = 0;

        stack.forEach((scene, index) => {
            const polygon = scene.coverage_polygon;
            if (!Array.isArray(polygon) || polygon.length < 3) {
                return;
            }
            const latLngs = polygon
                .filter((point) => Array.isArray(point) && point.length >= 2)
                .map((point) => [Number(point[1]), Number(point[0])])
                .filter(([lat, lon]) => Number.isFinite(lat) && Number.isFinite(lon));
            if (latLngs.length < 3) {
                return;
            }

            validSceneCount += 1;
            allLatLngs.push(...latLngs);
            const color = palette[index % palette.length];
            const scenePolygon = L.polygon(latLngs, {
                color,
                weight: 3,
                opacity: 0.95,
                fillColor: color,
                fillOpacity: 0.08,
                dashArray: scene.stack_selection_mode === 'pairwise_sbas_network' ? '8, 5' : null,
            });
            scenePolygon.bindPopup(
                `<strong>时序候选场景</strong><br>` +
                `ID: ${escapeHtml(scene.id)}<br>` +
                `日期: ${escapeHtml(scene.imaging_date || '-')}<br>` +
                `卫星: ${escapeHtml(scene.satellite || '-')}<br>` +
                `模式: ${escapeHtml(scene.imaging_mode || '-')} / ${escapeHtml(scene.polarization || '-')}`
            );
            previewGroup.addLayer(scenePolygon);
        });

        if (validSceneCount === 0 || allLatLngs.length < 3) {
            clearPsStackPreview({ silent: true });
            addLog('warn', `时序候选栈包含 ${stack.length} 个场景，但没有可绘制的覆盖范围。`);
            return;
        }

        previewGroup.addTo(mapRef.current);
        psStackPreviewLayerRef.current = previewGroup;
        mapRef.current.fitBounds(L.latLngBounds(allLatLngs), { padding: [50, 50], maxZoom: 10 });
        addLog('info', `正在预览包含 ${stack.length} 个场景的时序InSAR候选栈，已绘制 ${validSceneCount} 个覆盖范围。`);
    };

    const updateLayerTooltip = useCallback((layer, result, show) => {
        if (show) {
            const dates = parseDatesFromName(result.name, (value) => formatYmd(value, language));
            if (dates) {
                const content = `${dates.master} → ${dates.slave}`;
                if (layer.getTooltip()) {
                    layer.setTooltipContent(content);
                    if (!layer.isTooltipOpen()) {
                        layer.openTooltip();
                    }
                } else {
                    layer.bindTooltip(content, {
                        permanent: true,
                        direction: 'center',
                        className: 'date-tooltip',
                        opacity: 0.9
                    }).openTooltip();
                }
            }
        } else {
            if (layer.getTooltip()) {
                layer.unbindTooltip();
            }
        }
    }, [language]);
    updateLayerTooltipRef.current = updateLayerTooltip;

    // 当 showDates 改变时，更新所有可见图层的 Tooltip
    useEffect(() => {
        Object.entries(dinsarResultLayersRef.current).forEach(([id, layerData]) => {
            if (layerData && layerData.layer && mapRef.current.hasLayer(layerData.layer)) {
                const result = dinsarResults.find(r => r.id === parseInt(id));
                if (result) {
                    updateLayerTooltipRef.current?.(layerData.layer, result, showDates);
                }
            }
        });
    }, [showDates, dinsarResults]);

    const buildDinsarResultPopupHtml = useCallback((result) => {
        const dates = parseDatesFromName(result.name, (value) => formatYmd(value, language));
        const aiScore = result.ai_score === null || result.ai_score === undefined
            ? '-'
            : `${(Number(result.ai_score) * 100).toFixed(0)}%`;
        const engine = getDinsarEngineMeta(result.engine_code);
        const strategy = escapeHtml(result.selection_strategy || '标准选择');
        const taskAlias = escapeHtml(result.task_alias || result.task_name || result.name || '-');
        const pairKey = escapeHtml(result.pair_key || '-');
        const pairUid = escapeHtml(result.pair_uid || '-');
        const runKey = escapeHtml(result.run_key || '-');
        const networkRunId = escapeHtml(result.network_run_id || '-');
        const networkEdgeId = escapeHtml(result.network_edge_id ?? '-');
        const policyVersion = escapeHtml(result.policy_version || '-');
        const dateText = dates
            ? `${escapeHtml(dates.master)} → ${escapeHtml(dates.slave)}`
            : '-';

        return `
            <div class="dinsar-popup">
                <div class="dinsar-popup-title">${escapeHtml(result.name || '-') }</div>
                <div><strong>任务:</strong> ${taskAlias}</div>
                <div><strong>日期:</strong> ${dateText}</div>
                <div><strong>AI:</strong> ${aiScore}</div>
                <div><strong>引擎:</strong> ${escapeHtml(engine.label)} <span class="mono">(${escapeHtml(engine.code)})</span></div>
                <div><strong>策略:</strong> ${strategy}</div>
                <div><strong>edge:</strong> ${networkEdgeId}</div>
                <div><strong>run:</strong> <span class="mono">${networkRunId}</span></div>
                <div><strong>run_key:</strong> <span class="mono">${runKey}</span></div>
                <div><strong>pair_key:</strong> <span class="mono">${pairKey}</span></div>
                <div><strong>pair_uid:</strong> <span class="mono">${pairUid}</span></div>
                <div><strong>policy:</strong> ${policyVersion}</div>
            </div>
        `;
    }, [language]);

    const updateDinsarLayer = useCallback((result, shouldBeVisible) => {
        const resultId = result.id;
        const layerData = dinsarResultLayersRef.current[resultId];
        const popupHtml = buildDinsarResultPopupHtml(result);

        if (shouldBeVisible) {
            let layer = layerData?.layer;
            if (layer) {
                // 如果图层已存在，只需将其添加回地图
                if (!mapRef.current.hasLayer(layer)) { // 检查是否已在地图上
                    layer.addTo(mapRef.current);
                }
                layer.bindPopup(popupHtml, { maxWidth: 360 });
            } else {
                // 创建新图层
                // 后端已统一接口，不再区分 thumb/full，直接请求即可
                const imageUrl = `${apiClient.defaults.baseURL}/dinsar-results/${resultId}/thumb`;
                addLog('info', `加载结果 ${result.name} 可视化图...`);

                const bounds = [[result.min_lat, result.min_lon], [result.max_lat, result.max_lon]];
                layer = L.imageOverlay(imageUrl, bounds, {
                    opacity: 0.7,
                    interactive: true,
                }).addTo(mapRef.current);

                layer.on('load', () => {
                    addLog('success', `结果 ${result.name} 的图像已加载。`);
                });
                layer.on('error', (e) => {
                    addLog('error', `无法加载结果 ${result.name} 的图像。`);
                    console.error(`加载图像失败 (ID: ${resultId}, URL: ${imageUrl})`, e);
                    layer.remove();
                    // 从ref中清除，以便下次可以重试
                    delete dinsarResultLayersRef.current[resultId];
                });
                layer.bindPopup(popupHtml, { maxWidth: 360 });
                
                // 在ref中存储图层对象
                dinsarResultLayersRef.current[resultId] = {
                    layer: layer
                };
            }
            // 确保 Tooltip 状态正确
            updateLayerTooltip(layer, result, showDates);
        } else {
            // 隐藏图层
            if (layerData && layerData.layer && mapRef.current.hasLayer(layerData.layer)) {
                layerData.layer.remove();
            }
        }
    }, [addLog, buildDinsarResultPopupHtml, showDates, updateLayerTooltip]);

    const flyToSbasProduct = useCallback((product) => {
        if (!mapRef.current || !product) return false;
        const bounds = getSbasProductBounds(product);
        if (!bounds) {
            addLog('warn', '当前 SBAS 产品没有可定位的地理范围。');
            return false;
        }
        mapRef.current.flyToBounds(L.latLngBounds(bounds), { padding: [45, 45], maxZoom: 12 });
        return true;
    }, [addLog]);

    const toggleSbasRateLayer = useCallback((detail, shouldBeVisible, opacity = 0.78) => {
        if (!mapRef.current || !detail) return false;
        const layerKey = `rate:${detail.id}`;
        const existing = sbasAnalysisLayersRef.current[layerKey]?.layer;
        if (!shouldBeVisible) {
            if (existing) existing.remove();
            delete sbasAnalysisLayersRef.current[layerKey];
            return true;
        }

        const asset = findSbasAsset(detail, ['primary_geocoded_preview', 'primary_rate_color_preview']);
        const bounds = getSbasProductBounds(detail);
        if (!asset || !bounds) {
            addLog('warn', 'SBAS 产品缺少 LOS 速率图或地理范围，无法叠加到地图。');
            return false;
        }
        if (existing) {
            if (!mapRef.current.hasLayer(existing)) existing.addTo(mapRef.current);
            existing.setOpacity(opacity);
            flyToSbasProduct(detail);
            return true;
        }

        const imageUrl = getSbasInsarProductAssetUrl(detail.id, asset.id, sbasAssetCacheKey(asset));
        const layer = L.imageOverlay(imageUrl, bounds, {
            opacity,
            interactive: true,
            crossOrigin: true,
        }).addTo(mapRef.current);
        layer.bindPopup(
            `<div class="sbas-popup"><div style="font-weight:800;margin-bottom:6px">${escapeHtml(detail.display_name || detail.run_key || 'Gamma SBAS')}</div>` +
            `<div><strong>图层:</strong> LOS 速率图</div>` +
            `<div><strong>色表:</strong> ${escapeHtml(detail.color_policy?.colormap || 'Gamma hls.cm')}</div>` +
            `<div><strong>范围:</strong> ${escapeHtml((detail.color_policy?.display_range_mm_per_year || [-80, 80]).join(' 到 '))} mm/yr</div></div>`,
            { maxWidth: 340 },
        );
        layer.on('load', () => addLog('success', `SBAS LOS 速率图已加载：${detail.display_name || detail.run_key || detail.id}`));
        layer.on('error', () => {
            addLog('error', 'SBAS LOS 速率图加载失败。');
            layer.remove();
            delete sbasAnalysisLayersRef.current[layerKey];
        });
        sbasAnalysisLayersRef.current[layerKey] = { layer, kind: 'rate', productId: detail.id };
        flyToSbasProduct(detail);
        return true;
    }, [addLog, flyToSbasProduct]);

    const updateSbasRateOpacity = useCallback((opacity) => {
        Object.values(sbasAnalysisLayersRef.current).forEach((entry) => {
            if (entry?.kind === 'rate' && entry.layer?.setOpacity) {
                entry.layer.setOpacity(opacity);
            }
        });
    }, []);

    const toggleSbasProductOverview = useCallback((products, shouldBeVisible) => {
        if (!mapRef.current) return false;
        const layerKey = 'overview';
        const existing = sbasAnalysisLayersRef.current[layerKey]?.layer;
        if (!shouldBeVisible) {
            if (existing) existing.remove();
            delete sbasAnalysisLayersRef.current[layerKey];
            return true;
        }
        if (existing) {
            if (!mapRef.current.hasLayer(existing)) existing.addTo(mapRef.current);
            const existingBounds = sbasAnalysisLayersRef.current[layerKey]?.bounds;
            if (existingBounds) {
                mapRef.current.flyToBounds(existingBounds, { padding: [55, 55], maxZoom: 10 });
            }
            return true;
        }

        const validProducts = (Array.isArray(products) ? products : [])
            .map((product) => ({ product, bounds: getSbasProductBounds(product) }))
            .filter(item => item.bounds);
        if (!validProducts.length) {
            addLog('warn', '当前没有可绘制范围的 SBAS 产品。');
            return false;
        }

        const group = L.layerGroup();
        let allBounds = null;
        validProducts.forEach(({ product, bounds }, index) => {
            const color = SBAS_OVERVIEW_COLORS[index % SBAS_OVERVIEW_COLORS.length] || '#1d4ed8';
            const rectangle = L.rectangle(bounds, {
                color,
                weight: 2,
                opacity: 0.95,
                fillColor: color,
                fillOpacity: 0.08,
                dashArray: index % 2 === 0 ? undefined : '6 4',
                interactive: true,
            });
            rectangle.bindPopup(buildSbasOverviewPopupHtml(product), { maxWidth: 360 });
            rectangle.bindTooltip(
                `${product.display_name || product.stack_key || product.run_key || product.id}<br>${String(product.date_start || '-').slice(0, 10)} → ${String(product.date_end || '-').slice(0, 10)}`,
                { sticky: true, direction: 'top', opacity: 0.92 },
            );
            rectangle.addTo(group);
            const nextBounds = L.latLngBounds(bounds);
            allBounds = allBounds ? allBounds.extend(nextBounds) : nextBounds;
        });
        group.addTo(mapRef.current);
        sbasAnalysisLayersRef.current[layerKey] = { layer: group, kind: 'overview', bounds: allBounds };
        if (allBounds) {
            mapRef.current.flyToBounds(allBounds, { padding: [55, 55], maxZoom: 10 });
        }
        addLog('info', `已显示 ${validProducts.length} 个 SBAS 产品范围和时间。`);
        return true;
    }, [addLog]);

    const toggleSbasMonitorPoints = useCallback((detail, shouldBeVisible) => {
        if (!mapRef.current || !detail) return false;
        const layerKey = `points:${detail.id}`;
        const existing = sbasAnalysisLayersRef.current[layerKey]?.layer;
        if (!shouldBeVisible) {
            if (existing) existing.remove();
            delete sbasAnalysisLayersRef.current[layerKey];
            return true;
        }
        if (existing) {
            if (!mapRef.current.hasLayer(existing)) existing.addTo(mapRef.current);
            flyToSbasProduct(detail);
            return true;
        }
        const points = (detail.monitor_points?.monitor_points || [])
            .filter(point => Number.isFinite(Number(point.lat)) && Number.isFinite(Number(point.lon)));
        if (!points.length) {
            addLog('warn', '当前 SBAS 监测点没有 WGS84 坐标，请重新注册资产后再显示。');
            return false;
        }
        const group = L.layerGroup();
        const latLngs = [];
        points.forEach((point) => {
            const lat = Number(point.lat);
            const lon = Number(point.lon);
            const rate = Number(point.deformation_rate_mm_per_year);
            const color = sbasRateColor(rate);
            const marker = L.circleMarker([lat, lon], {
                radius: 7,
                color: '#ffffff',
                weight: 2,
                fillColor: color,
                fillOpacity: 0.92,
                interactive: true,
            });
            marker.bindPopup(buildSbasPointPopupHtml(point), { maxWidth: 320 });
            marker.addTo(group);
            latLngs.push([lat, lon]);
        });
        group.addTo(mapRef.current);
        sbasAnalysisLayersRef.current[layerKey] = { layer: group, kind: 'points', productId: detail.id };
        if (latLngs.length) {
            mapRef.current.flyToBounds(L.latLngBounds(latLngs), { padding: [55, 55], maxZoom: 13 });
        }
        addLog('info', `已显示 ${points.length} 个 SBAS 监测点。`);
        return true;
    }, [addLog, flyToSbasProduct]);

    const showSbasQueryPoint = useCallback((result, detail) => {
        if (!mapRef.current || !result?.matched) return false;
        const matched = result.matched;
        const lat = Number(matched.lat);
        const lon = Number(matched.lon);
        if (!Number.isFinite(lat) || !Number.isFinite(lon)) return false;
        const layerKey = 'query';
        const existing = sbasAnalysisLayersRef.current[layerKey]?.layer;
        if (existing) existing.remove();
        const marker = L.circleMarker([lat, lon], {
            radius: 8,
            color: '#111827',
            weight: 2,
            fillColor: matched.used_nearest ? '#f59e0b' : '#22c55e',
            fillOpacity: 0.95,
            interactive: true,
        }).addTo(mapRef.current);
        const point = {
            point_id: matched.used_nearest ? 'query_nearest' : 'query_point',
            selection_label: matched.used_nearest ? '查询点最近有效像元' : '查询点',
            deformation_rate_mm_per_year: matched.los_rate_mm_per_year,
            displacements: result.displacements || [],
            matched,
            lon,
            lat,
        };
        marker.bindPopup(buildSbasPointPopupHtml(point), { maxWidth: 320 }).openPopup();
        sbasAnalysisLayersRef.current[layerKey] = { layer: marker, kind: 'query', productId: detail?.id };
        mapRef.current.flyTo([lat, lon], Math.max(mapRef.current.getZoom(), 12), { animate: true });
        return true;
    }, []);

    const sbasAnalysisPanel = {
        onToggleRateLayer: toggleSbasRateLayer,
        onRateOpacityChange: updateSbasRateOpacity,
        onToggleMonitorPoints: toggleSbasMonitorPoints,
        onToggleProductOverview: toggleSbasProductOverview,
        onFlyToProduct: flyToSbasProduct,
        onShowQueryPoint: showSbasQueryPoint,
        onClearLayers: clearSbasAnalysisLayers,
    };

    const toggleDinsarResultVisibility = useCallback((resultId) => {
        cancelMapBatch();
        const currentResults = dinsarResultsRef.current;
        const newResults = [...currentResults];
        const resultIndex = newResults.findIndex(r => r.id === resultId);
        if (resultIndex === -1) return;
    
        const result = newResults[resultIndex];
        const nextResult = { ...result, isVisible: !result.isVisible };
        newResults[resultIndex] = nextResult;
        dinsarResultsRef.current = newResults;
        setDinsarResults(newResults);
        updateDinsarLayer(nextResult, nextResult.isVisible);
    }, [cancelMapBatch, setDinsarResults, updateDinsarLayer]);

    const handleSetAllDinsarVisibility = useCallback((shouldBeVisible) => {
        const currentResults = dinsarResultsRef.current;
        const currentFilters = {
            scoreFilter,
            engineFilter,
            strategyFilter,
            traceSearch,
            focusedHazardPoint,
        };
        const matchedResults = filterDinsarResults(currentResults, currentFilters);
        const matchedIds = new Set(matchedResults.map((result) => result.id));
        const traceKeyword = String(traceSearch || '').trim();
        const filterNotes = [];

        if (scoreFilter > 0) {
            filterNotes.push(`AI>=${Math.round(scoreFilter * 100)}%`);
        }
        if (engineFilter !== DINSAR_ENGINE_ALL) {
            filterNotes.push(`引擎:${getDinsarEngineMeta(engineFilter).shortLabel}`);
        }
        if (focusedHazardPoint?.hazard_name) {
            filterNotes.push(`点位:${focusedHazardPoint.hazard_name}`);
        }
        if (strategyFilter !== DINSAR_STRATEGY_ALL) {
            filterNotes.push(`策略:${strategyFilter}`);
        }
        if (traceKeyword) {
            filterNotes.push(`trace:${traceKeyword}`);
        }

        if (matchedResults.length === 0) {
            addLog('warn', `当前筛选下没有可${shouldBeVisible ? '显示' : '隐藏'}的 D-InSAR 结果。`);
            return;
        }

        addLog(
            'info',
            `${shouldBeVisible ? '显示' : '隐藏'} ${matchedResults.length} 个 D-InSAR 结果${filterNotes.length > 0 ? `（${filterNotes.join('，')}）` : ''}...`
        );

        const changedResults = [];
        const nextResults = currentResults.map((result) => {
            const isMatched = matchedIds.has(result.id);
            const nextVisibleState = shouldBeVisible
                ? isMatched
                : (isMatched ? false : result.isVisible);

            if (result.isVisible === nextVisibleState) {
                return result;
            }

            const nextResult = { ...result, isVisible: nextVisibleState };
            changedResults.push(nextResult);
            return nextResult;
        });

        dinsarResultsRef.current = nextResults;
        setDinsarResults(nextResults);
        runMapBatch(changedResults, (result) => {
            updateDinsarLayer(result, result.isVisible);
        });
    }, [addLog, engineFilter, focusedHazardPoint, runMapBatch, scoreFilter, setDinsarResults, strategyFilter, traceSearch, updateDinsarLayer]);

    const handleScoreFilterChange = useCallback((e) => {
        cancelMapBatch();
        const newScore = parseFloat(e.target.value);
        setScoreFilter(newScore);

        setDinsarResults(prevResults => {
            const newResults = prevResults.map(result => {
                if (result.isVisible && result.ai_score !== null && result.ai_score < newScore) {
                    updateDinsarLayer(result, false);
                    return { ...result, isVisible: false };
                }
                return result;
            });
            return newResults;
        });
    }, [cancelMapBatch, setDinsarResults, setScoreFilter, updateDinsarLayer]);

    const handleShowDataInfo = useCallback((item, event) => {
        if (event) event.stopPropagation();
        setSelectedDataInfo(item);
        setShowDataInfo(true);
    }, [setSelectedDataInfo, setShowDataInfo]);

    const {
        findPairs,
        createDinsarBatch,
    } = usePairingLogic({
        fetchRegionGeometry,
        refreshBatchList,
        fetchBatchItems,
        onClearAoiLayer: () => {
            if (aoeLayerRef.current) {
                aoeLayerRef.current.remove();
                aoeLayerRef.current = null;
            }
        },
    });

    const flyTo = useCallback((item) => {
        if (mapRef.current && item.coverage_polygon) {
            mapRef.current.closePopup();
            const latLngs = item.coverage_polygon.map(p => [p[1], p[0]]);
            const bounds = L.latLngBounds(latLngs);
            mapRef.current.flyToBounds(bounds, { padding: [50, 50], maxZoom: 10 });

            if (item.isVisible && activeLayersRef.current[item.id]) {
                mapRef.current.once('moveend', () => {
                    activeLayersRef.current[item.id].openPopup();
                });
            }
        }
    }, []);

    const updateLayerVisibility = useCallback((item, shouldBeVisible) => {
        if (shouldBeVisible) {
            if (activeLayersRef.current[item.id]) return;

            const latLngs = item.coverage_polygon.map(p => [p[1], p[0]]);
            const polygon = L.polygon(latLngs, {
                color: item.has_orbit_data ? '#48bb78' : '#f56565',
                weight: 2,
            });
            const originalFilename = escapeHtml(item.file_path.split(/[\\/]/).pop());
            const displayName = escapeHtml(item.displayName);
            const imagingDate = escapeHtml(item.imaging_date);
            polygon.bindPopup(
                `<strong>原始名称:</strong><br>${originalFilename}<br><br>` +
                `<strong>列表名称:</strong> ${displayName}<br>` +
                `<strong>日期:</strong> ${imagingDate}`
            );
            activeLayersRef.current[item.id] = polygon;
            polygon.addTo(mapRef.current);
        } else {
            if (activeLayersRef.current[item.id]) {
                activeLayersRef.current[item.id].remove();
                delete activeLayersRef.current[item.id];
            }
        }
    }, []);

    const updateRadarPreviewVisibility = useCallback(async (item, shouldBeVisible) => {
        if (!item || !mapRef.current) return;
        const itemId = item.id;
        const layer = radarPreviewLayersRef.current[itemId];

        if (shouldBeVisible) {
            const refreshedStatus = await fetchRadarPreviewStatus(itemId, { silent: true });
            if (refreshedStatus) {
                item = {
                    ...item,
                    previewStatus: normalizePreviewStatus(refreshedStatus.status),
                    previewFallbackInUse: !!refreshedStatus.fallback_in_use,
                    previewHasGeoCache: !!refreshedStatus.has_geo_cache,
                    previewHasRawCache: !!refreshedStatus.has_raw_cache,
                    previewSourceFound: !!refreshedStatus.source_found,
                    previewMessage: refreshedStatus.message || '',
                    previewError: refreshedStatus.error || '',
                    previewCacheKey: refreshedStatus.cache_updated_at || item.previewCacheKey || `${Date.now()}-${itemId}`,
                };
            }

            if (layer) {
                if (!mapRef.current.hasLayer(layer)) {
                    layer.addTo(mapRef.current);
                }
                return;
            }

            const cacheTag = encodeURIComponent(item.previewCacheKey || `${Date.now()}-${itemId}`);
            const imageUrl = `${apiClient.defaults.baseURL}/radar-data/${itemId}/thumb?v=${cacheTag}`;
            const bounds = [[item.min_lat, item.min_lon], [item.max_lat, item.max_lon]];
            const previewLayer = L.imageOverlay(imageUrl, bounds, {
                opacity: 0.78,
                crossOrigin: true,
            });

            previewLayer.on('error', () => {
                addLog('warn', `未找到 ${item.displayName} 的源影像缓存（或缓存生成失败）。`);
                if (radarPreviewLayersRef.current[itemId]) {
                    radarPreviewLayersRef.current[itemId].remove();
                    delete radarPreviewLayersRef.current[itemId];
                }
                setAllData(prev => prev.map(row => (
                    row.id === itemId ? { ...row, isPreviewVisible: false } : row
                )));
                fetchRadarPreviewStatus(itemId, { silent: true });
            });

            radarPreviewLayersRef.current[itemId] = previewLayer;
            previewLayer.addTo(mapRef.current);
        } else if (layer) {
            layer.remove();
            delete radarPreviewLayersRef.current[itemId];
        }
    }, [addLog, fetchRadarPreviewStatus, setAllData]);

    const toggleLayerVisibility = useCallback((itemId) => {
        cancelMapBatch();
        const currentData = allDataRef.current;
        const itemIndex = currentData.findIndex(d => d.id === itemId);
        if (itemIndex === -1) return;

        const newAllData = [...currentData];
        const item = { ...newAllData[itemIndex] };
        item.isVisible = !item.isVisible;
        newAllData[itemIndex] = item;
        allDataRef.current = newAllData;
        setAllData(newAllData);

        updateLayerVisibility(item, item.isVisible);
    }, [cancelMapBatch, setAllData, updateLayerVisibility]);

    const toggleRadarPreviewVisibility = useCallback((itemId) => {
        cancelMapBatch();
        const currentData = allDataRef.current;
        const itemIndex = currentData.findIndex(d => d.id === itemId);
        if (itemIndex === -1) return;

        const newAllData = [...currentData];
        const item = { ...newAllData[itemIndex] };
        item.isPreviewVisible = !item.isPreviewVisible;
        newAllData[itemIndex] = item;
        allDataRef.current = newAllData;
        setAllData(newAllData);

        updateRadarPreviewVisibility(item, item.isPreviewVisible);
    }, [cancelMapBatch, setAllData, updateRadarPreviewVisibility]);

    const handleSelectAllVisibility = useCallback((e) => {
        const shouldBeVisible = e.target.checked;
        const currentData = allDataRef.current;
        const changedItems = [];
        const newAllData = currentData.map(item => {
            if (item.isVisible === shouldBeVisible) {
                return item;
            }
            const nextItem = { ...item, isVisible: shouldBeVisible };
            changedItems.push(nextItem);
            return nextItem;
        });
        allDataRef.current = newAllData;
        setAllData(newAllData);
        runMapBatch(changedItems, (item) => {
            updateLayerVisibility(item, item.isVisible);
        });
    }, [runMapBatch, setAllData, updateLayerVisibility]);

    const handleSetAllRadarPreviewVisibility = useCallback((shouldBeVisible) => {
        const currentData = allDataRef.current;
        const changedItems = [];
        const newAllData = currentData.map(item => {
            if (item.isPreviewVisible === shouldBeVisible) {
                return item;
            }
            const nextItem = { ...item, isPreviewVisible: shouldBeVisible };
            changedItems.push(nextItem);
            return nextItem;
        });
        allDataRef.current = newAllData;
        setAllData(newAllData);
        runMapBatch(changedItems, (item) => {
            updateRadarPreviewVisibility(item, item.isPreviewVisible);
        });
    }, [runMapBatch, setAllData, updateRadarPreviewVisibility]);

    const handleWaterSceneOnMap = (scene) => {
        if (!mapRef.current || !scene.coverage_polygon) return;
        const layerKey = scene.radar_data_id ? `scene_${scene.id}` : `radar_${scene.id}`;
        if (waterSceneLayersRef.current[layerKey]) {
            waterSceneLayersRef.current[layerKey].remove();
            delete waterSceneLayersRef.current[layerKey];
            return false;
        }
        const latLngs = scene.coverage_polygon.map(p => [p[1], p[0]]);
        const polygon = L.polygon(latLngs, {
            color: '#00e5cc',
            weight: 3,
            fillColor: '#00e5cc',
            fillOpacity: 0.12,
            dashArray: '6, 4',
        });
        polygon.bindPopup(
            `<strong>水体场景 ID=${scene.id}</strong><br>` +
            `${scene.satellite || ''} · ${scene.imaging_date || ''}<br>` +
            `<span style="color:#888;font-size:11px">${(scene.geo_path || '').split(/[\\/]/).pop()}</span>`
        );
        waterSceneLayersRef.current[layerKey] = polygon;
        polygon.addTo(mapRef.current);
        mapRef.current.flyToBounds(L.latLngBounds(latLngs), { padding: [50, 50], maxZoom: 10 });
        polygon.openPopup();
        return true;
    };

    const handleFloodSourcePreviewOnMap = (item) => {
        if (!mapRef.current || !item) return;
        const enriched = {
            ...item,
            displayName: item.displayName || item.file_path?.split(/[\\/]/).pop() || `${item.satellite || 'SAR'} #${item.id}`,
            previewCacheKey: item.previewCacheKey || item.preview_cache_updated_at || `${Date.now()}-${item.id}`,
        };
        if (radarPreviewLayersRef.current[enriched.id]) {
            updateRadarPreviewVisibility(enriched, false);
            return false;
        }
        updateRadarPreviewVisibility(enriched, true);
        if (
            enriched.min_lat != null && enriched.max_lat != null
            && enriched.min_lon != null && enriched.max_lon != null
        ) {
            mapRef.current.flyToBounds(
                L.latLngBounds([[enriched.min_lat, enriched.min_lon], [enriched.max_lat, enriched.max_lon]]),
                { padding: [50, 50], maxZoom: 10 },
            );
        }
        return true;
    };

    // 洪涝事件三图层叠加（灾前/灾后/分类结果），每层可独立开关
    const handleFloodEventOnMap = (ev, layers) => {
        if (!mapRef.current) return;
        const evId = ev.id;

        // 清除该事件已有图层
        const existing = floodEventLayersRef.current[evId];
        if (existing) {
            Object.values(existing).forEach(l => l && l.remove());
        }

        const created = {};
        let allBounds = null;

        const addImageLayer = (key, data, opacity) => {
            if (!data || !data.image_b64 || !data.bounds) return;
            const [minLat, minLon, maxLat, maxLon] = data.bounds;
            const bounds = [[minLat, minLon], [maxLat, maxLon]];
            const url = `data:image/png;base64,${data.image_b64}`;
            const layer = L.imageOverlay(url, bounds, { opacity, interactive: false });
            layer.addTo(mapRef.current);
            created[key] = layer;
            allBounds = allBounds ? allBounds.extend(bounds) : L.latLngBounds(bounds);
        };

        addImageLayer('pre', layers.pre, 0.75);
        addImageLayer('post', layers.post, 0.75);
        addImageLayer('classified', layers.classified, 0.85);

        floodEventLayersRef.current[evId] = created;

        if (allBounds) {
            mapRef.current.flyToBounds(allBounds, { padding: [40, 40], maxZoom: 12 });
        }
    };

    // 切换洪涝事件单个图层可见性
    const toggleFloodEventLayer = (evId, key, visible) => {
        const layers = floodEventLayersRef.current[evId];
        if (!layers || !layers[key]) return;
        if (visible) layers[key].addTo(mapRef.current);
        else layers[key].remove();
    };

    const handleFloodPairPreviewOnMap = (pair) => {
        if (!mapRef.current || !pair?.pre?.coverage_polygon || !pair?.post?.coverage_polygon) return;
        if (floodPairPreviewLayerRef.current) {
            floodPairPreviewLayerRef.current.remove();
            floodPairPreviewLayerRef.current = null;
        }
        const makePolygon = (scene, color, label) => {
            const latLngs = scene.coverage_polygon.map(p => [p[1], p[0]]);
            const polygon = L.polygon(latLngs, {
                color,
                weight: 2,
                fillColor: color,
                fillOpacity: 0.14,
            });
            polygon.bindPopup(
                `<strong>${escapeHtml(label)}</strong><br>` +
                `场景 #${escapeHtml(scene.id)} · ${escapeHtml(scene.satellite || '')}<br>` +
                `${escapeHtml(formatYmd(scene.imaging_date, language))} · ${escapeHtml(scene.polarization || '')}`
            );
            return polygon;
        };

        const prePolygon = makePolygon(pair.pre, '#2563eb', '灾前覆盖');
        const postPolygon = makePolygon(pair.post, '#16a34a', '灾后覆盖');
        const group = L.featureGroup([prePolygon, postPolygon]);
        floodPairPreviewLayerRef.current = group;
        group.addTo(mapRef.current);
        mapRef.current.flyToBounds(group.getBounds(), { padding: [50, 50], maxZoom: 10 });
    };

    const handleFloodVectorOnMap = (impact) => {
        if (!mapRef.current || !impact?.flood_vector_geojson) return;
        const layerId = impact.overlay_id || impact.detection_id || 'current';
        const existing = floodVectorLayersRef.current[layerId];
        if (existing) existing.remove();

        const layer = L.geoJSON(impact.flood_vector_geojson, {
            style: {
                color: '#dc2626',
                weight: 2,
                fillColor: '#ef4444',
                fillOpacity: 0.26,
            },
            onEachFeature: (feature, featureLayer) => {
                const props = feature?.properties || {};
                featureLayer.bindPopup(
                    `<strong>洪涝矢量</strong><br>` +
                    `Overlay #${escapeHtml(layerId)}<br>` +
                    `${escapeHtml(props.name || props.class || '')}`
                );
            },
        });
        floodVectorLayersRef.current[layerId] = layer;
        layer.addTo(mapRef.current);
        const bounds = layer.getBounds();
        if (bounds.isValid()) {
            mapRef.current.flyToBounds(bounds, { padding: [50, 50], maxZoom: 12 });
        }
    };

    const visualizePair = useCallback((pair) => {
        const masterLatLngs = pair.master.coverage_polygon.map(p => [p[1], p[0]]);
        const slaveLatLngs = pair.slave.coverage_polygon.map(p => [p[1], p[0]]);
        const groupBounds = L.latLngBounds(masterLatLngs).extend(slaveLatLngs);
        mapRef.current.flyToBounds(groupBounds, { padding: [50, 50], maxZoom: 10 });
    }, []);

    const togglePairVisibility = useCallback((pairIndex) => {
        const currentPairs = foundPairsRef.current;
        const pair = currentPairs[pairIndex];
        if (!pair) return;

        const nextPair = { ...pair, isVis: !pair.isVis };
        const newFoundPairs = [...currentPairs];
        newFoundPairs[pairIndex] = nextPair;
        foundPairsRef.current = newFoundPairs;
        setFoundPairs(newFoundPairs);

        const pairId = nextPair.task_name;

        if (nextPair.isVis) {
            if (pairLayersRef.current[pairId]) {
                pairLayersRef.current[pairId].addTo(mapRef.current);
            } else {
                const masterLatLngs = nextPair.master.coverage_polygon.map(p => [p[1], p[0]]);
                const slaveLatLngs = nextPair.slave.coverage_polygon.map(p => [p[1], p[0]]);

                const masterPolygon = L.polygon(masterLatLngs, { color: '#3498db', weight: 2, fillOpacity: 0.2 });
                const slavePolygon = L.polygon(slaveLatLngs, { color: '#2ecc71', weight: 2, fillOpacity: 0.2 });

                masterPolygon.bindPopup(`<strong>Master:</strong> ${escapeHtml(nextPair.master.id)}`);
                slavePolygon.bindPopup(`<strong>Slave:</strong> ${escapeHtml(nextPair.slave.id)}`);
                
                const group = L.featureGroup([masterPolygon, slavePolygon]);
                pairLayersRef.current[pairId] = group;
                group.addTo(mapRef.current);
            }
        } else {
            if (pairLayersRef.current[pairId]) {
                pairLayersRef.current[pairId].remove();
            }
        }
    }, [setFoundPairs]);

    const selectedPairsCount = foundPairs.filter(p => p.isSelected).length;
    const hasEnoughRadarScenesForPlanning = radarImagingDates.length >= 2;

    const licenseOk = !!licenseStatus?.ok;
    const avgTaskProgress = activeTasks.length
        ? Math.round(activeTasks.reduce((sum, t) => sum + (t.progress || 0), 0) / activeTasks.length)
        : 0;
    const handleRefreshHealth = useCallback(() => {
        fetchHealthStatus({ refresh: true });
    }, [fetchHealthStatus]);

    const refreshDinsarResults = useCallback(() => {
        fetchDinsarResults({ offset: 0 });
    }, [fetchDinsarResults]);

    const handleCloseCancelTask = useCallback(() => {
        setShowCancelTask(false);
        setCancelTaskPwd('');
    }, [setShowCancelTask, setCancelTaskPwd]);

    const radarPanel = {
        radarCurrentPage,
        radarTotalPages,
        showRadarPageInputError,
        radarPageInputValidationError,
        onSearchAll: searchAllRadarData,
        onSearch: applyRadarSearch,
        onReset: resetRadarSearch,
        onAoiModeChange: handleRadarSearchAoiModeChange,
        onProvinceChange: handleRadarSearchProvinceChange,
        onCityChange: handleRadarSearchCityChange,
        onSetRadarSearchFiles: setRadarSearchFiles,
        updateDraft: updateRadarSearchDraft,
        onPageChange: changeRadarPage,
        onPageSizeChange: handleRadarPageSizeChange,
        onGoToPage: goToRadarPage,
        onSelectAllVisibility: handleSelectAllVisibility,
        onSetAllPreviewVisibility: handleSetAllRadarPreviewVisibility,
        onToggleLayer: toggleLayerVisibility,
        onTogglePreview: toggleRadarPreviewVisibility,
        onRebuildPreview: rebuildRadarPreviewCache,
        onShowDataInfo: handleShowDataInfo,
        onFlyTo: flyTo,
        onChangeSatelliteGroup: changeSatelliteGroup,
    };
    const pairingPanel = {
        onOpenPairingModal: openPairingModal,
        onRefreshRadarSearch: refreshCurrentRadarSearch,
        onRefreshDinsar: refreshDinsarResults,
    };
    const taskPanel = {
        onTaskStart: handleTaskStart,
    };
    const hazardPanel = {
        onPointClick: flyToHazardPoint,
        onToggleVisibility: setShowHazardPoints,
        onScanComplete: fetchHazardPoints,
    };
    const floodPanel = {
        onShowSourceSceneOnMap: handleWaterSceneOnMap,
        onShowSourcePreviewOnMap: handleFloodSourcePreviewOnMap,
        onShowReadyProductOnMap: handleWaterSceneOnMap,
        onShowOnMap: handleWaterSceneOnMap,
        onShowFloodOnMap: handleFloodEventOnMap,
        onShowFloodRunOnMap: handleFloodEventOnMap,
        onToggleFloodLayer: toggleFloodEventLayer,
        onShowFloodPairOnMap: handleFloodPairPreviewOnMap,
        onShowFloodVectorOnMap: handleFloodVectorOnMap,
    };
    const dinsarPanel = {
        dinsarCurrentPage,
        dinsarTotalPages,
        showDinsarPageInputError,
        dinsarPageInputValidationError,
        onSetAllVisibility: handleSetAllDinsarVisibility,
        onScoreFilterChange: handleScoreFilterChange,
        onPageChange: changeDinsarPage,
        onPageSizeChange: handleDinsarPageSizeChange,
        onGoToPage: goToDinsarPage,
        onToggleVisibility: toggleDinsarResultVisibility,
        onLabel: handleLabelResult,
        onAnalyze: handleAnalyzeResult,
    };
    const aiPanel = {
        onTrain: handleTrainAi,
        onPredictAll: handlePredictAll,
    };
    const pairsPanel = {
        onVisualizePair: visualizePair,
        onTogglePairVisibility: togglePairVisibility,
        onCreateDinsarBatch: createDinsarBatch,
    };

    if (!authChecked) {
        return (
            <div className="login-page-wrapper">
                <div className="login-card">
                    <h2>正在检查登录状态...</h2>
                    <p>请稍候，系统正在验证会话。</p>
                </div>
            </div>
        );
    }

    if (!currentUser) {
        return <LoginPage onLoginSuccess={handleLoginSuccess} />;
    }

    return (
        <div id="app-container">
            <AppStatusHeader
                language={language}
                setLanguage={setLanguage}
                currentUser={currentUser}
                isAdmin={isAdmin}
                isReadOnlyUser={isReadOnlyUser}
                activeTasks={activeTasks}
                avgTaskProgress={avgTaskProgress}
                runtimeSummary={runtimeSummary}
                licenseStatus={licenseStatus}
                healthStatus={healthStatus}
                healthLoading={healthLoading}
                healthError={healthError}
                onRefreshHealth={handleRefreshHealth}
                onLogout={handleLogout}
            />
            <div className={`main-layout${isStandaloneLeftPage ? ' main-layout--standalone' : ''}`}>
                <AppSidePanel
                    leftPanelWidth={isStandaloneLeftPage ? '100%' : leftPanelWidth}
                    leftPanelTab={leftPanelTab}
                    setLeftPanelTab={setLeftPanelTab}
                    isStandalone={isStandaloneLeftPage}
                    isAdmin={isAdmin}
                    isReadOnlyUser={isReadOnlyUser}
                    currentUser={currentUser}
                    language={language}
                    apiEndpoint={apiClient.defaults.baseURL}
                    licenseOk={licenseOk}
                    foundPairs={foundPairs}
                    dinsarTotal={dinsarPagination.total}
                    selectedPairsCount={selectedPairsCount}
                    hasEnoughRadarScenesForPlanning={hasEnoughRadarScenesForPlanning}
                    isLoading={isLoading}
                    hasRadarSearched={hasRadarSearched}
                    showHazardPoints={showHazardPoints}
                    hazardPoints={hazardPoints}
                    aiStatus={aiStatus}
                    radarPanel={radarPanel}
                    pairingPanel={pairingPanel}
                    taskPanel={taskPanel}
                    hazardPanel={hazardPanel}
                    floodPanel={floodPanel}
                    dinsarPanel={dinsarPanel}
                    aiPanel={aiPanel}
                    pairsPanel={pairsPanel}
                    sbasAnalysisPanel={sbasAnalysisPanel}
                />

                <div
                    style={{
                        display: isStandaloneLeftPage ? 'none' : 'flex',
                        flex: '1 1 auto',
                        minWidth: 0,
                        minHeight: 0,
                    }}
                >
                    <AppMapWorkspace
                        language={language}
                        showMapRegionLocator={showMapRegionLocator}
                        toggleMapRegionLocator={toggleMapRegionLocator}
                        mapRegionOptions={mapRegionOptions}
                        mapRegionSelection={mapRegionSelection}
                        mapRegionLoading={mapRegionLoading}
                        mapRegionLocating={mapRegionLocating}
                        mapRegionError={mapRegionError}
                        mapRegionLocatedName={mapRegionLocatedName}
                        onMapRegionProvinceChange={handleMapRegionProvinceChange}
                        onMapRegionCityChange={handleMapRegionCityChange}
                        onLocateSelectedRegion={locateSelectedRegionOnMap}
                        onClearMapRegionHighlight={clearMapRegionHighlight}
                        baseLayerKey={baseLayerKey}
                        setBaseLayerKey={setBaseLayerKey}
                        onOpenExportModal={mapExport.openExportModal}
                    />
                </div>

            </div>

            <AppOverlays
                onPairingSubmit={findPairs}
                onPairingAoiModeChange={handlePairingAoiModeChange}
                onPairingProvinceChange={handlePairingProvinceChange}
                onPairingCityChange={handlePairingCityChange}
                licenseLoading={licenseLoading}
                licenseStatus={licenseStatus}
                isAdmin={isAdmin}
                licenseFileRef={licenseFileRef}
                onUploadFile={handleLicenseUpload}
                onRefreshLicenseStatus={fetchLicenseStatus}
                licenseFileName={licenseFileName}
                licenseUploadStatus={licenseUploadStatus}
                activeTasks={activeTasks}
                runtimeSummary={runtimeSummary}
                showCancelTask={showCancelTask}
                cancelTaskPwd={cancelTaskPwd}
                onShowCancelTask={() => setShowCancelTask(true)}
                onCancelTaskPwdChange={setCancelTaskPwd}
                onCancelTaskConfirm={handleCancelActiveTasks}
                onCloseCancelTask={handleCloseCancelTask}
                mapExport={mapExport}
            />
        </div>
    );
}

export default App;






