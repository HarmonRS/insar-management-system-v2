import { memo } from 'react';

function basename(path) {
    const text = String(path || '').trim();
    if (!text) return '';
    const normalized = text.replace(/\\/g, '/');
    return normalized.split('/').filter(Boolean).pop() || text;
}

function formatScene(scene) {
    if (!scene) return '未识别影像';
    const name = basename(scene.file_path) || scene.source_product_token || scene.product_unique_id || `#${scene.id || '-'}`;
    const meta = [
        scene.satellite_family || scene.satellite,
        scene.imaging_date,
        scene.imaging_mode,
        scene.polarization,
    ].filter(Boolean).join(' / ');
    return meta ? `${name} (${meta})` : name;
}

function formatPercent(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '-';
    return `${(number * 100).toFixed(number >= 0.995 ? 0 : 1)}%`;
}

function formatDistance(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '-';
    if (Math.abs(number) >= 1000) return `${(number / 1000).toFixed(2)}km`;
    return `${number.toFixed(1)}m`;
}

function qualityLabel(pair) {
    const tier = String(pair?.dinsar_quality_tier || '').trim().toUpperCase();
    const readiness = String(pair?.dinsar_readiness || '').trim().toUpperCase();
    if (!tier && !readiness) return null;
    const tierLabel = { A: 'A级', B: 'B级', C: 'C级', REJECT: '不推荐' }[tier] || tier;
    const readinessLabel = {
        RECOMMENDED: '推荐',
        CANDIDATE: '候选',
        NOT_RECOMMENDED: '不推荐',
    }[readiness] || readiness;
    return readinessLabel ? `${tierLabel} ${readinessLabel}` : tierLabel;
}

function productionLabel(summary) {
    if (!summary) {
        return { text: '生产状态未知', tone: 'unknown' };
    }
    const aliasOnly = summary.match_level === 'task_alias';
    if (summary.is_produced) {
        const readyCount = Number(summary.ready_product_count || 0);
        return {
            text: aliasOnly
                ? '别名记录 已生产'
                : (readyCount > 0 ? `已生产 ${readyCount} 个结果` : '已生产'),
            tone: aliasOnly ? 'running' : 'ready',
        };
    }
    if (summary.has_record) {
        const status = String(summary.status || summary.latest_item_status || summary.latest_run_status || 'RUNNING').toUpperCase();
        const failed = Number(summary.failed_run_count || 0) > 0 || ['FAILED', 'ERROR', 'CANCELLED', 'CANCELED'].includes(status);
        return {
            text: aliasOnly ? `别名记录 ${status}` : `有记录 ${status}`,
            tone: failed ? 'failed' : 'running',
        };
    }
    return { text: '未生产', tone: 'missing' };
}

function PairListRow({
    pair,
    index,
    onToggleSelected,
    onVisualizePair,
    onTogglePairVisibility,
}) {
    const centerDistance = pair.scene_center_distance_meters ?? pair.spatial_baseline_meters;
    const overlap = pair.pair_aoi_overlap_ratio ?? pair.scene_overlap_ratio ?? pair.overlap_ratio;
    const overlapLabel = pair.pair_aoi_overlap_ratio != null ? 'AOI覆盖' : '影像重叠';
    const production = productionLabel(pair.production_summary);
    const quality = qualityLabel(pair);
    const engines = Array.isArray(pair.production_summary?.engine_codes)
        ? pair.production_summary.engine_codes.filter(Boolean).join('/')
        : '';
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
                <div className="pair-scenes">
                    <span title={pair.master?.file_path}>主: {formatScene(pair.master)}</span>
                    <span title={pair.slave?.file_path}>辅: {formatScene(pair.slave)}</span>
                </div>
                <div className="pair-details">
                    <span>时基: {pair.time_baseline_days}d</span>
                    <span>中心距: {formatDistance(centerDistance)}</span>
                    <span>{overlapLabel}: {formatPercent(overlap)}</span>
                    {quality && <span>D-InSAR: {quality}</span>}
                </div>
                <div className="pair-status-line">
                    <span className={`pair-production-badge ${production.tone}`}>{production.text}</span>
                    {engines && <span className="pair-engine-text">引擎: {engines}</span>}
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
