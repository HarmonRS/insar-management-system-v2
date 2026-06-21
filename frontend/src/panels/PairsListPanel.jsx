import { useCallback, useMemo } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { usePairingStore, useAuthStore } from '../store';
import VirtualizedList from '../components/common/VirtualizedList';
import PairListRow from '../components/panels/PairListRow';
import MiniCoverageMap from '../components/MiniCoverageMap';

const PAIR_ROW_HEIGHT = 176;

function PairsListPanel({
    onVisualizePair,
    onTogglePairVisibility,
    onCreateDinsarBatch,
}) {
    const { foundPairs, setFoundPairs, pairingAlert } = usePairingStore(useShallow((state) => ({
        foundPairs: state.foundPairs,
        setFoundPairs: state.setFoundPairs,
        pairingAlert: state.pairingAlert,
    })));
    const { currentUser } = useAuthStore();
    const isReadOnlyUser = !!currentUser && currentUser.role !== 'admin';

    const handlePairSelectionChange = useCallback((index) => {
        setFoundPairs((prevPairs) => {
            if (!prevPairs[index]) return prevPairs;
            const nextPairs = [...prevPairs];
            const pair = nextPairs[index];
            nextPairs[index] = { ...pair, isSelected: !pair.isSelected };
            return nextPairs;
        });
    }, [setFoundPairs]);

    const handleSelectAllPairs = useCallback((e) => {
        const isSelected = e.target.checked;
        setFoundPairs((prevPairs) => prevPairs.map((pair) => (
            pair.isSelected === isSelected ? pair : { ...pair, isSelected }
        )));
    }, [setFoundPairs]);

    const allPairsSelected = useMemo(
        () => foundPairs.length > 0 && foundPairs.every((pair) => pair.isSelected),
        [foundPairs]
    );
    const selectedPairsCount = useMemo(
        () => foundPairs.filter((pair) => pair.isSelected).length,
        [foundPairs]
    );
    const mapPreviewPairs = useMemo(() => {
        const visible = foundPairs.filter((pair) => pair.isVis);
        return visible.slice(0, 24);
    }, [foundPairs]);
    const previewPolygons = useMemo(() => (
        mapPreviewPairs.flatMap((pair, index) => {
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
    ), [mapPreviewPairs]);

    return (
        <>
            <div className="panel-content panel-scroll-shell">
                {(pairingAlert.warnings.length > 0 || pairingAlert.fallbackUsed) && (
                    <div className={`pairing-alert ${pairingAlert.fallbackUsed ? 'danger' : 'warning'}`}>
                        <div className="pairing-alert-header">
                            <span className="pairing-alert-title">配对提示</span>
                            {pairingAlert.fallbackUsed && (
                                <span className="pairing-alert-badge">回退模式</span>
                            )}
                        </div>
                        {pairingAlert.warnings.length > 0 ? (
                            <ul>
                                {pairingAlert.warnings.map((msg, idx) => (
                                    <li key={`${idx}-${String(msg).slice(0, 12)}`}>{String(msg)}</li>
                                ))}
                            </ul>
                        ) : (
                            <p>配对进入回退路径，请检查数据库函数或收紧筛选条件。</p>
                        )}
                    </div>
                )}
                <div className={`pair-planning-layout ${foundPairs.length ? 'with-map' : ''}`}>
                    <div className="pair-planning-list-pane">
                        {foundPairs.length === 0 ? (
                            <p className="empty-state">未找到配对。</p>
                        ) : (
                            <>
                                <div className="list-toolbar">
                                    <input
                                        type="checkbox"
                                        checked={allPairsSelected}
                                        onChange={handleSelectAllPairs}
                                        id="select-all-pairs"
                                    />
                                    <label htmlFor="select-all-pairs">
                                        全选 ({selectedPairsCount} / {foundPairs.length} 已选择)
                                    </label>
                                </div>
                                <VirtualizedList
                                    items={foundPairs}
                                    itemHeight={PAIR_ROW_HEIGHT}
                                    viewportClassName="pair-planning-list-viewport"
                                    getKey={(pair, index) => pair.task_name || `${pair.master?.id || 'm'}-${pair.slave?.id || 's'}-${index}`}
                                    renderItem={(pair, index, key) => (
                                        <PairListRow
                                            key={key || `${pair.task_name}-${index}`}
                                            pair={pair}
                                            index={index}
                                            onToggleSelected={handlePairSelectionChange}
                                            onVisualizePair={onVisualizePair}
                                            onTogglePairVisibility={onTogglePairVisibility}
                                        />
                                    )}
                                />
                            </>
                        )}
                    </div>
                    {foundPairs.length > 0 && (
                        <div className="pair-planning-map-pane">
                            <MiniCoverageMap
                                title="候选配对范围"
                                subtitle={`${mapPreviewPairs.length}/${foundPairs.length} 对显示`}
                                polygons={previewPolygons}
                                height={372}
                                emptyText="点击左侧“显示”后在这里查看配对范围。"
                            />
                        </div>
                    )}
                </div>
            </div>
            <footer className="panel-footer">
                <button
                    onClick={onCreateDinsarBatch}
                    disabled={selectedPairsCount === 0 || isReadOnlyUser}
                    className="footer-button"
                    title="保存选中的配对为任务批次"
                >
                    保存批次 ({selectedPairsCount})
                </button>
            </footer>
        </>
    );
}

export default PairsListPanel;
