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
    onCreatePsBatch,
}) {
    const viewportHeight = useMemo(
        () => Math.min(Math.max(stack.length * PS_STACK_ROW_HEIGHT, PS_STACK_ROW_HEIGHT), PS_STACK_MAX_HEIGHT),
        [stack.length]
    );

    return (
        <div className="ps-stack">
            <div className="ps-stack-header">
                <h4>{direction} ({stack.length} scenes)</h4>
                <button className="preview-button" onClick={() => onPreviewPsStack(stack)}>预览</button>
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
            </div>
        </div>
    );
}

export default memo(PsStackSection);
