/**
 * usePairingLogic — pairing and PS stack business logic extracted from App.jsx
 *
 * Contains: findPairs, handleFindPsStack, createDinsarBatch, createPsBatch,
 * focusBatchAfterCreate, clearPsResults
 */
import apiClient from '../api/client';
import {
    useUiStore, usePairingStore, useMapStore, useBatchStore, useAuthStore,
} from '../store';
import { getSelectedRegionTreeId } from '../utils/appUiHelpers';

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
    const { setBatchTab, setSelectedBatchId, setBatchItems } = useBatchStore();
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
        const { focusAfterCreate = true } = options;
        try {
            const response = await apiClient.post('/task-batches/ps', {
                direction,
                stack,
                name: `PS_${direction}_${new Date().toISOString().slice(0, 10)}`
            });
            const batchId = response.data?.batch_id || '';
            addLog('success', `已创建时序批次: ${batchId || direction}`);
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
            const response = await apiClient.post('/task-batches/dinsar', {
                name: `DINSAR_${new Date().toISOString().slice(0, 10)}`,
                pairs: selectedPairs
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
        for (const key in pairingParams) {
            const value = pairingParams[key];
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
                for (const [direction, stack] of Object.entries(processedResults)) {
                    await createPsBatch(direction, stack, { focusAfterCreate: false });
                }
            } else {
                addLog('info', '在给定的AOI和阈值下，未找到合适的时序影像栈。');
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
