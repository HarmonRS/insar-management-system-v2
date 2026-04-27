import { memo } from 'react';
import { parseDatesFromName, formatYmd } from '../../utils/appUiHelpers';
import { getDinsarEngineMeta } from '../../utils/dinsarEngines';

function truncateMiddle(value, maxLength = 28) {
    const text = String(value || '').trim();
    if (!text || text.length <= maxLength) {
        return text || '-';
    }
    const sideLength = Math.max(6, Math.floor((maxLength - 3) / 2));
    return `${text.slice(0, sideLength)}...${text.slice(-sideLength)}`;
}

function DinsarResultRow({
    result,
    language,
    showDates,
    isLoading,
    isReadOnlyUser,
    onLabel,
    onAnalyze,
    onToggleVisibility,
}) {
    const dates = showDates ? parseDatesFromName(result.name, (value) => formatYmd(value, language)) : null;
    const engineMeta = getDinsarEngineMeta(result.engine_code);
    const hasTrace = !!(
        result.selection_strategy ||
        result.network_run_id ||
        result.network_edge_id ||
        result.pair_uid ||
        result.pair_key ||
        result.run_key
    );

    return (
        <li className="data-item dinsar-item">
            <div className="dinsar-row-header">
                <span className="data-item-name" title={result.name}>
                    {result.name}
                </span>
                <div className="dinsar-row-badges">
                    <span
                        className={`dinsar-engine-badge tone-${engineMeta.tone}`}
                        title={`${language === 'en' ? 'Engine' : '生产引擎'}: ${engineMeta.label}`}
                    >
                        {engineMeta.shortLabel}
                    </span>
                    {result.ai_score !== null && (
                        <span
                            className={`ai-score ${result.ai_score > 0.7 ? 'good' : (result.ai_score < 0.4 ? 'bad' : 'medium')}`}
                            title={language === 'en' ? 'AI quality score' : 'AI 质量评分'}
                        >
                            AI {Math.round(result.ai_score * 100)}
                        </span>
                    )}
                </div>
            </div>

            {hasTrace && (
                <div className="dinsar-trace-info">
                    <div className="dinsar-trace-line">
                        <span
                            className="dinsar-trace-pill"
                            title={language === 'en' ? 'Pairing selection strategy' : '配对选择策略'}
                        >
                            {result.selection_strategy || 'legacy'}
                        </span>
                        {result.run_key && (
                            <span
                                className="dinsar-trace-stat"
                                title={`${language === 'en' ? 'Run key' : '运行标识'}: ${result.run_key}`}
                            >
                                <strong>{language === 'en' ? 'run' : '运行'}</strong>
                                <span>{truncateMiddle(result.run_key, 24)}</span>
                            </span>
                        )}
                        {result.network_edge_id != null && (
                            <span
                                className="dinsar-trace-stat"
                                title={language === 'en' ? 'Network edge id' : '网络边编号'}
                            >
                                <strong>edge</strong>
                                <span>{result.network_edge_id}</span>
                            </span>
                        )}
                        {result.network_run_id && (
                            <span
                                className="dinsar-trace-stat"
                                title={`${language === 'en' ? 'Network run id' : '网络运行编号'}: ${result.network_run_id}`}
                            >
                                <strong>{language === 'en' ? 'network' : '网络'}</strong>
                                <span>{truncateMiddle(result.network_run_id, 22)}</span>
                            </span>
                        )}
                    </div>
                    <div className="dinsar-trace-line">
                        <span
                            className="dinsar-trace-stat"
                            title={result.pair_uid || result.pair_key || '-'}
                        >
                            <strong>{language === 'en' ? 'pair' : '配对'}</strong>
                            <span>{truncateMiddle(result.pair_uid || result.pair_key, 36)}</span>
                        </span>
                        {result.policy_version && (
                            <span className="dinsar-trace-stat">
                                <strong>{language === 'en' ? 'policy' : '策略版本'}</strong>
                                <span>{result.policy_version}</span>
                            </span>
                        )}
                    </div>
                </div>
            )}

            {dates && (
                <div className="date-info">
                    <span className="date-tag master" title={language === 'en' ? 'Master date' : '主影像日期'}>
                        {dates.master}
                    </span>
                    <span className="date-arrow">-&gt;</span>
                    <span className="date-tag slave" title={language === 'en' ? 'Slave date' : '从影像日期'}>
                        {dates.slave}
                    </span>
                </div>
            )}

            <div className="data-item-controls">
                <div className="label-buttons">
                    <button
                        className={`label-btn good ${result.user_label === 1 ? 'active' : ''}`}
                        onClick={() => onLabel(result.id, result.user_label === 1 ? null : 1)}
                        title={language === 'en' ? 'Mark as high quality' : '标记为高质量'}
                        disabled={isReadOnlyUser}
                    >
                        {language === 'en' ? 'Good' : '良好'}
                    </button>
                    <button
                        className={`label-btn bad ${result.user_label === 0 ? 'active' : ''}`}
                        onClick={() => onLabel(result.id, result.user_label === 0 ? null : 0)}
                        title={language === 'en' ? 'Mark as low quality' : '标记为低质量'}
                        disabled={isReadOnlyUser}
                    >
                        {language === 'en' ? 'Poor' : '较差'}
                    </button>
                </div>

                <div className="dinsar-control-cluster">
                    <button
                        className="ai-analyze-btn"
                        onClick={(event) => {
                            event.stopPropagation();
                            onAnalyze(result.id);
                        }}
                        disabled={isLoading || isReadOnlyUser}
                        title={language === 'en' ? 'Use AI to analyze this result' : '使用 AI 分析该结果'}
                    >
                        {language === 'en' ? 'AI Diagnose' : 'AI 诊断'}
                    </button>
                    <label className="dinsar-toggle-label" title={language === 'en' ? 'Show or hide on map' : '在地图上显示或隐藏'}>
                        <input
                            type="checkbox"
                            checked={!!result.isVisible}
                            onChange={(event) => {
                                event.stopPropagation();
                                onToggleVisibility(result.id);
                            }}
                        />
                        <span>{language === 'en' ? 'Map' : '地图'}</span>
                    </label>
                </div>
            </div>
        </li>
    );
}

export default memo(DinsarResultRow);
