import { useMemo } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { usePairingStore, useAuthStore } from '../store';
import PsStackSection from '../components/panels/PsStackSection';

function PsResultsPanel({
    onPreviewPsStack,
    onClearPsStackPreview,
    onCreatePsBatch,
    onSendToTimeseriesProduction,
    onClearPsResults,
}) {
    const { psResults } = usePairingStore(useShallow((state) => ({
        psResults: state.psResults,
    })));
    const { currentUser } = useAuthStore();
    const isReadOnlyUser = !!currentUser && currentUser.role !== 'admin';
    const psStacks = useMemo(() => Object.entries(psResults || {}), [psResults]);

    return (
        <div className="panel-content panel-scroll-shell">
            {psStacks.length === 0 ? (
                <p className="empty-state">未找到满足 SBAS 至少 3 景要求的时序InSAR候选栈。</p>
            ) : (
                <>
                    <div className="list-toolbar">
                        <button onClick={onClearPsResults}>清空结果</button>
                    </div>
                    <div className="ps-stack-list">
                        {psStacks.map(([direction, stack]) => (
                            <PsStackSection
                                key={direction}
                                direction={direction}
                                stack={stack}
                                isReadOnlyUser={isReadOnlyUser}
                                onPreviewPsStack={onPreviewPsStack}
                                onClearPsStackPreview={onClearPsStackPreview}
                                onCreatePsBatch={onCreatePsBatch}
                                onSendToTimeseriesProduction={onSendToTimeseriesProduction}
                            />
                        ))}
                    </div>
                </>
            )}
        </div>
    );
}

export default PsResultsPanel;
