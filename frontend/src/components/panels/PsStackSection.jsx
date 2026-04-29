import { memo, useMemo } from 'react';
import VirtualizedList from '../common/VirtualizedList';

const PS_STACK_ROW_HEIGHT = 24;
const PS_STACK_MAX_HEIGHT = 168;

const PsStackItemRow = memo(function PsStackItemRow({ item }) {
    return (
        <li className="ps-stack-item" title={item.file_path}>
            {item.displayName}
        </li>
    );
});

function PsStackSection({
    direction,
    stack,
    isReadOnlyUser,
    onPreviewPsStack,
    onClearPsStackPreview,
    onCreatePsBatch,
    onSendToTimeseriesProduction,
}) {
    const planMeta = stack[0] || {};
    const commonAoiRatio = planMeta.stack_common_aoi_coverage_ratio == null
        ? NaN
        : Number(planMeta.stack_common_aoi_coverage_ratio);
    const consistencyRatio = planMeta.stack_coverage_consistency_ratio == null
        ? NaN
        : Number(planMeta.stack_coverage_consistency_ratio);
    const selectionModeLabel = planMeta.stack_selection_mode === 'pairwise_sbas_network'
        ? 'SBAS网络'
        : (planMeta.stack_selection_mode === 'common_overlap' ? '公共栈' : '');
    const viewportHeight = useMemo(
        () => Math.min(Math.max(stack.length * PS_STACK_ROW_HEIGHT, PS_STACK_ROW_HEIGHT), PS_STACK_MAX_HEIGHT),
        [stack.length]
    );

    return (
        <div className="ps-stack">
            <div className="ps-stack-header">
                <div>
                    <h4>{direction} ({stack.length} scenes)</h4>
                    {(planMeta.stack_plan_id || planMeta.stack_key) && (
                        <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>
                            {planMeta.stack_plan_id ? `plan=${planMeta.stack_plan_id}` : ''}
                            {planMeta.stack_plan_id && planMeta.stack_key ? ' / ' : ''}
                            {planMeta.stack_key ? `stack=${planMeta.stack_key}` : ''}
                        </div>
                    )}
                    {(Number.isFinite(consistencyRatio) || Number.isFinite(commonAoiRatio)) && (
                        <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>
                            {selectionModeLabel ? `${selectionModeLabel} / ` : ''}
                            {Number.isFinite(consistencyRatio) ? `一致性 ${(consistencyRatio * 100).toFixed(1)}%` : ''}
                            {Number.isFinite(consistencyRatio) && Number.isFinite(commonAoiRatio) ? ' / ' : ''}
                            {Number.isFinite(commonAoiRatio) ? `公共AOI ${(commonAoiRatio * 100).toFixed(1)}%` : ''}
                        </div>
                    )}
                </div>
                <div className="ps-stack-preview-actions">
                    <button className="preview-button" onClick={() => onPreviewPsStack(stack)}>预览开</button>
                    <button className="preview-button preview-button-secondary" onClick={onClearPsStackPreview}>预览关</button>
                </div>
            </div>
            <VirtualizedList
                items={stack}
                itemHeight={PS_STACK_ROW_HEIGHT}
                getKey={(item) => item.id}
                viewportClassName="ps-stack-list-viewport"
                contentClassName="ps-stack-list-content"
                viewportStyle={{ height: viewportHeight }}
                renderItem={(item, index, key) => (
                    <PsStackItemRow key={key || `${item.id}-${index}`} item={item} />
                )}
            />
            <div className="ps-stack-actions">
                <button onClick={() => onCreatePsBatch(direction, stack)} disabled={isReadOnlyUser}>
                    保存批次
                </button>
                <button onClick={() => onSendToTimeseriesProduction(direction, stack)} disabled={isReadOnlyUser}>
                    送入生产
                </button>
            </div>
        </div>
    );
}

export default memo(PsStackSection);
