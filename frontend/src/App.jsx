import { useEffect, useRef, useCallback } from 'react';
import L from 'leaflet';
import { useShallow } from 'zustand/react/shallow';
import 'leaflet/dist/leaflet.css';
import './App.css';
import LoginPage from './LoginPage';
import AppLogPanel from './components/app/AppLogPanel';
import AppMapWorkspace from './components/app/AppMapWorkspace';
import AppOverlays from './components/app/AppOverlays';
import AppSidePanel from './components/app/AppSidePanel';
import AppStatusHeader from './components/app/AppStatusHeader';
import { useI18n } from './i18n/I18nContext';
import apiClient from './api/client';
import {
    useAuthStore, useTaskStore, useUiStore, useRadarStore,
    useDinsarStore, useBatchStore, usePairingStore, useHazardStore, useMapStore,
} from './store';
import useAppAuthLifecycle from './hooks/useAppAuthLifecycle';
import useGlobalTaskControl from './hooks/useGlobalTaskControl';
import usePanelResize from './hooks/usePanelResize';
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
        activeTasks, setActiveTasks, isGlobalLocked, setIsGlobalLocked,
        isCheckingTasks, setIsCheckingTasks,
        pendingTaskIds, setPendingTaskIds,
        nonBlockingTaskIds, setNonBlockingTaskIds,
    } = useTaskStore(useShallow((state) => ({
        activeTasks: state.activeTasks,
        setActiveTasks: state.setActiveTasks,
        isGlobalLocked: state.isGlobalLocked,
        setIsGlobalLocked: state.setIsGlobalLocked,
        isCheckingTasks: state.isCheckingTasks,
        setIsCheckingTasks: state.setIsCheckingTasks,
        pendingTaskIds: state.pendingTaskIds,
        setPendingTaskIds: state.setPendingTaskIds,
        nonBlockingTaskIds: state.nonBlockingTaskIds,
        setNonBlockingTaskIds: state.setNonBlockingTaskIds,
    })));
    const {
        leftPanelTab, setLeftPanelTab, leftPanelWidth, setLeftPanelWidth,
        rightPanelWidth, setRightPanelWidth, isResizing, setIsResizing,
        setShowStats, setShowDataInfo, setSelectedDataInfo, showDates,
        baseLayerKey, setBaseLayerKey, isLoading, setIsLoading, addLog,
    } = useUiStore(useShallow((state) => ({
        leftPanelTab: state.leftPanelTab,
        setLeftPanelTab: state.setLeftPanelTab,
        leftPanelWidth: state.leftPanelWidth,
        setLeftPanelWidth: state.setLeftPanelWidth,
        rightPanelWidth: state.rightPanelWidth,
        setRightPanelWidth: state.setRightPanelWidth,
        isResizing: state.isResizing,
        setIsResizing: state.setIsResizing,
        setShowStats: state.setShowStats,
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
        foundPairs, setFoundPairs, psResults,
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
        psResults: state.psResults,
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
    const hazardLayersGroupRef = useRef(null);
    const aoeLayerRef = useRef(null);
    const waterSceneLayersRef = useRef({});
    const floodEventLayersRef = useRef({});  // { eventId: { pre, post, classified } }
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
    const resizeStateRef = useRef({ side: null, startX: 0, startLeft: 0, startRight: 0 });
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
    }, [isStandaloneLeftPage, leftPanelWidth, rightPanelWidth]);

    const getVisibleLayerRefs = useCallback(() => ({
        activeLayersRef: activeLayersRef.current,
        hazardLayersGroupRef: hazardLayersGroupRef.current,
        dinsarResultLayersRef: dinsarResultLayersRef.current,
        waterSceneLayersRef: waterSceneLayersRef.current,
        radarPreviewLayersRef: radarPreviewLayersRef.current,
        pairLayersRef: pairLayersRef.current,
        aoeLayerRef: aoeLayerRef.current,
        floodEventLayersRef: floodEventLayersRef.current,
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
        if (isCheckingTasks || isGlobalLocked) {
            addLog('warn', '系统正在处理任务，请稍候...');
            return false;
        }
        return true;
    }, [addLog, isAdmin, isCheckingTasks, isGlobalLocked]);

    const clearRadarMapLayers = () => {
        cancelMapBatch();
        if (aoeLayerRef.current) {
            aoeLayerRef.current.remove();
            aoeLayerRef.current = null;
        }
        setAoiLayer(null);

        Object.values(activeLayersRef.current).forEach(layer => layer.remove());
        activeLayersRef.current = {};
        Object.values(radarPreviewLayersRef.current).forEach(layer => layer.remove());
        radarPreviewLayersRef.current = {};
    };

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
        handlePsAoiModeChange,
        handlePairingProvinceChange,
        handlePairingCityChange,
        handlePsProvinceChange,
        handlePsCityChange,
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
        openPsModal,
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

    const { startResize } = usePanelResize({
        isResizing,
        setIsResizing,
        leftPanelWidth,
        rightPanelWidth,
        setLeftPanelWidth,
        setRightPanelWidth,
        resizeStateRef,
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
        setIsGlobalLocked,
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
        forceUnlockPwd,
        setForceUnlockPwd,
        showForceUnlock,
        setShowForceUnlock,
        handleForceUnlock,
    } = useGlobalTaskControl({
        currentUser,
        licenseOk: !!licenseStatus?.ok,
        activeTasks,
        setActiveTasks,
        pendingTaskIds,
        setPendingTaskIds,
        nonBlockingTaskIds,
        setNonBlockingTaskIds,
        isGlobalLocked,
        setIsGlobalLocked,
        setIsCheckingTasks,
        handleTaskCompletionRef,
        initializeAppDataRef,
        addLog,
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

        // 立即锁定前端
        handleTaskStart(null, `正在生成影像 ${itemId} 的预览缓存...`);

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

    const previewPsStack = (stack) => {
        cancelMapBatch();
        const stackIds = new Set(stack.map(img => img.id));
        const currentData = allDataRef.current;
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
        addLog('info', `正在预览包含 ${stack.length} 个场景的时序InSAR候选栈。`);
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
        const strategy = escapeHtml(result.selection_strategy || 'legacy');
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
        handleFindPsStack,
        createDinsarBatch,
        createPsBatch,
        clearPsResults,
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

    const updateRadarPreviewVisibility = useCallback((item, shouldBeVisible) => {
        if (!item || !mapRef.current) return;
        const itemId = item.id;
        const layer = radarPreviewLayersRef.current[itemId];

        if (shouldBeVisible) {
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
            fetchRadarPreviewStatus(itemId, { silent: true });
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
        if (waterSceneLayersRef.current[scene.id]) {
            waterSceneLayersRef.current[scene.id].remove();
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
        waterSceneLayersRef.current[scene.id] = polygon;
        polygon.addTo(mapRef.current);
        mapRef.current.flyToBounds(L.latLngBounds(latLngs), { padding: [50, 50], maxZoom: 10 });
        polygon.openPopup();
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

    const openStatisticsDashboard = useCallback(() => {
        setShowStats(true);
    }, [setShowStats]);

    const refreshDinsarResults = useCallback(() => {
        fetchDinsarResults({ offset: 0 });
    }, [fetchDinsarResults]);

    const handleCancelForceUnlock = useCallback(() => {
        setShowForceUnlock(false);
        setForceUnlockPwd('');
    }, [setShowForceUnlock, setForceUnlockPwd]);

    const radarPanel = {
        radarCurrentPage,
        radarTotalPages,
        showRadarPageInputError,
        radarPageInputValidationError,
        onSearchAll: searchAllRadarData,
        onShowStats: openStatisticsDashboard,
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
        onOpenPsModal: openPsModal,
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
    const waterPanel = {
        onShowOnMap: handleWaterSceneOnMap,
        onShowFloodOnMap: handleFloodEventOnMap,
        onToggleFloodLayer: toggleFloodEventLayer,
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
    const psPanel = {
        onPreviewPsStack: previewPsStack,
        onCreatePsBatch: createPsBatch,
        onClearPsResults: clearPsResults,
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
                    psResults={psResults}
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
                    waterPanel={waterPanel}
                    dinsarPanel={dinsarPanel}
                    aiPanel={aiPanel}
                    pairsPanel={pairsPanel}
                    psPanel={psPanel}
                />

                <div
                    className="panel-resizer"
                    onMouseDown={(event) => startResize('left', event)}
                    style={{ display: isStandaloneLeftPage ? 'none' : undefined }}
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

                <div
                    className="panel-resizer"
                    onMouseDown={(event) => startResize('right', event)}
                    style={{ display: isStandaloneLeftPage ? 'none' : undefined }}
                />

                <div style={{ display: isStandaloneLeftPage ? 'none' : undefined }}>
                    <AppLogPanel width={rightPanelWidth} />
                </div>
            </div>

            <AppOverlays
                onPairingSubmit={findPairs}
                onPairingAoiModeChange={handlePairingAoiModeChange}
                onPairingProvinceChange={handlePairingProvinceChange}
                onPairingCityChange={handlePairingCityChange}
                onPsSubmit={handleFindPsStack}
                onPsAoiModeChange={handlePsAoiModeChange}
                onPsProvinceChange={handlePsProvinceChange}
                onPsCityChange={handlePsCityChange}
                licenseLoading={licenseLoading}
                licenseStatus={licenseStatus}
                isAdmin={isAdmin}
                licenseFileRef={licenseFileRef}
                onUploadFile={handleLicenseUpload}
                onRefreshLicenseStatus={fetchLicenseStatus}
                licenseFileName={licenseFileName}
                licenseUploadStatus={licenseUploadStatus}
                isGlobalLocked={isGlobalLocked}
                activeTasks={activeTasks}
                showForceUnlock={showForceUnlock}
                forceUnlockPwd={forceUnlockPwd}
                onShowForceUnlock={() => setShowForceUnlock(true)}
                onForceUnlockPwdChange={setForceUnlockPwd}
                onForceUnlockConfirm={handleForceUnlock}
                onCancelForceUnlock={handleCancelForceUnlock}
                mapExport={mapExport}
            />
        </div>
    );
}

export default App;






