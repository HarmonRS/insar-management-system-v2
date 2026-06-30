/**
 * usePairingLogic - pairing and time-series stack business logic extracted from App.jsx.
 */
import apiClient from '../api/client';
import { getPairingHealth } from '../api/pairing';
import {
    useUiStore, usePairingStore, useMapStore, useBatchStore, useAuthStore,
} from '../store';
import { getSelectedRegionTreeId } from '../utils/appUiHelpers';

const compactDinsarBatchScene = (scene = {}) => ({
    file_path: scene.file_path || '',
    satellite: scene.satellite || null,
    satellite_family: scene.satellite_family || null,
    imaging_date: scene.imaging_date || null,
    imaging_mode: scene.imaging_mode || null,
    polarization: scene.polarization || null,
    orbit_direction: scene.orbit_direction || null,
    relative_orbit: scene.relative_orbit || null,
    absolute_orbit: scene.absolute_orbit || null,
    has_orbit_data: scene.has_orbit_data ?? null,
    orbit_file_path: scene.orbit_file_path || null,
});

const compactDinsarBatchPair = (pair = {}) => ({
    task_name: pair.task_name || null,
    task_alias: pair.task_alias || pair.task_name || null,
    pair_key: pair.pair_key || null,
    pair_uid: pair.pair_uid || null,
    network_run_id: pair.network_run_id || null,
    network_edge_id: pair.network_edge_id ?? null,
    policy_version: pair.policy_version || null,
    selection_strategy: pair.selection_strategy || null,
    time_baseline_days: pair.time_baseline_days ?? null,
    spatial_baseline_meters: pair.spatial_baseline_meters ?? null,
    scene_center_distance_meters: pair.scene_center_distance_meters ?? pair.spatial_baseline_meters ?? null,
    dinsar_quality_tier: pair.dinsar_quality_tier || null,
    dinsar_quality_score: pair.dinsar_quality_score ?? null,
    dinsar_readiness: pair.dinsar_readiness || null,
    master: compactDinsarBatchScene(pair.master),
    slave: compactDinsarBatchScene(pair.slave),
});

const normalizePairingFamilies = (values) => {
    if (!Array.isArray(values)) return null;
    const normalized = [];
    values.forEach((value) => {
        const compact = String(value || '').trim().toUpperCase().replace(/[-_\s]/g, '');
        if (['LT1', 'LT1A', 'LT1B', 'LUTAN1', 'LUTAN1A', 'LUTAN1B'].includes(compact)) {
            normalized.push('LT1');
        } else if (['S1', 'S1A', 'S1B', 'S1C', 'SENTINEL1', 'SENTINEL1A', 'SENTINEL1B', 'SENTINEL1C'].includes(compact)) {
            normalized.push('S1');
        }
    });
    return [...new Set(normalized)];
};

const readPairingNumber = (params, key, label, { integer = false, min = -Infinity, max = Infinity, defaultValue = null } = {}) => {
    const rawValue = params[key];
    const text = String(rawValue ?? '').trim();
    const parsed = text === '' && defaultValue !== null ? defaultValue : Number(text);
    if (!Number.isFinite(parsed) || (integer && !Number.isInteger(parsed))) {
        return { error: `${label}必须是${integer ? '整数' : '数字'}。` };
    }
    if (parsed < min || parsed > max) {
        return { error: `${label}必须在 ${min} 到 ${max} 之间。` };
    }
    return { value: parsed };
};

