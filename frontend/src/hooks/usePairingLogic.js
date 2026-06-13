/**
 * usePairingLogic — pairing and PS stack business logic extracted from App.jsx
 *
 * Contains: findPairs, handleFindPsStack, createDinsarBatch, createPsBatch,
 * focusBatchAfterCreate, clearPsResults
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
    imaging_date: scene.imaging_date || null,
    imaging_mode: scene.imaging_mode || null,
    polarization: scene.polarization || null,
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
    master: compactDinsarBatchScene(pair.master),
    slave: compactDinsarBatchScene(pair.slave),
});

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
        setShowPairingModal, setFoundPairs, setPairingAlert,
        psAoiMode, psFiles, setPsFiles, psRegionSelection,
        psParams, setShowPsModal, setPsResults,
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

    const createDinsarBatch = async () => {
        if (!ensureCanOperate()) return;
        const foundPairs = usePairingStore.getState().foundPairs;
        const selectedPairs = foundPairs.filter(p => p.isSelected);
        if (selectedPairs.length === 0) {
            addLog('warn', '没有选中的配对可保存。');
            return;
        }
        try {
            const batchPairs = selectedPairs.map(compactDinsarBatchPair);
            const response = await apiClient.post('/task-batches/dinsar', {
                name: `DINSAR_${new Date().toISOString().slice(0, 10)}`,
                pairs: batchPairs
            });
            const batchId = response.data?.batch_id || '';
            addLog('success', `已创建 D-InSAR 批次: ${batchId || 'OK'}`);
            if (batchId) {
                await focusBatchAfterCreate('dinsar', batchId);
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
        addLog('info', '时序InSAR 候选栈结果已清空。');
    };

    const findPairs = async (e, externalRequireOrbitRef) => {
        e.preventDefault();
        if (!ensureCanOperate()) return;
        setIsLoading(true);
        addLog('info', '开始寻找干涉对...');
        setPairingAlert({ warnings: [], fallbackUsed: false });

        const formData = new FormData();
        const effectivePairingParams = { ...pairingParams };
        if (!effectivePairingParams.strategy) {
            effectivePairingParams.strategy = 'sbas';
        }
        if (effectivePairingParams.strategy === 'all') {
            const hasDateWindow = Boolean(
                effectivePairingParams.master_date_from
                || effectivePairingParams.master_date_to
                || effectivePairingParams.slave_date_from
                || effectivePairingParams.slave_date_to
            );
            if (!hasDateWindow && pairingAoiMode !== 'region' && !pairingFiles?.length) {
                addLog('warn', '全部配对可能返回大量结果。请先限定行政区、上传 AOI 或设置主/从影像时间范围。');
                setIsLoading(false);
                return;
            }
        }

        try {
            const pairingHealth = await getPairingHealth();
            if (pairingHealth?.needs_rebuild || pairingHealth?.status !== 'READY') {
                addLog(
                    'warn',
                    `配对基础当前状态为 ${pairingHealth?.status || 'UNKNOWN'}，dirty 场景 ${Number(pairingHealth?.dirty_scene_count || 0)}。请先在“配对规划”页执行“修复配对基础”。`
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
            // 跳过 null/undefined 值
            if (value === null || value === undefined) continue;
            // allowed_satellites 是数组，需要序列化为 JSON
            if (key === 'allowed_satellites' && Array.isArray(value)) {
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
            if (!selectedRegionTreeId) {
                addLog('warn', '请选择行政区后再执行配对。');
                setIsLoading(false);
                return;
            }
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
            const response = await apiClient.post('/find-pairs', formData);
            const { pairs, aoi_geojson } = response.data;
            const warnings = Array.isArray(response.data?.warnings) ? response.data.warnings : [];
            const fallbackUsed = Boolean(response.data?.fallback_used ?? response.data?.fallbackUsed);
            const degraded = Boolean(response.data?.degraded);
            const networkRunId = response.data?.network_run_id || response.data?.networkRunId;
            const policyVersion = response.data?.policy_version || response.data?.policyVersion;
            const candidateCount = Number(response.data?.candidate_count ?? response.data?.candidateCount ?? pairs.length ?? 0);
            const selectedEdgeCount = Number(response.data?.selected_edge_count ?? response.data?.selectedEdgeCount ?? pairs.length ?? 0);
            setFoundPairs(pairs.map(p => ({ ...p, isSelected: true, isVis: false })));
            if (aoi_geojson) {
                setAoiLayer(aoi_geojson);
            }
            setPairingAlert({ warnings, fallbackUsed });
            if (warnings.length > 0) {
                warnings.forEach(msg => addLog('warn', msg));
            }
            if (fallbackUsed && warnings.length === 0) {
                addLog('warn', '配对进入回退路径，请检查数据库函数或收紧筛选条件。');
            }
            if (networkRunId) {
                addLog('info', `配对网络已生成: ${networkRunId} (${policyVersion || 'unknown policy'})`);
            }
            if (degraded) {
                addLog('warn', '当前配对结果来自降级缓存状态，建议尽快执行缓存修复。');
            }
            addLog('success', `成功找到 ${pairs.length} 个干涉对（候选 ${candidateCount}，入选 ${selectedEdgeCount}）。`);
            setShowPairingModal(false);
            setPairingFiles(null);
            setLeftPanelTab('pairs');
        } catch (error) {
            const errorMessage = error.response?.data?.detail || error.message;
            addLog('error', `寻找干涉对失败: ${errorMessage}`);
        } finally {
            setIsLoading(false);
        }
    };

    const handleFindPsStack = async (e) => {
        e.preventDefault();
        if (!ensureCanOperate()) return;
        if (psAoiMode === 'shp') {
            if (!psFiles || psFiles.length === 0) {
                addLog('warn', '请先选择有效的Shapefile文件。');
                return;
            }
        } else {
            const selectedRegionTreeId = getSelectedRegionTreeId(psRegionSelection);
            if (!selectedRegionTreeId) {
                addLog('warn', '请先选择行政区。');
                return;
            }
        }

        setShowPsModal(false);
        setIsLoading(true);
        addLog('info', '开始准备时序InSAR候选栈...');

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
                addLog('success', `成功找到 ${Object.keys(processedResults).length} 个时序InSAR候选栈。`);
                setLeftPanelTab('ps_results');
                addLog('info', '候选栈仅作为预览结果保留；需要生产时请手动保存批次或送入生产。');
            } else {
                addLog('info', '在给定的AOI和阈值下，未找到满足 SBAS 至少 3 景要求的时序影像栈。');
                setLeftPanelTab('ps_results');
            }
        } catch (error) {
            console.error('时序InSAR候选栈准备失败:', error);
            const errorMessage = error.response?.data?.detail || error.message || '未知错误';
            addLog('error', `时序InSAR候选栈准备失败: ${errorMessage}`);
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
