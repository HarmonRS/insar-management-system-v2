import { useCallback, useMemo } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { usePairingStore, useAuthStore } from '../store';
import VirtualizedList from '../components/common/VirtualizedList';
import PairListRow from '../components/panels/PairListRow';

const PAIR_ROW_HEIGHT = 64;

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