const normalizePairingParamsForRequest = (params = {}) => {
    const timeMin = readPairingNumber(params, 'time_baseline_min', '最小时间基线', {
        integer: true,
        min: 0,
        max: 3650,
        defaultValue: 1,
    });
    if (timeMin.error) return timeMin;
    const timeMax = readPairingNumber(params, 'time_baseline_max', '最大时间基线', {
        integer: true,
        min: 1,
        max: 3650,
        defaultValue: 30,
    });
    if (timeMax.error) return timeMax;
    if (timeMin.value > timeMax.value) {
        return { error: '最小时间基线不能大于最大时间基线。' };
    }
    const overlap = readPairingNumber(params, 'overlap_threshold', '两景最小重叠率', {
        min: 0,
        max: 1,
        defaultValue: 0.5,
    });
    if (overlap.error) return overlap;
    const centerDistance = readPairingNumber(params, 'spatial_baseline_max_meters', 'footprint 中心距离上限', {
        integer: true,
        min: 0,
        max: 20000000,
        defaultValue: 5000,
    });
    if (centerDistance.error) return centerDistance;
    const aoiOverlap = readPairingNumber(params, 'aoi_overlap_threshold', 'AOI 覆盖率阈值', {
        min: 0,
        max: 1,
        defaultValue: 0,
    });
    if (aoiOverlap.error) return aoiOverlap;

    return {
        value: {
            ...params,
            time_baseline_min: timeMin.value,
            time_baseline_max: timeMax.value,
            overlap_threshold: overlap.value,
            spatial_baseline_max_meters: centerDistance.value,
            aoi_overlap_threshold: aoiOverlap.value,
        },
    };
};

