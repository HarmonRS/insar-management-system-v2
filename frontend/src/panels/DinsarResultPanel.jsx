import { useEffect, useMemo, useState } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { useDinsarStore, useHazardStore, useUiStore, useAuthStore } from '../store';
import { useI18n } from '../i18n/I18nContext';
import VirtualizedList from '../components/common/VirtualizedList';
import DinsarResultRow from '../components/panels/DinsarResultRow';
import ResultExportModal from '../components/ResultExportModal';
import { PAGE_SIZE_OPTIONS } from '../config/appConstants';
import { getPageHintText } from '../utils/appUiHelpers';
import {
    DINSAR_ENGINE_ALL,
    buildDinsarEngineOptions,
    getDinsarEngineMeta,
} from '../utils/dinsarEngines';
import {
    DINSAR_STRATEGY_ALL,
    buildDinsarStrategyOptions,
    filterDinsarResults,
} from '../utils/dinsarResultFilters';

const DINSAR_ROW_HEIGHT = {
    compact: 136,
    expanded: 166,
};

export default function DinsarResultPanel({
    dinsarCurrentPage,
    dinsarTotalPages,
    showDinsarPageInputError,
    dinsarPageInputValidationError,
    onSetAllVisibility,
    onScoreFilterChange,
    onPageChange,
    onPageSizeChange,
    onGoToPage,
    onToggleVisibility,
    onLabel,
    onAnalyze,
}) {
    const { language } = useI18n();
    const {
        dinsarResults,
        dinsarPagination,
        scoreFilter,
        engineFilter,
        traceSearch,
        strategyFilter,
        dinsarPageInput,
        setEngineFilter,
        setTraceSearch,
        setStrategyFilter,
        setDinsarPageInput,
        setDinsarPageInputTouched,
    } = useDinsarStore(useShallow((state) => ({
        dinsarResults: state.dinsarResults,
        dinsarPagination: state.dinsarPagination,
        scoreFilter: state.scoreFilter,
        engineFilter: state.engineFilter,
        traceSearch: state.traceSearch,
        strategyFilter: state.strategyFilter,
        dinsarPageInput: state.dinsarPageInput,
        setEngineFilter: state.setEngineFilter,
        setTraceSearch: state.setTraceSearch,
        setStrategyFilter: state.setStrategyFilter,
        setDinsarPageInput: state.setDinsarPageInput,
        setDinsarPageInputTouched: state.setDinsarPageInputTouched,
    })));
    const { focusedHazardPoint, setFocusedHazardPoint } = useHazardStore(useShallow((state) => ({
        focusedHazardPoint: state.focusedHazardPoint,
        setFocusedHazardPoint: state.setFocusedHazardPoint,
    })));
    const { isLoading, showDates, setShowDates } = useUiStore(useShallow((state) => ({
        isLoading: state.isLoading,
        showDates: state.showDates,
        setShowDates: state.setShowDates,
    })));
    const { currentUser } = useAuthStore();
    const isReadOnlyUser = !!currentUser && currentUser.role !== 'admin';
    const [showExportModal, setShowExportModal] = useState(false);

    const strategyOptions = useMemo(
        () => buildDinsarStrategyOptions(dinsarResults),
        [dinsarResults]
    );
    const engineOptions = useMemo(
        () => buildDinsarEngineOptions(dinsarResults),
        [dinsarResults]
    );
    const engineFilterOptions = useMemo(
        () => [
            {
                value: DINSAR_ENGINE_ALL,
                label: language === 'en' ? 'All engines' : '全部引擎',
            },
            ...engineOptions.map((option) => ({
                value: option.value,
                label: option.label,
            })),
        ],
        [engineOptions, language]
    );
    const filteredEngineMeta = useMemo(
        () => (engineFilter === DINSAR_ENGINE_ALL ? null : getDinsarEngineMeta(engineFilter)),
        [engineFilter]
    );
    const engineCounts = useMemo(() => {
        const counts = new Map();
        dinsarResults.forEach((result) => {
            const meta = getDinsarEngineMeta(result?.engine_code);
            counts.set(meta.code, (counts.get(meta.code) || 0) + 1);
        });
        return engineOptions.map((option) => ({
            ...option,
            count: counts.get(option.code) || 0,
        }));
    }, [dinsarResults, engineOptions]);

    useEffect(() => {
        if (strategyFilter === DINSAR_STRATEGY_ALL) {
            return;
        }
        if (!strategyOptions.includes(strategyFilter)) {
            setStrategyFilter(DINSAR_STRATEGY_ALL);
        }
    }, [setStrategyFilter, strategyFilter, strategyOptions]);

    useEffect(() => {
        if (engineFilter === DINSAR_ENGINE_ALL) {
            return;
        }
        if (!engineOptions.some((option) => option.value === engineFilter)) {
            setEngineFilter(DINSAR_ENGINE_ALL);
        }
    }, [engineFilter, engineOptions, setEngineFilter]);

    const filteredResults = useMemo(
        () => filterDinsarResults(dinsarResults, {
            scoreFilter,
            engineFilter,
            strategyFilter,
            traceSearch,
            focusedHazardPoint,
        }),
        [dinsarResults, engineFilter, focusedHazardPoint, scoreFilter, strategyFilter, traceSearch]
    );

    const scorePercent = Math.round(Number(scoreFilter || 0) * 100);
    const virtualRowHeight = showDates ? DINSAR_ROW_HEIGHT.expanded : DINSAR_ROW_HEIGHT.compact;
    const pageSummaryText = language === 'en'
        ? `Page ${dinsarCurrentPage}/${dinsarTotalPages} · ${dinsarPagination.total} total`
        : `第 ${dinsarCurrentPage}/${dinsarTotalPages} 页 · 共 ${dinsarPagination.total} 条`;

    return (
        <div className="panel-content">
            {dinsarPagination.total === 0 ? (
                <p className="empty-state">
                    {language === 'en' ? 'No D-InSAR results found.' : '未找到 D-InSAR 结果。'}
                </p>
            ) : (
                <>
                    <div className="list-toolbar column-layout">
                        {focusedHazardPoint && (
                            <div className="filter-banner">
                                <span>
                                    {language === 'en' ? (
                                        <>
                                            Viewing results covering <strong>{focusedHazardPoint.hazard_name}</strong>
                                        </>
                                    ) : (
                                        <>
                                            正在查看覆盖点 <strong>{focusedHazardPoint.hazard_name}</strong> 的结果
                                        </>
                                    )}
                                </span>
                                <button className="clear-filter-btn" onClick={() => setFocusedHazardPoint(null)}>
                                    {language === 'en' ? 'Clear Filter' : '清除筛选'}
                                </button>
                            </div>
                        )}

                        <div className="toolbar-row">
                            <button onClick={() => onSetAllVisibility(true)}>
                                {language === 'en' ? 'Show All' : '显示全部'}
                            </button>
                            <button onClick={() => onSetAllVisibility(false)}>
                                {language === 'en' ? 'Hide All' : '隐藏全部'}
                            </button>
                            <button onClick={() => setShowDates(!showDates)}>
                                {showDates
                                    ? (language === 'en' ? 'Hide Dates' : '隐藏日期')
                                    : (language === 'en' ? 'Show Dates' : '显示日期')}
                            </button>
                            <button
                                onClick={() => setShowExportModal(true)}
                                disabled={isLoading || dinsarResults.length === 0}
                                title={language === 'en' ? 'Export selected results to a directory' : '将结果文件提取到指定目录'}
                            >
                                {language === 'en' ? 'Export...' : '导出...'}
                            </button>
                        </div>

                        <div className="toolbar-row filter-row">
                            <label title={language === 'en' ? 'Filter low-quality results below this score' : '过滤低于当前分数的结果'}>
                                {language === 'en' ? 'AI Score Filter' : 'AI 评分过滤'}: {(scoreFilter * 100).toFixed(0)}
                            </label>
                            <input
                                type="range"
                                min="0"
                                max="1"
                                step="0.1"
                                value={scoreFilter}
                                onChange={onScoreFilterChange}
                            />
                        </div>

                        <div className="toolbar-row filter-row">
                            <label>{language === 'en' ? 'Pairing strategy' : '配对策略'}:</label>
                            <select
                                value={strategyFilter}
                                onChange={(event) => setStrategyFilter(event.target.value)}
                                disabled={isLoading}
                            >
                                <option value={DINSAR_STRATEGY_ALL}>
                                    {language === 'en' ? 'All strategies' : '全部策略'}
                                </option>
                                {strategyOptions
                                    .filter((value) => value !== DINSAR_STRATEGY_ALL)
                                    .map((value) => (
                                        <option key={value} value={value}>{value}</option>
                                    ))}
                            </select>
                            <input
                                type="text"
                                value={traceSearch}
                                onChange={(event) => setTraceSearch(event.target.value)}
                                placeholder={language === 'en' ? 'Search pair/run trace' : '搜索 pair/run trace'}
                                disabled={isLoading}
                                style={{ minWidth: '180px', flex: 1 }}
                            />
                        </div>

                        <div className="toolbar-row">
                            <span style={{ fontSize: '12px', color: '#4a5568' }}>
                                {language === 'en'
                                    ? `Filtered ${filteredResults.length} / ${dinsarResults.length} results`
                                    : `当前筛出 ${filteredResults.length} / ${dinsarResults.length} 条结果`}
                            </span>
                        </div>

                        <div className="toolbar-row">
                            <button type="button" onClick={() => onPageChange(-1)} disabled={isLoading || dinsarPagination.offset <= 0}>
                                {language === 'en' ? 'Previous' : '上一页'}
                            </button>
                            <span style={{ fontSize: '12px', color: '#4a5568' }}>
                                {language === 'en'
                                    ? `Page ${dinsarCurrentPage}/${dinsarTotalPages} (Total ${dinsarPagination.total} items)`
                                    : `第 ${dinsarCurrentPage}/${dinsarTotalPages} 页（共 ${dinsarPagination.total} 条）`}
                            </span>
                            <button type="button" onClick={() => onPageChange(1)} disabled={isLoading || !dinsarPagination.hasMore}>
                                {language === 'en' ? 'Next' : '下一页'}
                            </button>
                        </div>

                        <div className="toolbar-row">
                            <label style={{ fontSize: '12px', color: '#4a5568' }}>
                                {language === 'en' ? 'Per page' : '每页'}
                                <select
                                    value={dinsarPagination.limit}
                                    onChange={onPageSizeChange}
                                    disabled={isLoading}
                                    style={{ marginLeft: '6px', marginRight: '6px' }}
                                >
                                    {PAGE_SIZE_OPTIONS.map((size) => (
                                        <option key={size} value={size}>{size}</option>
                                    ))}
                                </select>
                                {language === 'en' ? 'items' : '条'}
                            </label>
                            <label style={{ fontSize: '12px', color: '#4a5568' }}>
                                {language === 'en' ? 'Go to' : '跳到'}
                                <input
                                    type="number"
                                    min={1}
                                    max={dinsarTotalPages}
                                    value={dinsarPageInput}
                                    onChange={(event) => {
                                        setDinsarPageInput(event.target.value);
                                        setDinsarPageInputTouched(false);
                                    }}
                                    onBlur={() => setDinsarPageInputTouched(true)}
                                    onKeyDown={(event) => {
                                        if (event.key === 'Enter') {
                                            event.preventDefault();
                                            onGoToPage();
                                        }
                                    }}
                                    disabled={isLoading}
                                    style={{
                                        width: '70px',
                                        marginLeft: '6px',
                                        marginRight: '6px',
                                        borderColor: showDinsarPageInputError ? '#e53e3e' : undefined,
                                        boxShadow: showDinsarPageInputError ? '0 0 0 1px rgba(229,62,62,0.25)' : undefined,
                                    }}
                                />
                                {language === 'en' ? 'page' : '页'}
                            </label>
                            <button type="button" onClick={onGoToPage} disabled={isLoading}>
                                {language === 'en' ? 'Jump' : '跳转'}
                            </button>
                        </div>

                        <div className="toolbar-row">
                            <span style={{ fontSize: '12px', color: showDinsarPageInputError ? '#e53e3e' : '#718096' }}>
                                {showDinsarPageInputError
                                    ? dinsarPageInputValidationError
                                    : getPageHintText(dinsarTotalPages, language)}
                            </span>
                        </div>
                    </div>

                    <div className="panel-scroll-shell">
                        <VirtualizedList
                            items={filteredResults}
                            itemHeight={virtualRowHeight}
                            getKey={(result) => result.id}
                            renderItem={(result, index, key) => (
                                <DinsarResultRow
                                    key={key || `${result.id}-${index}`}
                                    result={result}
                                    language={language}
                                    showDates={showDates}
                                    isLoading={isLoading}
                                    isReadOnlyUser={isReadOnlyUser}
                                    onLabel={onLabel}
                                    onAnalyze={onAnalyze}
                                    onToggleVisibility={onToggleVisibility}
                                />
                            )}
                        />
                    </div>
                </>
            )}

            {showExportModal && (
                <ResultExportModal
                    results={dinsarResults}
                    onClose={() => setShowExportModal(false)}
                />
            )}
        </div>
    );
}
