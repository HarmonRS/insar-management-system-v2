/**
 * useDinsarOperations — D-InSAR / AI / hazard business logic extracted from App.jsx
 *
 * Contains: fetchDinsarResults, fetchAiStatus, fetchHazardPoints,
 * handleTaskCompletion, handleLabelResult, handleTrainAi, handlePredictAll,
 * handleAnalyzeResult, handleTaskStart, initializeAppData
 */
import apiClient from '../api/client';
import { useUiStore, useDinsarStore, useHazardStore, useTaskStore, useAuthStore, useRadarStore } from '../store';
import { normalizePagePayload } from '../utils/appHelpers';
import { normalizeTaskStatus } from '../utils/appUiHelpers';
import { DEFAULT_LIST_PAGE_SIZE } from '../config/appConstants';

const NON_BLOCKING_TASK_TYPES = new Set(['UNPACK_ARCHIVES', 'UNPACK_SENTINEL1', 'GF3_UNPACK', 'SCAN_ASSET_INVENTORY', 'COPY_DATA']);

export default function useDinsarOperations({
    onCleanupDinsarLayers,
    fetchRadarImagingDates,
    fetchRadarSearchOptions,
    fetchAllData,
    radarSearchRequestSeqRef,
}) {
    const { addLog, setIsLoading } = useUiStore();
    const {
        dinsarResults, setDinsarResults, dinsarPagination, setDinsarPagination,
        setAiStatus, setActiveAiReport,
    } = useDinsarStore();
    const { setHazardPoints } = useHazardStore();
    const { setPendingTaskIds, setNonBlockingTaskIds, setIsGlobalLocked } = useTaskStore();
    const { currentUser } = useAuthStore();
    const {
        hasRadarSearched, radarPagination,
        radarSearchApplied, radarSearchAppliedAoiMode,
        radarSearchAppliedRegionTreeId, radarSearchAoiToken,
    } = useRadarStore();

    const isAdmin = currentUser?.role === 'admin';

    const ensureCanOperate = () => {
        if (!isAdmin) {
            addLog('warn', '当前账号为只读用户，无法执行写操作。');
            return false;
        }
        return true;
    };

    const fetchAiStatus = async () => {
        try {
            const response = await apiClient.get('/ai/status');
            setAiStatus(response.data);
        } catch (error) {
            console.error("Failed to fetch AI status", error);
        }
    };

    const fetchHazardPoints = async () => {
        try {
            const response = await apiClient.get('/hazard-points');
            setHazardPoints(response.data);
        } catch (error) {
            console.error("获取灾害点失败:", error);
        }
    };

    const fetchDinsarResults = async (options = {}) => {
        const requestedLimit = Math.max(
            1,
            Math.min(
                Number(options.limit ?? dinsarPagination.limit ?? DEFAULT_LIST_PAGE_SIZE) || DEFAULT_LIST_PAGE_SIZE,
                2000
            )
        );
        const requestedOffset = Math.max(
            0,
            Number(options.offset ?? dinsarPagination.offset ?? 0) || 0
        );
        addLog('info', `正在获取D-InSAR结果（offset=${requestedOffset}, limit=${requestedLimit}）...`);
        try {
            const response = await apiClient.get('/dinsar-results', {
                params: {
                    limit: requestedLimit,
                    offset: requestedOffset,
                },
            });
            const pagePayload = normalizePagePayload(response.data, requestedLimit, requestedOffset);
            if (
                pagePayload.items.length === 0 &&
                pagePayload.total > 0 &&
                requestedOffset >= pagePayload.total &&
                requestedOffset > 0
            ) {
                const fallbackOffset = Math.max(0, requestedOffset - requestedLimit);
                await fetchDinsarResults({ limit: requestedLimit, offset: fallbackOffset });
                return;
            }
            setDinsarPagination({
                total: pagePayload.total,
                limit: pagePayload.limit,
                offset: pagePayload.offset,
                hasMore: pagePayload.hasMore,
            });

            onCleanupDinsarLayers();

            setDinsarResults(prevResults => {
                const visibilityMap = {};
                prevResults.forEach(r => {
                    visibilityMap[r.id] = r.isVisible;
                });
                return pagePayload.items.map(item => ({
                    ...item,
                    isVisible: visibilityMap[item.id] || false
                }));
            });

            const currentPage = Math.floor(pagePayload.offset / pagePayload.limit) + 1;
            const totalPages = Math.max(1, Math.ceil(pagePayload.total / pagePayload.limit));
            addLog('success', `D-InSAR结果已加载：第 ${currentPage}/${totalPages} 页，当前页 ${pagePayload.items.length} 条，总计 ${pagePayload.total} 条。`);
            fetchAiStatus();
        } catch (error) {
            addLog('error', `获取Dinsar结果失败: ${error.message}`);
        }
    };

    const handleTaskStart = (taskId, message, options = {}) => {
        const taskType = String(options?.taskType || '').trim().toUpperCase();
        const isNonBlocking = !!options?.nonBlocking || NON_BLOCKING_TASK_TYPES.has(taskType);
        if (taskId) {
            setPendingTaskIds(prev => [...prev, taskId]);
            if (isNonBlocking) {
                setNonBlockingTaskIds(prev => [...new Set([...prev, taskId])]);
            }
        }
        if (!isNonBlocking) {
            setIsGlobalLocked(true);
        }
        if (message) addLog('info', message);
    };

    const handleTaskCompletion = (taskInfo) => {
        console.log("收到任务完成通知:", taskInfo);
        const taskStatus = normalizeTaskStatus(taskInfo?.status);
        const syncRadarViewsAfterUnpack = async () => {
            try {
                await Promise.all([
                    fetchRadarImagingDates(),
                    fetchRadarSearchOptions(),
                ]);
                if (hasRadarSearched) {
                    const requestId = radarSearchRequestSeqRef.current + 1;
                    radarSearchRequestSeqRef.current = requestId;
                    await fetchAllData({
                        limit: radarPagination.limit,
                        offset: radarPagination.offset,
                        criteria: radarSearchApplied,
                        aoiMode: radarSearchAppliedAoiMode,
                        regionTreeId: radarSearchAppliedRegionTreeId,
                        aoiToken: radarSearchAoiToken,
                        files: null,
                        requestId,
                    });
                }
            } catch (error) {
                console.error('解包完成后刷新 LT-1 视图失败:', error);
                addLog('warn', 'LT-1 解包已完成，但刷新数据视图时发生错误，请手动刷新。');
            }
        };

        if (taskInfo.task_type === 'AI_ANALYZE') {
            if (taskInfo.message) {
                try {
                    const result = JSON.parse(taskInfo.message);
                    console.log("解析 AI 诊断结果:", result);

                    if (taskStatus === 'COMPLETED') {
                        const analysisContent = result.analysis || "AI 未能生成有效文字描述，请检查影像质量或重试。";
                        addLog('success', `AI 诊断完成: ${result.result_name || '未知结果'}`);
                        setActiveAiReport({
                            title: result.result_name || `诊断报告 (ID: ${result.result_id})`,
                            content: analysisContent
                        });
                    } else if (taskStatus === 'FAILED') {
                        const errorDetail = result.error || "未知错误";
                        addLog('error', `AI 诊断失败: ${errorDetail}`);
                        setActiveAiReport({
                            title: "AI 诊断失败",
                            content: `### 诊断任务执行失败\n\n**错误详情**：\n> ${errorDetail}\n\n**建议**：\n1. 检查本地 Ollama 服务是否已启动。\n2. 检查网络连接或模型加载是否超时。\n3. 请稍后重试。\n\n<strong class="disclaimer">免责声明</strong>`
                        });
                    }
                } catch (e) {
                    console.error("解析任务结果失败:", e);
                    if (taskStatus === 'FAILED') {
                        addLog('error', `AI 诊断失败: ${taskInfo.message}`);
                    }
                }
            }
        } else if (taskInfo.task_type === 'AI_WARMUP') {
            if (taskStatus === 'COMPLETED') {
                addLog('success', taskInfo.message || 'AI 模型预热完成，显存已就绪。');
            } else if (taskStatus === 'FAILED') {
                addLog('error', `AI 预热失败: ${taskInfo.message}`);
            }
        } else if (taskInfo.task_type === 'AI_TRAIN') {
            if (taskStatus === 'COMPLETED') {
                addLog('success', taskInfo.message || 'AI 模型训练完成。');
                fetchAiStatus();
            } else if (taskStatus === 'FAILED') {
                addLog('error', `AI 训练失败: ${taskInfo.message || '未知错误'}`);
            }
        } else if (taskInfo.task_type === 'AI_PREDICT') {
            if (taskStatus === 'COMPLETED') {
                addLog('success', taskInfo.message || 'AI 质量预测完成。');
                fetchDinsarResults();
            } else if (taskStatus === 'FAILED') {
                addLog('error', `AI 质量预测失败: ${taskInfo.message || '未知错误'}`);
            }
        } else if (taskInfo.task_type === 'SCAN_HAZARD') {
            if (taskStatus === 'COMPLETED') {
                addLog('success', taskInfo.message || '灾害点同步完成。');
                fetchHazardPoints();
            } else if (taskStatus === 'FAILED') {
                addLog('error', `灾害点同步失败: ${taskInfo.message || '未知错误'}`);
            }
        } else if (taskInfo.task_type === 'UNPACK_ARCHIVES') {
            if (taskStatus === 'COMPLETED') {
                addLog('success', taskInfo.message || 'LT-1 解包完成。');
                addLog('info', '正在同步 LT-1 解包后的数据视图...');
                void syncRadarViewsAfterUnpack();
            } else if (taskStatus === 'FAILED') {
                addLog('error', `LT-1 解包失败: ${taskInfo.message || '未知错误'}`);
            }
        } else if (taskInfo.task_type === 'GF3_UNPACK') {
            if (taskStatus === 'COMPLETED') {
                addLog('success', taskInfo.message || 'GF3 解包完成。');
            } else if (taskStatus === 'FAILED') {
                addLog('error', `GF3 解包失败: ${taskInfo.message || '未知错误'}`);
            }
        }
    };

    const handleLabelResult = async (resultId, label) => {
        if (!ensureCanOperate()) return;
        try {
            const newResults = dinsarResults.map(r =>
                r.id === resultId ? { ...r, user_label: label } : r
            );
            setDinsarResults(newResults);

            await apiClient.post(`/dinsar-results/${resultId}/label`,
                new URLSearchParams({ label: label === null ? '' : label })
            );
            fetchAiStatus();
        } catch (error) {
            addLog('error', `标记失败: ${error.message}`);
            fetchDinsarResults();
        }
    };

    const handleTrainAi = async () => {
        if (!ensureCanOperate()) return;
        setIsLoading(true);
        addLog('info', '开始训练AI模型...');
        try {
            const response = await apiClient.post('/ai/train');
            const taskId = response.data.task_id;
            handleTaskStart(taskId, 'AI 模型训练任务已启动，请稍候...');
        } catch (error) {
            const msg = error.response?.data?.detail || error.message;
            addLog('error', `训练失败: ${msg}`);
        } finally {
            setIsLoading(false);
        }
    };

    const handlePredictAll = async () => {
        if (!ensureCanOperate()) return;
        setIsLoading(true);
        addLog('info', '开始全量预测...');
        try {
            const response = await apiClient.post('/ai/predict-all');
            const taskId = response.data.task_id;
            handleTaskStart(taskId, 'AI 质量预测任务已启动，请稍候...');
        } catch (error) {
            const msg = error.response?.data?.detail || error.message;
            addLog('error', `预测失败: ${msg}`);
        } finally {
            setIsLoading(false);
        }
    };

    const handleAnalyzeResult = async (resultId) => {
        if (!ensureCanOperate()) return;
        addLog('info', `正在对结果 ID:${resultId} 发起 AI 智能诊断任务...`);
        setIsGlobalLocked(true);
        try {
            const response = await apiClient.post(`/ai/analyze-result/${resultId}`);
            const taskId = response.data.task_id;
            handleTaskStart(taskId);
            addLog('info', `AI 诊断任务已启动 (ID: ${taskId})，请稍候...`);
        } catch (error) {
            const msg = error.response?.data?.detail || error.message;
            addLog('error', `发起 AI 诊断失败: ${msg}`);
            setIsGlobalLocked(false);
        }
    };

    const initializeAppData = async (options = {}) => {
        const shouldRefreshRadarSearch = !!options.refreshRadarSearch && hasRadarSearched;
        setIsLoading(true);
        addLog('info', '开始加载初始数据...');
        try {
            await Promise.all([
                fetchDinsarResults({ offset: 0 }),
                fetchRadarImagingDates(),
                fetchRadarSearchOptions(),
                fetchHazardPoints(),
            ]);
            if (shouldRefreshRadarSearch) {
                addLog('info', '检测到已有源数据检索结果，正在刷新当前检索页...');
                const requestId = radarSearchRequestSeqRef.current + 1;
                radarSearchRequestSeqRef.current = requestId;
                await fetchAllData({
                    limit: radarPagination.limit,
                    offset: radarPagination.offset,
                    criteria: radarSearchApplied,
                    aoiMode: radarSearchAppliedAoiMode,
                    regionTreeId: radarSearchAppliedRegionTreeId,
                    aoiToken: radarSearchAoiToken,
                    files: null,
                    requestId,
                });
            }
            addLog('success', shouldRefreshRadarSearch ? '系统数据与检索结果已同步。' : '系统初始数据加载完毕。');
        } catch {
            addLog('error', '加载初始数据时发生一个或多个错误。');
        } finally {
            setIsLoading(false);
        }
    };

    return {
        fetchDinsarResults,
        fetchAiStatus,
        fetchHazardPoints,
        handleTaskCompletion,
        handleLabelResult,
        handleTrainAi,
        handlePredictAll,
        handleAnalyzeResult,
        handleTaskStart,
        initializeAppData,
    };
}
