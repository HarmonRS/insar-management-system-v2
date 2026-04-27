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
                    <div className="list-toolbar column-layout dinsar-results-toolbar">
                        {focusedHazardPoint && (
                            <div className="filter-banner">
                                <span>
                                    {language === 'en' ? (
                                        <>
                                            Viewing results covering <strong>{focusedHazardPoint.hazard_name}</strong>
                                        </>
                                    ) : (
                                        <>
                                            当前仅显示覆盖隐患点 <strong>{focusedHazardPoint.hazard_name}</strong> 的结果
                                        </>
                                    )}
                                </span>
                                <button className="clear-filter-btn" onClick={() => setFocusedHazardPoint(null)}>
                                    {language === 'en' ? 'Clear Filter' : '清除筛选'}
                                </button>
                            </div>
                        )}

                        <div className="dinsar-toolbar-grid">
                            <section className="dinsar-toolbar-panel">
                                <span className="dinsar-toolbar-kicker">
                                    {language === 'en' ? 'Current page' : '当前页'}
                                </span>
                                <strong className="dinsar-toolbar-value">
                                    {filteredResults.length} / {dinsarResults.length}
                                </strong>
                                <p className="dinsar-toolbar-note">
                                    {language === 'en'
                                        ? 'Results after local filtering on the current page'
                                        : '当前页本地筛选后的结果数量'}
                                </p>
                                <div className="dinsar-toolbar-chip-row">
                                    <span className="dinsar-toolbar-chip">
                                        {language === 'en' ? 'AI score' : 'AI 分数'}
                                        {' >= '}
                                        {scorePercent}
                                    </span>
                                    <span className="dinsar-toolbar-chip">
                                        {language === 'en' ? 'Dates' : '日期'}
                                        {showDates
                                            ? (language === 'en' ? ': visible' : '：已展开')
                                            : (language === 'en' ? ': hidden' : '：已收起')}
                                    </span>
                                </div>
                            </section>

                            <section className="dinsar-toolbar-panel">
                                <span className="dinsar-toolbar-kicker">
                                    {language === 'en' ? 'Engine focus' : '当前引擎'}
                                </span>
                                <strong className="dinsar-toolbar-value">
                                    {filteredEngineMeta
                                        ? filteredEngineMeta.shortLabel
                                        : (language === 'en' ? 'All' : '全部')}
                                </strong>
                                <p className="dinsar-toolbar-note">
                                    {filteredEngineMeta
                                        ? filteredEngineMeta.label
                                        : (language === 'en'
                                            ? 'Compare outputs from all registered engines'
                                            : '同时查看所有登记引擎的结果')}
                                </p>
                                <div className="dinsar-toolbar-chip-row">
                                    <span className="dinsar-toolbar-chip">
                                        {language === 'en' ? 'Strategy' : '策略'}:
                                        {' '}
                                        {strategyFilter === DINSAR_STRATEGY_ALL
                                            ? (language === 'en' ? 'All' : '全部')
                                            : strategyFilter}
                                    </span>
                                    <span className="dinsar-toolbar-chip">
                                        {language === 'en' ? 'Trace search' : '检索词'}:
                                        {' '}
                                        {traceSearch.trim() || (language === 'en' ? 'None' : '未设置')}
                                    </span>
                                </div>
                            </section>

                            <section className="dinsar-toolbar-panel">
                                <span className="dinsar-toolbar-kicker">
                                    {language === 'en' ? 'Page control' : '分页控制'}
                                </span>
                                <strong className="dinsar-toolbar-value">{pageSummaryText}</strong>
                                <p className="dinsar-toolbar-note">
                                    {language === 'en'
                                        ? 'Use page size and jump controls below for large catalogs'
                                        : '大规模结果集请结合页大小和跳页控制使用'}
                                </p>
                                <div className="dinsar-toolbar-actions">
                                    <button onClick={() => onSetAllVisibility(true)}>
                                        {language === 'en' ? 'Show All' : '全部显示'}
                                    </button>
                                    <button onClick={() => onSetAllVisibility(false)}>
                                        {language === 'en' ? 'Hide All' : '全部隐藏'}
                                    </button>
                                    <button onClick={() => setShowDates(!showDates)}>
                                        {showDates
                                            ? (language === 'en' ? 'Hide Dates' : '收起日期')
                                            : (language === 'en' ? 'Show Dates' : '显示日期')}
                                    </button>
                                    <button
                                        onClick={() => setShowExportModal(true)}
                                        disabled={isLoading || filteredResults.length === 0}
                                        title={language === 'en'
                                            ? 'Export visible results in the current filter scope'
                                            : '按当前筛选范围导出结果文件'}
                                    >
                                        {language === 'en' ? 'Export...' : '提取结果...'}
                                    </button>
                                </div>
                            </section>
                        </div>

                        <div className="dinsar-filter-layout">
                            <label className="dinsar-filter-field">
                                <span>{language === 'en' ? 'AI score floor' : 'AI 分数下限'}</span>
                                <div className="dinsar-score-filter">
                                    <input
                                        type="range"
                                        min="0"
                                        max="1"
                                        step="0.1"
                                        value={scoreFilter}
                                        onChange={onScoreFilterChange}
                                    />
                                    <strong>{scorePercent}</strong>
                                </div>
                            </label>

                            <label className="dinsar-filter-field">
                                <span>{language === 'en' ? 'Pairing strategy' : '配对策略'}</span>
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
                            </label>

                            <label className="dinsar-filter-field">
                                <span>{language === 'en' ? 'Production engine' : '生产引擎'}</span>
                                <select
                                    value={engineFilter}
                                    onChange={(event) => setEngineFilter(event.target.value)}
                                    disabled={isLoading}
                                >
                                    {engineFilterOptions.map((option) => (
                                        <option key={option.value} value={option.value}>
                                            {option.label}
                                        </option>
                                    ))}
                                </select>
                            </label>

                            <label className="dinsar-filter-field dinsar-filter-field-wide">
                                <span>{language === 'en' ? 'Trace search' : 'Trace 检索'}</span>
                                <input
                                    type="text"
                                    value={traceSearch}
                                    onChange={(event) => setTraceSearch(event.target.value)}
                                    placeholder={language === 'en'
                                        ? 'Search pair / run / policy / engine'
                                        : '搜索 pair / run / policy / engine'}
                                    disabled={isLoading}
                                />
                            </label>
                        </div>

                        <div className="dinsar-engine-filter-row">
                            <button
                                type="button"
                                className={engineFilter === DINSAR_ENGINE_ALL ? 'active' : ''}
                                onClick={() => setEngineFilter(DINSAR_ENGINE_ALL)}
                                disabled={isLoading}
                            >
                                <span>{language === 'en' ? 'All engines' : '全部引擎'}</span>
                                <strong>{dinsarResults.length}</strong>
                            </button>
                            {engineCounts.map((option) => (
                                <button
                                    key={option.code}
                                    type="button"
                                    className={engineFilter === option.code ? 'active' : ''}
                                    onClick={() => setEngineFilter(option.code)}
                                    disabled={isLoading}
                                >
                                    <span>{option.shortLabel}</span>
                                    <strong>{option.count}</strong>
                                </button>
                            ))}
                        </div>

                        <div className="dinsar-toolbar-footer">
                            <div className="dinsar-toolbar-footer-main">
                                <button
                                    type="button"
                                    onClick={() => onPageChange(-1)}
                                    disabled={isLoading || dinsarPagination.offset <= 0}
                                >
                                    {language === 'en' ? 'Previous' : '上一页'}
                                </button>
                                <button
                                    type="button"
                                    onClick={() => onPageChange(1)}
                                    disabled={isLoading || !dinsarPagination.hasMore}
                                >
                                    {language === 'en' ? 'Next' : '下一页'}
                                </button>

                                <label className="dinsar-pagination-field">
                                    <span>{language === 'en' ? 'Per page' : '每页条数'}</span>
                                    <select
                                        value={dinsarPagination.limit}
                                        onChange={onPageSizeChange}
                                        disabled={isLoading}
                                    >
                                        {PAGE_SIZE_OPTIONS.map((size) => (
                                            <option key={size} value={size}>{size}</option>
                                        ))}
                                    </select>
                                </label>

                                <label className="dinsar-pagination-field dinsar-pagination-field-jump">
                                    <span>{language === 'en' ? 'Jump to page' : '跳转页码'}</span>
                                    <div className="dinsar-page-jump-input">
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
                                            className={showDinsarPageInputError ? 'has-error' : ''}
                                        />
                                        <button type="button" onClick={onGoToPage} disabled={isLoading}>
                                            {language === 'en' ? 'Jump' : '跳转'}
                                        </button>
                                    </div>
                                </label>
                            </div>

                            <div className={`dinsar-toolbar-hint ${showDinsarPageInputError ? 'error' : ''}`}>
                                {showDinsarPageInputError
                                    ? dinsarPageInputValidationError
                                    : getPageHintText(dinsarTotalPages, language)}
                            </div>
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
                    results={filteredResults}
                    onClose={() => setShowExportModal(false)}
                />
            )}
        </div>
    );
}
