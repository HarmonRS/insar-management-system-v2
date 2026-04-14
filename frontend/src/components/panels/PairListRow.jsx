import { memo } from 'react';

function PairListRow({
    pair,
    index,
    onToggleSelected,
    onVisualizePair,
    onTogglePairVisibility,
}) {
    return (
        <li className="pair-item">
            <input
                type="checkbox"
                title="选择以导出"
                checked={pair.isSelected}
                onChange={() => onToggleSelected(index)}
                onClick={(event) => event.stopPropagation()}
            />
            <div className="pair-info" onClick={() => onVisualizePair(pair)}>
                <strong>{pair.task_name}</strong>
                <div className="pair-details">
                    <span>时基: {pair.time_baseline_days}d</span>
                    <span>空基: {pair.spatial_baseline_meters.toFixed(2)}m</span>
                </div>
            </div>
            <button
                className={`visibility-toggle ${pair.isVis ? 'visible' : ''}`}
                onClick={(event) => {
                    event.stopPropagation();
                    onTogglePairVisibility(index);
                }}
                title={pair.isVis ? '在地图上隐藏' : '在地图上显示'}
            >
                {pair.isVis ? '隐藏' : '显示'}
            </button>
        </li>
    );
}

export default memo(PairListRow);