export default function usePairingLogic({
    fetchRegionGeometry,
    refreshBatchList,
    fetchBatchItems,
    onClearAoiLayer,
}) {
    const { addLog, setIsLoading, setLeftPanelTab } = useUiStore();
    const {
        pairingParams, pairingAoiMode,
        pairingFiles, setPairingFiles,
        pairingRegionSelection,
        setPairingRegionError,
        setShowPairingModal, setFoundPairs, setPairingAlert,
        psAoiMode, psFiles, setPsFiles, psRegionSelection,
        psParams, setPsResults,
    } = usePairingStore();
    const { setAoiLayer } = useMapStore();
    const { setBatchTab, setSelectedBatchId, setBatchItems, setPendingTimeseriesBatchId } = useBatchStore();
    const { currentUser } = useAuthStore();

    const isAdmin = currentUser?.role === 'admin';

    const ensureCanOperate = () => {
        if (!isAdmin) {
            addLog('warn', '当前账号为只读用户，无法执行写操作。');
            return false;
        }
        return true;
    };

    const focusBatchAfterCreate = async (type, batchId) => {
        if (!batchId) return;
        setBatchTab(type);
        setLeftPanelTab('batches');
        setSelectedBatchId(batchId);
        setBatchItems([]);
        await refreshBatchList();
        await fetchBatchItems(type, batchId);
    };

    const createPsBatch = async (direction, stack, options = {}) => {
        if (!ensureCanOperate()) return;
        const { focusAfterCreate = true, sendToProduction = false } = options;
        if (!Array.isArray(stack) || stack.length < 3) {
            addLog('warn', `当前候选栈仅 ${Array.isArray(stack) ? stack.length : 0} 景，SBAS 至少需要 3 景。`);
            return;
        }
        const firstScene = stack[0] || {};
        const planId = firstScene.stack_plan_id || null;
        const batchDirection = firstScene.orbit_direction || direction;
        const planningContext = {
            source: planId ? 'timeseries_stack_plan' : 'find_ps_timeseries',
            plan_id: planId,
            strategy: 'sbas_stack',
            pool_role: 'candidate_timeseries_pool',
            production_contract: 'prepare_run_will_freeze_prepared_sbas_stack',
            direction: batchDirection,
            display_group: direction,
            scene_count: stack.length,
            group_key: firstScene.stack_group_key || null,
            stack_key: firstScene.stack_key || null,
            initial_overlap_threshold: psParams?.initial_overlap_threshold ?? null,
            final_overlap_threshold: psParams?.final_overlap_threshold ?? null,
            network_edge_count: firstScene.stack_network_edge_count ?? null,
            network_warnings: firstScene.stack_network_warnings ?? [],
            stack_dates: stack.map(item => item.imaging_date).filter(Boolean),
        };
        try {
            const response = await apiClient.post('/task-batches/ps', {
                direction: batchDirection,
                plan_id: planId,
                stack,
                name: `TS_${batchDirection}_${new Date().toISOString().slice(0, 10)}`,
                planning_context: planningContext,
            });
            const batchId = response.data?.batch_id || '';
            if (planId && batchId) {
                addLog('info', `时序批次已关联候选栈计划 ${planId}`);
            }
            addLog('success', `已创建时序批次: ${batchId || batchDirection}`);
            addLog('info', '当前批次是候选时序池；正式生产会先执行 prepare，冻结 prepared SBAS 小栈后再进入处理器。');
            if (batchId && sendToProduction) {
                setBatchTab('ps');
                setSelectedBatchId(batchId);
                setPendingTimeseriesBatchId(batchId);
                setLeftPanelTab('ps_production');
                addLog('info', `已将批次 ${batchId} 送入时序生产入口。`);
                return;
            }
            if (focusAfterCreate && batchId) {
                await focusBatchAfterCreate('ps', batchId);
            }
        } catch (error) {
            const errorMessage = error.response?.data?.detail || error.message || '未知错误';
            addLog('error', `时序批次创建失败: ${errorMessage}`);
        }
    };

    const createDinsarBatch = async (options = {}) => {
        if (!ensureCanOperate()) return;
        const chunkSize = Number(options?.chunkSize || 0);
        const foundPairs = usePairingStore.getState().foundPairs;
        const selectedPairs = foundPairs.filter(p => p.isSelected);
        if (selectedPairs.length === 0) {
            addLog('warn', '没有选中的配对可保存。');
            return;
        }
        if (!Number.isInteger(chunkSize) || chunkSize <= 0) {
            addLog('warn', '每批条数必须是大于 0 的整数。');
            return;
        }
        try {
            const createdBatchIds = [];
            const createdAt = new Date().toISOString().slice(0, 10);
            const totalChunks = Math.ceil(selectedPairs.length / chunkSize);
            for (let offset = 0; offset < selectedPairs.length; offset += chunkSize) {
                const chunkIndex = Math.floor(offset / chunkSize) + 1;
                const chunkPairs = selectedPairs
                    .slice(offset, offset + chunkSize)
                    .map(compactDinsarBatchPair);
                const response = await apiClient.post('/task-batches/dinsar', {
                    name: totalChunks > 1
                        ? `DINSAR_${createdAt}_${String(chunkIndex).padStart(3, '0')}_of_${String(totalChunks).padStart(3, '0')}`
                        : `DINSAR_${createdAt}`,
                    pairs: chunkPairs,
                });
                const batchId = response.data?.batch_id || '';
                if (batchId) {
                    createdBatchIds.push(batchId);
                }
                addLog(
                    'success',
                    `已创建 D-InSAR 批次 ${chunkIndex}/${totalChunks}: ${batchId || 'OK'} (${chunkPairs.length} 条)`
                );
            }
            if (createdBatchIds.length > 0) {
                addLog('info', `已按每批 ${chunkSize} 条拆分为 ${createdBatchIds.length} 个 D-InSAR 批次。`);
                await focusBatchAfterCreate('dinsar', createdBatchIds[0]);
            }
        } catch (error) {
            const errorMessage = error.response?.data?.detail || error.message || '未知错误';
            addLog('error', `D-InSAR 批次创建失败: ${errorMessage}`);
        }
    };

    const clearPsResults = () => {
        setPsResults(null);
        onClearAoiLayer();
        setAoiLayer(null);
        addLog('info', '时序 InSAR 候选栈结果已清空。');
    };

    const findPairs = async (e, externalRequireOrbitRef, overridePairingParams = null) => {
        e?.preventDefault?.();
        if (!ensureCanOperate()) return;
        setIsLoading(true);
        addLog('info', '开始查找 D-InSAR 生产配对...');
        setPairingAlert({ warnings: [], fallbackUsed: false });

        const formData = new FormData();
        const sourcePairingParams = overridePairingParams || pairingParams;
        const normalizedParams = normalizePairingParamsForRequest(sourcePairingParams);
        if (normalizedParams.error) {
            addLog('error', normalizedParams.error);
            setIsLoading(false);
            return;
        }
        const effectivePairingParams = {
            ...normalizedParams.value,
            strategy: 'dinsar_production',
            limit_footprint_center_distance: true,
            cross_satellite_pairing: false,
        };
        effectivePairingParams.allowed_satellites = normalizePairingFamilies(sourcePairingParams.allowed_satellites);

        try {
            const pairingHealth = await getPairingHealth();
            if (pairingHealth?.needs_rebuild || pairingHealth?.status !== 'READY') {
                addLog(
                    'warn',
                    `配对基础当前状态为 ${pairingHealth?.status || 'UNKNOWN'}，dirty 场景 ${Number(pairingHealth?.dirty_scene_count || 0)}。请先在生产规划页执行“修复配对基础”。`
                );
                setIsLoading(false);
                return;
            }
        } catch (error) {
            addLog('warn', `配对基础状态检查失败: ${error.response?.data?.detail || error.message}`);
            setIsLoading(false);
            return;
        }

        for (const key in effectivePairingParams) {
            const value = effectivePairingParams[key];
            if (value === null || value === undefined) continue;
            if (typeof value === 'string' && value.trim() === '') continue;
            if (key === 'allowed_satellites' && Array.isArray(value)) {
                if (value.length === 0) continue;
                formData.append(key, JSON.stringify(value));
            } else {
                formData.append(key, value);
            }
        }
        if (externalRequireOrbitRef?.current) {
            formData.append('require_orbit_data', externalRequireOrbitRef.current.checked);
        }

        if (pairingAoiMode === 'shp') {
            if (pairingFiles) {
                Array.from(pairingFiles).forEach(file => {
                    formData.append('files', file);
                });
            }
        } else {
            const selectedRegionTreeId = getSelectedRegionTreeId(pairingRegionSelection);
            if (selectedRegionTreeId) {
                try {
                    const selectedAoiGeoJson = await fetchRegionGeometry(selectedRegionTreeId);
                    if (!selectedAoiGeoJson) {
                        addLog('error', '未获取到行政区边界，请检查后端行政区边界数据。');
                        setIsLoading(false);
                        return;
                    }
                    formData.append('aoi_geojson', JSON.stringify(selectedAoiGeoJson));
                    setAoiLayer(selectedAoiGeoJson);
                } catch (error) {
                    const errorMessage = error.response?.data?.detail || error.message || '行政区边界加载失败';
                    addLog('error', `加载行政区边界失败: ${errorMessage}`);
                    setIsLoading(false);
                    return;
                }
            }
        }

        try {
            const response = await apiClient.post('/find-pairs', formData);
            const { pairs, aoi_geojson } = response.data;
            const warnings = Array.isArray(response.data?.warnings) ? response.data.warnings : [];
            const fallbackUsed = Boolean(response.data?.fallback_used ?? response.data?.fallbackUsed);
            const degraded = Boolean(response.data?.degraded);
            const networkRunId = response.data?.network_run_id || response.data?.networkRunId;
            const policyVersion = response.data?.policy_version || response.data?.policyVersion;
            const candidateCount = Number(response.data?.candidate_count ?? response.data?.candidateCount ?? pairs.length ?? 0);
            const selectedEdgeCount = Number(response.data?.selected_edge_count ?? response.data?.selectedEdgeCount ?? pairs.length ?? 0);

            setFoundPairs(Array.isArray(pairs) ? pairs.map(p => ({ ...p, isSelected: true, isVis: false })) : []);
            setPairingAlert({ warnings, fallbackUsed });
            if (!Array.isArray(pairs) || pairs.length === 0) {
                const emptyMessage = `当前参数下没有满足条件的 D-InSAR 配对。候选 ${candidateCount}，入选 ${selectedEdgeCount}。`;
                const detailText = warnings.length > 0
                    ? warnings.join('\n')
                    : '可以放宽时间基线、footprint 中心距离、重叠率、AOI，或检查配对缓存/精轨状态。';
                addLog('warn', emptyMessage);
                warnings.forEach(msg => addLog('warn', msg));
                setPairingRegionError(`${emptyMessage}\n${detailText}`);
                return;
            }
            if (aoi_geojson) {
                setAoiLayer(aoi_geojson);
            }
            warnings.forEach(msg => addLog('warn', msg));
            if (fallbackUsed && warnings.length === 0) {
                addLog('warn', '配对进入回退路径，请检查数据库函数或收紧筛选条件。');
            }
            if (networkRunId) {
                addLog('info', `配对网络已生成 ${networkRunId} (${policyVersion || 'unknown policy'})`);
            }
            if (degraded) {
                addLog('warn', '当前配对结果来自降级缓存状态，建议尽快执行缓存修复。');
            }
            addLog('success', `成功找到 ${pairs.length} 个 D-InSAR 生产配对（候选 ${candidateCount}，入选 ${selectedEdgeCount}）。`);
            setShowPairingModal(false);
            setPairingFiles(null);
            setLeftPanelTab('pairs');
        } catch (error) {
            const errorMessage = error.response?.data?.detail || error.message;
            addLog('error', `查找 D-InSAR 配对失败: ${errorMessage}`);
        } finally {
            setIsLoading(false);
        }
    };

    const handleFindPsStack = async (e) => {
        e.preventDefault();
        if (!ensureCanOperate()) return;
        if (psAoiMode === 'shp') {
            if (!psFiles || psFiles.length === 0) {
                addLog('warn', '请先选择有效的 Shapefile 文件。');
                return;
            }
        } else {
            const selectedRegionTreeId = getSelectedRegionTreeId(psRegionSelection);
            if (!selectedRegionTreeId) {
                addLog('warn', '请先选择行政区。');
                return;
            }
        }

        setIsLoading(true);
        addLog('info', '开始准备时序 InSAR 候选栈...');

        const formData = new FormData();
        for (const key in psParams) {
            formData.append(key, psParams[key]);
        }
        if (psAoiMode === 'shp') {
            Array.from(psFiles).forEach(file => {
                formData.append('files', file);
            });
        } else {
            const selectedRegionTreeId = getSelectedRegionTreeId(psRegionSelection);
            try {
                const selectedAoiGeoJson = await fetchRegionGeometry(selectedRegionTreeId);
                if (!selectedAoiGeoJson) {
                    addLog('error', '未获取到行政区边界，请检查后端行政区边界数据。');
                    setIsLoading(false);
                    return;
                }
                formData.append('aoi_geojson', JSON.stringify(selectedAoiGeoJson));
                setAoiLayer(selectedAoiGeoJson);
            } catch (error) {
                const errorMessage = error.response?.data?.detail || error.message || '行政区边界加载失败';
                addLog('error', `加载行政区边界失败: ${errorMessage}`);
                setIsLoading(false);
                return;
            }
        }

        try {
            const response = await apiClient.post('/find-ps-timeseries', formData);
            const results = response.data;

            const processedResults = {};
            for (const [direction, stack] of Object.entries(results)) {
                const nameCounts = {};
                processedResults[direction] = stack.map(item => {
                    const baseName = `${item.satellite}_${item.imaging_mode}_${item.imaging_date}`;
                    nameCounts[baseName] = (nameCounts[baseName] || 0) + 1;
                    const count = nameCounts[baseName];
                    const displayName = count > 1 ? `${baseName}_${count - 1}` : baseName;
                    return { ...item, displayName };
                });
            }

            setPsResults(processedResults);

            if (Object.keys(processedResults).length > 0) {
                addLog('success', `成功找到 ${Object.keys(processedResults).length} 个时序 InSAR 候选栈。`);
                setLeftPanelTab('ps_results');
                addLog('info', '候选栈仅作为预览结果保留；需要生产时请手动保存批次或送入生产。');
            } else {
                addLog('info', '在给定的 AOI 和阈值下，未找到满足 SBAS 至少 3 景要求的时序影像栈。');
                setLeftPanelTab('ps_results');
            }
        } catch (error) {
            console.error('时序 InSAR 候选栈准备失败:', error);
            const errorMessage = error.response?.data?.detail || error.message || '未知错误';
            addLog('error', `时序 InSAR 候选栈准备失败: ${errorMessage}`);
        } finally {
            setIsLoading(false);
            setPsFiles(null);
        }
    };

    return {
        findPairs,
        handleFindPsStack,
        createDinsarBatch,
        createPsBatch,
        clearPsResults,
    };
}
