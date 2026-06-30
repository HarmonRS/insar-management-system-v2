import { useCallback, useEffect, useMemo, useState } from 'react';
import {
    getPairingHealth,
    rebuildPairingCache,
    reconcileDirtyPairingCache,
} from '../api/pairing';
import MiniCoverageMap from '../components/MiniCoverageMap';

const formatIso = (value, en = false) => {
    if (!value) return en ? 'Never' : '未执行';
    try {
        return new Date(value).toLocaleString();
    } catch {
        return value;
    }
};

const formatActionMode = (mode, en = false) => {
    switch (mode) {
        case 'full_rebuild':
            return en ? 'Full rebuild' : '全量重建';
        case 'incremental_reconcile':
            return en ? 'Incremental reconcile' : '增量修复';
        case 'auto_reconcile':
            return en ? 'Automatic repair queued' : '自动修复已提交';
        case 'noop':
            return en ? 'No-op reconcile' : '无需修复';
        default:
            return en ? 'Pairing cache action' : '配对缓存操作';
    }
};

export default function PairPlanningPanel({
    foundPairs,
    selectedPairsCount,
    isLoading,
    isReadOnlyUser,
    hasEnoughRadarScenesForPlanning,
    onOpenPairingModal,
    hasRadarSearched,
    onRefreshRadarSearch,
    onSearchAll,
    onRefreshDinsar,
    language,
}) {
    const en = language === 'en';
    const [pairingStatus, setPairingStatus] = useState(null);
    const [pairingStatusLoading, setPairingStatusLoading] = useState(false);
    const [pairingStatusError, setPairingStatusError] = useState('');
    const [pairingRepairing, setPairingRepairing] = useState(false);
    const [pairingFullRebuilding, setPairingFullRebuilding] = useState(false);
    const [pairingActionResult, setPairingActionResult] = useState(null);
    const previewPairs = useMemo(() => foundPairs.slice(0, 20), [foundPairs]);
    const previewPolygons = useMemo(() => (
        previewPairs.flatMap((pair, index) => {
            const taskLabel = pair.task_alias || pair.task_name || `Pair ${index + 1}`;
            return [
                {
                    label: `${taskLabel} / master`,
                    points: pair.master?.coverage_polygon,
                    color: '#2563eb',
                    fillOpacity: 0.08,
                },
                {
                    label: `${taskLabel} / slave`,
                    points: pair.slave?.coverage_polygon,
                    color: '#16a34a',
                    fillOpacity: 0.08,
                },
            ];
        })
    ), [previewPairs]);

    const refreshPairingStatus = useCallback(async () => {
        if (isReadOnlyUser) {
            setPairingStatus(null);
            setPairingStatusError('');
            return;
        }

        setPairingStatusLoading(true);
        setPairingStatusError('');
        try {
            const result = await getPairingHealth();
            setPairingStatus(result);
        } catch (error) {
            setPairingStatus(null);
            setPairingStatusError(
                error.response?.data?.detail ||
                error.message ||
                (en ? 'Failed to fetch pairing foundation status.' : '配对基础状态获取失败。')
            );
        } finally {
            setPairingStatusLoading(false);
        }
    }, [en, isReadOnlyUser]);

    useEffect(() => {
        void refreshPairingStatus();
    }, [refreshPairingStatus]);

    const handlePairingCacheAction = useCallback(async (mode) => {
        const setBusy = mode === 'auto' ? setPairingRepairing : setPairingFullRebuilding;
        setBusy(true);
        setPairingActionResult(null);
        try {
            const result = mode === 'auto'
                ? await reconcileDirtyPairingCache()
                : await rebuildPairingCache();
            setPairingActionResult(result);
            await refreshPairingStatus();
        } catch (error) {
            setPairingActionResult({
                mode,
                error: error.response?.data?.detail || error.message || (
                    en ? 'Pairing cache action failed.' : '配对缓存操作失败。'
                ),
            });
        } finally {
            setBusy(false);
        }
    }, [en, refreshPairingStatus]);

    const pairingActionBusy = pairingRepairing || pairingFullRebuilding;

    return (
        <div className="panel-content" style={{ flex: '1 1 auto', overflowY: 'auto', padding: '12px' }}>
            <div className="panel-card">
                <div className="panel-card-title">{en ? 'Pair Planning' : '配对规划'}</div>
                <p className="panel-card-desc">
                    {en
                        ? 'Filter interferometric pairs by temporal baseline, footprint center distance, and pair footprint overlap ratio. Optional AOI constraint.'
                        : '基于时间基线、footprint 中心距和两景 footprint 最小重叠率筛选干涉对，可选 AOI 约束范围。'}
                </p>
                <div className="header-buttons" style={{ marginTop: '10px' }}>
                    <button onClick={onOpenPairingModal} disabled={isLoading || !hasEnoughRadarScenesForPlanning || isReadOnlyUser} style={{ flex: 1 }}>
                        {en ? 'Plan D-InSAR Pairs' : '生成 D-InSAR 配对'}
                    </button>
                </div>
            </div>

            <div style={{ marginTop: '12px' }}>
                <MiniCoverageMap
                    title={en ? 'D-InSAR Pair Coverage Preview' : 'D-InSAR配对范围预览'}
                    subtitle={
                        foundPairs.length > previewPairs.length
                            ? `${previewPairs.length}/${foundPairs.length} 对`
                            : `${foundPairs.length} 对`
                    }
                    polygons={previewPolygons}
                    height={260}
                    emptyText={en ? 'Run pair planning to preview pair footprints.' : '生成配对后显示候选范围。'}
                />
            </div>

            <div className="panel-card" style={{ marginTop: '12px' }}>
                <div className="panel-card-title">{en ? 'Pairing Foundation' : '配对基础'}</div>
                {isReadOnlyUser ? (
                    <p className="panel-card-desc" style={{ marginBottom: 0 }}>
                        {en ? 'Repair operations require an admin account.' : '配对缓存修复需要管理员账号。'}
                    </p>
                ) : pairingStatusLoading ? (
                    <p className="panel-card-desc" style={{ marginBottom: 0 }}>
                        {en ? 'Loading pairing foundation status...' : '正在加载配对基础状态...'}
                    </p>
                ) : pairingStatusError ? (
                    <div style={{ color: '#b91c1c', fontSize: 13 }}>{pairingStatusError}</div>
                ) : pairingStatus ? (
                    <>
                        <div className="panel-card-row">
                            <span>{en ? 'Status' : '状态'}</span>
                            <strong>{pairingStatus.status || (en ? 'Unknown' : '未知')}</strong>
                        </div>
                        <div className="panel-card-row">
                            <span>{en ? 'Scenes / pairs' : '场景 / 候选对'}</span>
                            <strong>{Number(pairingStatus.scene_count || 0)} / {Number(pairingStatus.pair_count || 0)}</strong>
                        </div>
                        <div className="panel-card-row">
                            <span>{en ? 'Dirty scenes' : 'Dirty 场景'}</span>
                            <strong>{Number(pairingStatus.dirty_scene_count || 0)}</strong>
                        </div>
                        <div className="panel-card-row">
                            <span>{en ? 'Last full rebuild' : '上次全量重建'}</span>
                            <strong>{formatIso(pairingStatus.last_full_rebuild_at, en)}</strong>
                        </div>
                        <div className="panel-card-row">
                            <span>{en ? 'Last incremental reconcile' : '上次增量修复'}</span>
                            <strong>{formatIso(pairingStatus.last_incremental_reconcile_at, en)}</strong>
                        </div>
                        <div
                            style={{
                                marginTop: 10,
                                padding: '10px 12px',
                                borderRadius: 8,
                                fontSize: 13,
                                lineHeight: 1.5,
                                background: pairingStatus.needs_rebuild ? '#fff7ed' : '#f0fdf4',
                                color: pairingStatus.needs_rebuild ? '#9a3412' : '#166534',
                                border: `1px solid ${pairingStatus.needs_rebuild ? '#fdba74' : '#86efac'}`,
                            }}
                        >
                            {pairingStatus.needs_rebuild
                                ? (en
                                    ? 'Pairing candidate cache is not ready. Repair it here before running pair search.'
                                    : '配对候选缓存当前不可直接用于配对。请先在这里修复，再执行配对搜索。')
                                : (en
                                    ? 'Pairing foundation is ready. You can proceed with pair planning.'
                                    : '配对基础已就绪，可以直接进行配对规划。')}
                        </div>
                        <div className="header-buttons" style={{ marginTop: '10px' }}>
                            <button
                                onClick={() => void handlePairingCacheAction('auto')}
                                disabled={pairingActionBusy || isLoading}
                                style={{ flex: 1 }}
                            >
                                {pairingRepairing
                                    ? (en ? 'Repairing...' : '修复中...')
                                    : (en ? 'Repair Pairing Foundation' : '修复配对基础')}
                            </button>
                            <button
                                onClick={() => void handlePairingCacheAction('full')}
                                disabled={pairingActionBusy || isLoading}
                                style={{ flex: 1 }}
                            >
                                {pairingFullRebuilding
                                    ? (en ? 'Rebuilding...' : '重建中...')
                                    : (en ? 'Force Full Rebuild' : '强制全量重建')}
                            </button>
                        </div>
                        <div style={{ marginTop: 8, fontSize: 12, color: '#64748b', lineHeight: 1.5 }}>
                            {en
                                ? '"Repair Pairing Foundation" will choose incremental reconcile or full rebuild automatically based on current dirty state.'
                                : '“修复配对基础”会根据当前 dirty 状态自动选择增量修复或全量重建；只有需要彻底重算时再使用“强制全量重建”。'}
                        </div>
                        {pairingActionResult && (
                            <div
                                style={{
                                    marginTop: 10,
                                    padding: '10px 12px',
                                    borderRadius: 8,
                                    background: '#f8fafc',
                                    border: '1px solid #e2e8f0',
                                    fontSize: 12,
                                    lineHeight: 1.6,
                                }}
                            >
                                {pairingActionResult.error ? (
                                    <div style={{ color: '#b91c1c' }}>{pairingActionResult.error}</div>
                                ) : pairingActionResult.queued ? (
                                    <>
                                        <div style={{ color: '#0f172a', fontWeight: 600 }}>
                                            {formatActionMode(pairingActionResult.mode, en)}
                                        </div>
                                        <div>
                                            {en
                                                ? `Task queued: ${pairingActionResult.task_id || '-'}`
                                                : `任务已提交：${pairingActionResult.task_id || '-'}`}
                                        </div>
                                        <div>
                                            {en
                                                ? 'Track progress in the task center. Refresh this status after the task completes.'
                                                : '请在任务中心查看进度，任务完成后刷新这里的状态。'}
                                        </div>
                                    </>
                                ) : (
                                    <>
                                        <div style={{ color: '#0f172a', fontWeight: 600 }}>
                                            {formatActionMode(pairingActionResult.mode, en)}
                                        </div>
                                        <div>
                                            {en
                                                ? `Scenes / pairs / dirty: ${Number(pairingActionResult.scene_count || 0)} / ${Number(pairingActionResult.pair_count || 0)} / ${Number(pairingActionResult.dirty_scene_count || 0)}`
                                                : `场景 / 候选对 / dirty：${Number(pairingActionResult.scene_count || 0)} / ${Number(pairingActionResult.pair_count || 0)} / ${Number(pairingActionResult.dirty_scene_count || 0)}`}
                                        </div>
                                        <div>
                                            {en
                                                ? `Resolved dirty rows: ${Number(pairingActionResult.resolved_dirty_rows || 0)}, deleted pair rows: ${Number(pairingActionResult.deleted_pair_rows || 0)}`
                                                : `已解决 dirty 记录：${Number(pairingActionResult.resolved_dirty_rows || 0)}，删除旧候选对：${Number(pairingActionResult.deleted_pair_rows || 0)}`}
                                        </div>
                                    </>
                                )}
                            </div>
                        )}
                    </>
                ) : (
                    <p className="panel-card-desc" style={{ marginBottom: 0 }}>
                        {en ? 'Pairing foundation status unavailable.' : '配对基础状态暂不可用。'}
                    </p>
                )}
            </div>

            <div className="panel-card" style={{ marginTop: '12px' }}>
                <div className="panel-card-title">{en ? 'Results & Refresh' : '结果与刷新'}</div>
                <div className="panel-card-row">
                    <span>{en ? 'Generated Pairs' : '已生成配对'}</span>
                    <strong>{foundPairs.length}</strong>
                </div>
                <div className="panel-card-row">
                    <span>{en ? 'Selected' : '已选中'}</span>
                    <strong>{selectedPairsCount}</strong>
                </div>
                <div className="header-buttons" style={{ marginTop: '10px' }}>
                    <button onClick={hasRadarSearched ? onRefreshRadarSearch : onSearchAll} disabled={isLoading} style={{ flex: 1 }}>
                        {hasRadarSearched
                            ? (en ? 'Refresh Current Search' : '刷新当前搜索')
                            : (en ? 'Search All Source Data' : '搜索全部源数据')
                        }
                    </button>
                    <button onClick={onRefreshDinsar} disabled={isLoading} style={{ flex: 1 }}>
                        {en ? 'Refresh Results' : '刷新结果'}
                    </button>
                </div>
            </div>
        </div>
    );
}
