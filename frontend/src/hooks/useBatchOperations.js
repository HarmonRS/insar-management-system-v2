/**
 * useBatchOperations — batch (D-InSAR / PS) CRUD logic extracted from App.jsx
 *
 * Contains: fetchDinsarBatches, fetchPsBatches, refreshBatchList,
 * fetchBatchItems, updateBatchItemLocal, saveBatchItem, completeBatch
 */
import { useCallback } from 'react';
import apiClient from '../api/client';
import {
    BATCH_API_PAGE_LIMIT,
    BATCH_API_MAX_PAGES,
} from '../config/appConstants';

export default function useBatchOperations({
    addLog,
    ensureCanOperate,
    batchTab,
    selectedBatchId,
    setDinsarBatches,
    setPsBatches,
    setBatchItems,
    setBatchLoading,
    setBatchError,
}) {
    const fetchDinsarBatches = useCallback(async () => {
        try {
            const batches = [];
            for (let page = 0; page < BATCH_API_MAX_PAGES; page += 1) {
                const offset = page * BATCH_API_PAGE_LIMIT;
                const response = await apiClient.get('/task-batches/dinsar', {
                    params: { limit: BATCH_API_PAGE_LIMIT, offset }
                });
                const items = Array.isArray(response.data) ? response.data : [];
                batches.push(...items);
                if (items.length < BATCH_API_PAGE_LIMIT) break;
            }
            setDinsarBatches(batches);
        } catch {
            setDinsarBatches([]);
        }
    }, [setDinsarBatches]);

    const fetchPsBatches = useCallback(async () => {
        try {
            const batches = [];
            for (let page = 0; page < BATCH_API_MAX_PAGES; page += 1) {
                const offset = page * BATCH_API_PAGE_LIMIT;
                const response = await apiClient.get('/task-batches/ps', {
                    params: { limit: BATCH_API_PAGE_LIMIT, offset }
                });
                const items = Array.isArray(response.data) ? response.data : [];
                batches.push(...items);
                if (items.length < BATCH_API_PAGE_LIMIT) break;
            }
            setPsBatches(batches);
        } catch {
            setPsBatches([]);
        }
    }, [setPsBatches]);

    const refreshBatchList = useCallback(async () => {
        setBatchLoading(true);
        setBatchError('');
        try {
            await Promise.all([fetchDinsarBatches(), fetchPsBatches()]);
        } catch {
            setBatchError('加载批次失败');
        } finally {
            setBatchLoading(false);
        }
    }, [fetchDinsarBatches, fetchPsBatches, setBatchLoading, setBatchError]);

    const fetchBatchItems = useCallback(async (type, batchId) => {
        if (!batchId) {
            setBatchItems([]);
            return;
        }
        setBatchLoading(true);
        setBatchError('');
        try {
            const endpoint = type === 'ps'
                ? `/task-batches/ps/${batchId}/items`
                : `/task-batches/dinsar/${batchId}/items`;
            const allItems = [];
            for (let page = 0; page < BATCH_API_MAX_PAGES; page += 1) {
                const offset = page * BATCH_API_PAGE_LIMIT;
                const response = await apiClient.get(endpoint, {
                    params: { limit: BATCH_API_PAGE_LIMIT, offset }
                });
                const items = Array.isArray(response.data) ? response.data : [];
                allItems.push(...items);
                if (items.length < BATCH_API_PAGE_LIMIT) break;
            }
            setBatchItems(allItems);
        } catch {
            setBatchError('加载批次明细失败');
            setBatchItems([]);
        } finally {
            setBatchLoading(false);
        }
    }, [setBatchItems, setBatchLoading, setBatchError]);

    const updateBatchItemLocal = useCallback((id, field, value) => {
        setBatchItems(prev =>
            prev.map(item => (item.id === id ? { ...item, [field]: value } : item))
        );
    }, [setBatchItems]);

    const saveBatchItem = useCallback(async (item) => {
        if (!ensureCanOperate()) return;
        try {
            const endpoint = batchTab === 'ps'
                ? `/task-batches/ps/items/${item.id}`
                : `/task-batches/dinsar/items/${item.id}`;
            await apiClient.patch(endpoint, {
                status: item.status,
                remark: item.remark ?? ''
            });
            await refreshBatchList();
        } catch {
            addLog('error', '批次明细更新失败');
        }
    }, [batchTab, ensureCanOperate, refreshBatchList, addLog]);

    const completeBatch = useCallback(async () => {
        if (!ensureCanOperate()) return;
        if (!selectedBatchId) return;
        try {
            const endpoint = batchTab === 'ps'
                ? `/task-batches/ps/${selectedBatchId}/complete-all`
                : `/task-batches/dinsar/${selectedBatchId}/complete-all`;
            await apiClient.patch(endpoint);
            await fetchBatchItems(batchTab, selectedBatchId);
            await refreshBatchList();
        } catch {
            addLog('error', '批次一键完成失败');
        }
    }, [batchTab, selectedBatchId, ensureCanOperate, fetchBatchItems, refreshBatchList, addLog]);

    return {
        fetchDinsarBatches,
        fetchPsBatches,
        refreshBatchList,
        fetchBatchItems,
        updateBatchItemLocal,
        saveBatchItem,
        completeBatch,
    };
}
