import { memo } from 'react';
import { parseDatesFromName, formatYmd } from '../../utils/appUiHelpers';

function truncateMiddle(value, maxLength = 26) {
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
    const hasTrace = !!(result.selection_strategy || result.network_run_id || result.network_edge_id || result.pair_uid);

    return (
        <li className="data-item dinsar-item">
            <div className="dinsar-info">
                <span className="data-item-name" title={result.name}>
                    {result.name}
                </span>
                {result.ai_score !== null && (
                    <span
                        className={`ai-score ${result.ai_score > 0.7 ? 'good' : (result.ai_score < 0.4 ? 'bad' : 'medium')}`}
                        title={language === 'en' ? 'AI quality score' : 'AI 质量评分'}
                    >
                        AI: {(result.ai_score * 100).toFixed(0)}
                    </span>
                )}
            </div>

            {hasTrace && (
                <div className="dinsar-trace-info">
                    <div className="dinsar-trace-line">
                        <span className="dinsar-trace-pill" title={language === 'en' ? 'Pairing selection strategy' : '配对选择策略'}>
                            {result.selection_strategy || 'legacy'}
                        </span>
                        <span title={language === 'en' ? 'Network edge id' : '网络边编号'}>
                            edge {result.network_edge_id ?? '-'}
                        </span>
                        <span title={language === 'en' ? 'Network run id' : '网络运行编号'}>
                            {truncateMiddle(result.network_run_id, 24)}
                        </span>
                    </div>
                    <div className="dinsar-trace-line" title={result.pair_uid || result.pair_key || '-'}>
                        <span>{truncateMiddle(result.pair_uid || result.pair_key, 30)}</span>
                        {result.policy_version && <span>{result.policy_version}</span>}
                    </div>
                </div>
            )}

            {dates && (
                <div className="date-info">
                    <span className="date-tag master" title={language === 'en' ? 'Master date' : '主影像日期'}>{dates.master}</span>
                    <span className="date-arrow">→</span>
                    <span className="date-tag slave" title={language === 'en' ? 'Slave date' : '辅影像日期'}>{dates.slave}</span>
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
                        {language === 'en' ? 'Poor' : '欠佳'}
                    </button>
                </div>
                <button
                    className="ai-analyze-btn"
                    onClick={(event) => {
                        event.stopPropagation();
                        onAnalyze(result.id);
                    }}
                    disabled={isLoading || isReadOnlyUser}
                    title={language === 'en' ? 'Use AI to analyze this result' : '使用 AI 自动分析此结果'}
                >
                    {language === 'en' ? 'AI Diagnose' : 'AI 诊断'}
                </button>
                <input
                    type="checkbox"
                    checked={!!result.isVisible}
                    onChange={(event) => {
                        event.stopPropagation();
                        onToggleVisibility(result.id);
                    }}
                    title={language === 'en' ? 'Show or hide on map' : '在地图上显示/隐藏'}
                />
            </div>
        </li>
    );
}

export default memo(DinsarResultRow);
