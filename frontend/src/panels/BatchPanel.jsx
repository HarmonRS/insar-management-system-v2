import { useEffect } from 'react';
import { useBatchStore, useUiStore, useAuthStore } from '../store';
import { useI18n } from '../i18n/I18nContext';
import useBatchOperations from '../hooks/useBatchOperations';
import { getDinsarEngineMeta } from '../utils/dinsarEngines';

function engineResultTone(status) {
    const normalized = String(status || '').toLowerCase();
    if (normalized === 'ready') return 'ready';
    if (normalized === 'failed') return 'error';
    if (normalized === 'blocked') return 'warn';
    if (normalized === 'running') return 'info';
    return 'neutral';
}

export default function BatchPanel() {
    const { language } = useI18n();
    const {
        batchTab, setBatchTab,
        selectedBatchId, setSelectedBatchId,
        batchLoading,
        batchError,
        batchItems, setBatchItems,
        dinsarBatches, psBatches,
    } = useBatchStore();
    const { addLog } = useUiStore();
    const { currentUser } = useAuthStore();
    const isAdmin = currentUser?.role === 'admin';
    const isReadOnlyUser = !!currentUser && !isAdmin;

    const ensureCanOperate = () => {
        if (!isAdmin) {
            addLog('warn', '当前账号为只读用户，无法执行写操作。');
            return false;
        }
        return true;
    };

    const {
        refreshBatchList,
        fetchBatchItems,
        updateBatchItemLocal,
        saveBatchItem,
        completeBatch,
    } = useBatchOperations({
        addLog,
        ensureCanOperate,
        batchTab,
        selectedBatchId,
        setDinsarBatches: useBatchStore.getState().setDinsarBatches,
        setPsBatches: useBatchStore.getState().setPsBatches,
        setBatchItems,
        setBatchLoading: useBatchStore.getState().setBatchLoading,
        setBatchError: useBatchStore.getState().setBatchError,
    });

    useEffect(() => {
        refreshBatchList();
    }, [refreshBatchList]);

    const currentBatches = batchTab === 'ps' ? psBatches : dinsarBatches;
    const en = language === 'en';

    return (
        <div className="panel-content">
            <div className="list-toolbar column-layout">
                <div className="toolbar-row">
                    <button
                        className={batchTab === 'dinsar' ? 'active-tool' : ''}
                        onClick={() => {
                            setBatchTab('dinsar');
                            setSelectedBatchId('');
                            setBatchItems([]);
                            refreshBatchList();
                        }}
                    >
                        D-InSAR
                    </button>
                    <button
                        className={batchTab === 'ps' ? 'active-tool' : ''}
                        onClick={() => {
                            setBatchTab('ps');
                            setSelectedBatchId('');
                            setBatchItems([]);
                            refreshBatchList();
                        }}
                    >
                        PS
                    </button>
                    <button onClick={refreshBatchList} disabled={batchLoading}>
                        {batchLoading ? (en ? 'Refreshing...' : '刷新中...') : (en ? 'Refresh Batches' : '刷新批次')}
                    </button>
                </div>
                <div className="toolbar-row">
                    <select
                        value={selectedBatchId}
                        onChange={(e) => {
                            const nextId = e.target.value;
                            setSelectedBatchId(nextId);
                            fetchBatchItems(batchTab, nextId);
                        }}
                        style={{ flex: 1, padding: '6px 8px' }}
                    >
                        <option value="">{en ? '-- Select Batch --' : '-- 选择批次 --'}</option>
                        {currentBatches.map(batch => (
                            <option key={batch.batch_id} value={batch.batch_id}>
                                {batch.name || batch.batch_id} ({batch.completed_items}/{batch.total_items})
                            </option>
                        ))}
                    </select>
                    <button onClick={completeBatch} disabled={!selectedBatchId || batchLoading || isReadOnlyUser}>
                        {en ? 'Mark All Done' : '全部完成'}
                    </button>
                </div>
            </div>
            {batchError && <p className="empty-state">{batchError}</p>}
            {!selectedBatchId && !batchError && (
                <p className="empty-state">{en ? 'Select a batch to view details.' : '请选择一个批次查看明细。'}</p>
            )}
            {selectedBatchId && batchItems.length === 0 && !batchLoading && (
                <p className="empty-state">{en ? 'No items in this batch.' : '该批次暂无明细。'}</p>
            )}
            {selectedBatchId && batchItems.length > 0 && (
                <ul className="data-list">
                    {batchItems.map(item => (
                        <li key={item.id} className="batch-item">
                            <div className="batch-item-main">
                                <strong>{batchTab === 'ps'
                                    ? (item.file_path || '').split(/[\\/]/).pop()
                                    : (item.task_name || `${item.master_imaging_date || ''}_${item.slave_imaging_date || ''}`)
                                }</strong>
                                {batchTab === 'dinsar' && (
                                    <div className="batch-item-meta">
                                        M: {item.master_imaging_date || '-'} / S: {item.slave_imaging_date || '-'}
                                    </div>
                                )}
                                {batchTab === 'dinsar' && (
                                    <div className="batch-engine-results">
                                        {['sarscape', 'landsar', 'pyint'].map((engineCode) => {
                                            const engineMeta = getDinsarEngineMeta(engineCode);
                                            const result = item.engine_results?.[engineCode] || {};
                                            const status = result.status || 'missing';
                                            return (
                                                <span
                                                    key={engineCode}
                                                    className={`batch-engine-chip tone-${engineResultTone(status)}`}
                                                    title={result.skip_reason || result.run_key || ''}
                                                >
                                                    {engineMeta.shortLabel}: {status}
                                                </span>
                                            );
                                        })}
                                    </div>
                                )}
                            </div>
                            <select
                                value={item.status || 'PENDING'}
                                onChange={(e) => updateBatchItemLocal(item.id, 'status', e.target.value)}
                                disabled={isReadOnlyUser}
                            >
                                <option value="PENDING">{en ? 'Pending' : '未审核'}</option>
                                <option value="IN_PROGRESS">{en ? 'In Review' : '审核中'}</option>
                                <option value="COMPLETED">{en ? 'Approved' : '可下发'}</option>
                                <option value="FAILED">{en ? 'Rejected' : '不宜下发'}</option>
                            </select>
                            <input
                                type="text"
                                value={item.remark || ''}
                                onChange={(e) => updateBatchItemLocal(item.id, 'remark', e.target.value)}
                                placeholder={en ? 'Remark' : '备注'}
                                disabled={isReadOnlyUser}
                            />
                            <button onClick={() => saveBatchItem(item)} disabled={isReadOnlyUser}>
                                {en ? 'Save' : '保存'}
                            </button>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
}
