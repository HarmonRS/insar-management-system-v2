import { Suspense, lazy } from 'react';
import apiClient from '../../api/client';
import RadarDataPanel from '../../panels/RadarDataPanel';
import {
    ADMIN_ONLY_TABS,
    LEFT_GROUP_LABELS,
    LEFT_GROUP_SECTIONS,
    LEFT_GROUP_TABS,
    LEFT_TAB_GROUP,
    LEFT_TAB_SECTION,
    PRODUCTION_WORKSPACE_ROUTE_TABS,
} from '../../config/appConstants';
import { getLeftTabLabel } from '../../utils/appUiHelpers';
import { PanelLoadingBody, PanelLoadingPanel } from './AppLoadingFallbacks';

const LazyDataMonitorPanel = lazy(() => import('../../DataMonitorPanel'));
const LazyDataCopierPanel = lazy(() => import('../../DataCopierPanel'));
const LazyIDLAutomationPanel = lazy(() => import('../../IDLAutomationPanel'));
const LazyHazardPointPanel = lazy(() => import('../../HazardPointPanel'));
const LazyHealthCheckPanel = lazy(() => import('../../HealthCheckPanel'));
const LazyWaterMonitorPanel = lazy(() => import('../../WaterMonitorPanel'));
const LazyUserAdminPanel = lazy(() => import('../../UserAdminPanel'));
const LazyAuditLogPanel = lazy(() => import('../../AuditLogPanel'));
const LazyAiQualityPanel = lazy(() => import('../../panels/AiQualityPanel'));
const LazyAiAnalysisPanel = lazy(() => import('../../AiAnalysisPanel'));
const LazyPairingPanel = lazy(() => import('../../panels/PairPlanningPanel'));
const LazyDinsarResultPanel = lazy(() => import('../../panels/DinsarResultPanel'));
const LazyBatchPanel = lazy(() => import('../../panels/BatchPanel'));
const LazyPairsListPanel = lazy(() => import('../../panels/PairsListPanel'));
const LazyPsResultsPanel = lazy(() => import('../../panels/PsResultsPanel'));
const LazyPsinsarCatalogPanel = lazy(() => import('../PsinsarCatalogPanel'));
const LazyProductionWorkspace = lazy(() => import('../../ProductionWorkspace'));

export default function AppSidePanel({
    leftPanelWidth,
    leftPanelTab,
    setLeftPanelTab,
    isStandalone,
    isAdmin,
    isReadOnlyUser,
    currentUser,
    language,
    apiEndpoint,
    licenseOk,
    foundPairs,
    psResults,
    dinsarTotal,
    selectedPairsCount,
    hasEnoughRadarScenesForPlanning,
    isLoading,
    hasRadarSearched,
    showHazardPoints,
    hazardPoints,
    aiStatus,
    radarPanel,
    pairingPanel,
    taskPanel,
    hazardPanel,
    waterPanel,
    dinsarPanel,
    aiPanel,
    pairsPanel,
    psPanel,
}) {
    const isProductionWorkspace = PRODUCTION_WORKSPACE_ROUTE_TABS.has(leftPanelTab);
    const activeLeftGroup = LEFT_TAB_GROUP[leftPanelTab] || 'data';
    const leftTabLabelContext = {
        pairCount: foundPairs.length,
        psResultCount: psResults ? Object.keys(psResults).length : 0,
        dinsarTotal,
    };
    const getVisibleTabs = (tabs = []) => tabs.filter((tab) => isAdmin || !ADMIN_ONLY_TABS.has(tab));
    const getVisibleSections = (groupKey) => (
        (LEFT_GROUP_SECTIONS[groupKey] || [])
            .map((section) => ({
                ...section,
                tabs: getVisibleTabs(section.tabs || []),
            }))
            .filter((section) => section.tabs.length > 0)
    );
    const getDefaultGroupTab = (groupKey) => {
        const visibleSections = getVisibleSections(groupKey);
        if (visibleSections.length > 0) {
            return visibleSections[0]?.tabs?.[0] || '';
        }
        return getVisibleTabs(LEFT_GROUP_TABS[groupKey] || [])[0] || '';
    };
    const mainWorkspaceTab = getDefaultGroupTab('data') || 'data';
    const activeGroupSections = getVisibleSections(activeLeftGroup);
    const hasSectionNav = activeGroupSections.length > 0;
    const preferredActiveSection = LEFT_TAB_SECTION[leftPanelTab];
    const activeLeftSection = hasSectionNav && activeGroupSections.some((section) => section.key === preferredActiveSection)
        ? preferredActiveSection
        : (activeGroupSections[0]?.key || null);
    const activeLeafTabs = hasSectionNav
        ? (activeGroupSections.find((section) => section.key === activeLeftSection)?.tabs || [])
        : getVisibleTabs(LEFT_GROUP_TABS[activeLeftGroup] || []);
    const standaloneSectionTabs = isProductionWorkspace
        ? []
        : (
            hasSectionNav
                ? (activeGroupSections.find((section) => section.key === activeLeftSection)?.tabs || activeLeafTabs)
                : activeLeafTabs
        );
    const standaloneEyebrow = [LEFT_GROUP_LABELS[activeLeftGroup], activeGroupSections.find((section) => section.key === activeLeftSection)?.label]
        .filter(Boolean)
        .join(' / ');
    const standaloneTitle = isProductionWorkspace
        ? '生产管理'
        : getLeftTabLabel(leftPanelTab, leftTabLabelContext);
    const standaloneDescription = isProductionWorkspace
        ? '这里统一承载 D-InSAR 与时序InSAR的运行和产物页面，当前时序流程默认接入 SBAS，后续可继续扩展 PS-InSAR / SBAS-InSAR。'
        : '当前模块已切换为独立工作区模式。';

    return (
        <aside
            className={`panel data-panel${isStandalone ? ' panel--standalone' : ''}`}
            style={{ display: 'flex', flexDirection: 'column', width: leftPanelWidth }}
        >
            {isStandalone ? (
                <div className="panel-standalone-header">
                    <div className="panel-standalone-header-main">
                        <span className="panel-standalone-eyebrow">{standaloneEyebrow}</span>
                        <strong>{standaloneTitle}</strong>
                        <p>{standaloneDescription}</p>
                    </div>
                    <div className="panel-standalone-actions">
                        <button
                            type="button"
                            className="panel-standalone-return"
                            onClick={() => setLeftPanelTab(mainWorkspaceTab)}
                        >
                            返回主界面
                        </button>
                        {standaloneSectionTabs.length > 1 && (
                            <>
                                {standaloneSectionTabs.map((tabKey) => (
                                    <button
                                        key={tabKey}
                                        className={leftPanelTab === tabKey ? 'active-tab' : ''}
                                        onClick={() => setLeftPanelTab(tabKey)}
                                    >
                                        {getLeftTabLabel(tabKey, leftTabLabelContext)}
                                    </button>
                                ))}
                            </>
                        )}
                    </div>
                </div>
            ) : (
                <div className="panel-tabs">
                    <div className="tabs-header group-tabs">
                        {Object.entries(LEFT_GROUP_LABELS)
                            .filter(([groupKey]) => {
                                if (isAdmin) return true;
                                return !!getDefaultGroupTab(groupKey);
                            })
                            .map(([groupKey, label]) => (
                                <button
                                    key={groupKey}
                                    className={activeLeftGroup === groupKey ? 'active-tab' : ''}
                                    onClick={() => {
                                        const nextTab = getDefaultGroupTab(groupKey);
                                        if (nextTab) setLeftPanelTab(nextTab);
                                    }}
                                >
                                    {label}
                                </button>
                            ))}
                    </div>
                    {hasSectionNav && (
                        <div className="tabs-header section-tabs">
                            {activeGroupSections.map((section) => (
                                <button
                                    key={section.key}
                                    className={activeLeftSection === section.key ? 'active-tab' : ''}
                                    onClick={() => setLeftPanelTab(section.tabs[0])}
                                >
                                    {section.label}
                                </button>
                            ))}
                        </div>
                    )}
                    <div className="tabs-header left-tabs sub-tabs">
                        {activeLeafTabs.map((tabKey) => (
                            <button
                                key={tabKey}
                                className={leftPanelTab === tabKey ? 'active-tab' : ''}
                                onClick={() => setLeftPanelTab(tabKey)}
                            >
                                {getLeftTabLabel(tabKey, leftTabLabelContext)}
                            </button>
                        ))}
                    </div>
                </div>
            )}

            {leftPanelTab === 'data' && (
                <RadarDataPanel
                    radarCurrentPage={radarPanel.radarCurrentPage}
                    radarTotalPages={radarPanel.radarTotalPages}
                    showRadarPageInputError={radarPanel.showRadarPageInputError}
                    radarPageInputValidationError={radarPanel.radarPageInputValidationError}
                    onSearchAll={radarPanel.onSearchAll}
                    onShowStats={radarPanel.onShowStats}
                    onSearch={radarPanel.onSearch}
                    onReset={radarPanel.onReset}
                    onAoiModeChange={radarPanel.onAoiModeChange}
                    onProvinceChange={radarPanel.onProvinceChange}
                    onCityChange={radarPanel.onCityChange}
                    onSetRadarSearchFiles={radarPanel.onSetRadarSearchFiles}
                    updateDraft={radarPanel.updateDraft}
                    onPageChange={radarPanel.onPageChange}
                    onPageSizeChange={radarPanel.onPageSizeChange}
                    onGoToPage={radarPanel.onGoToPage}
                    onSelectAllVisibility={radarPanel.onSelectAllVisibility}
                    onSetAllPreviewVisibility={radarPanel.onSetAllPreviewVisibility}
                    onToggleLayer={radarPanel.onToggleLayer}
                    onTogglePreview={radarPanel.onTogglePreview}
                    onRebuildPreview={radarPanel.onRebuildPreview}
                    onShowDataInfo={radarPanel.onShowDataInfo}
                    onFlyTo={radarPanel.onFlyTo}
                    onChangeSatelliteGroup={radarPanel.onChangeSatelliteGroup}
                />
            )}

            {leftPanelTab === 'ingest' && (
                <div className="panel-content" style={{ flex: '1 1 auto', padding: 0, overflow: 'auto' }}>
                    <Suspense fallback={<PanelLoadingBody message="正在加载数据接入面板..." />}>
                        <LazyDataMonitorPanel
                            apiEndpoint={apiEndpoint}
                            onTaskStart={taskPanel.onTaskStart}
                            readOnly={isReadOnlyUser}
                            enabled={!!currentUser && licenseOk}
                        />
                    </Suspense>
                </div>
            )}

            {leftPanelTab === 'pairing' && (
                <Suspense fallback={<PanelLoadingPanel message="正在加载组网规划面板..." />}>
                    <LazyPairingPanel
                        foundPairs={foundPairs}
                        selectedPairsCount={selectedPairsCount}
                        isLoading={isLoading}
                        isReadOnlyUser={isReadOnlyUser}
                        hasEnoughRadarScenesForPlanning={hasEnoughRadarScenesForPlanning}
                        onOpenPairingModal={pairingPanel.onOpenPairingModal}
                        onOpenPsModal={pairingPanel.onOpenPsModal}
                        hasRadarSearched={hasRadarSearched}
                        onRefreshRadarSearch={pairingPanel.onRefreshRadarSearch}
                        onSearchAll={radarPanel.onSearchAll}
                        onRefreshDinsar={pairingPanel.onRefreshDinsar}
                        language={language}
                    />
                </Suspense>
            )}

            {leftPanelTab === 'copier' && (
                <div className="panel-content" style={{ flex: '1 1 auto', padding: 0, overflow: 'auto' }}>
                    <Suspense fallback={<PanelLoadingBody message="正在加载数据分发面板..." />}>
                        <LazyDataCopierPanel
                            apiEndpoint={apiEndpoint}
                            readOnly={isReadOnlyUser}
                            onJobQueued={(taskId) => taskPanel.onTaskStart(taskId, '数据分发任务已入队，正在处理...')}
                        />
                    </Suspense>
                </div>
            )}

            {leftPanelTab === 'idl' && (
                <div className="panel-content" style={{ flex: '1 1 auto', padding: 0, overflow: 'auto' }}>
                    <Suspense fallback={<PanelLoadingBody message="正在加载 IDL 面板..." />}>
                        <LazyIDLAutomationPanel
                            apiEndpoint={apiEndpoint}
                            readOnly={isReadOnlyUser}
                            onJobQueued={(taskId) => taskPanel.onTaskStart(taskId, '任务已入队，等待处理...')}
                        />
                    </Suspense>
                </div>
            )}

            {isProductionWorkspace && (
                <div className="panel-content" style={{ flex: '1 1 auto', padding: 0, overflow: 'auto' }}>
                    <Suspense fallback={<PanelLoadingBody message="正在加载生产管理工作台..." />}>
                        <div style={{ minHeight: '100%' }}>
                            <LazyProductionWorkspace
                                activeEntry={leftPanelTab}
                                readOnly={isReadOnlyUser}
                                onTaskStart={taskPanel.onTaskStart}
                            />
                        </div>
                    </Suspense>
                </div>
            )}

            {leftPanelTab === 'hazard' && (
                <div className="panel-content" style={{ flex: '1 1 auto', padding: 0, overflow: 'auto' }}>
                    <Suspense fallback={<PanelLoadingBody message="正在加载隐患点面板..." />}>
                        <LazyHazardPointPanel
                            apiEndpoint={apiEndpoint}
                            onPointClick={hazardPanel.onPointClick}
                            isVisible={showHazardPoints}
                            onToggleVisibility={hazardPanel.onToggleVisibility}
                            onScanComplete={hazardPanel.onScanComplete}
                            points={hazardPoints}
                            onTaskStart={taskPanel.onTaskStart}
                            readOnly={isReadOnlyUser}
                        />
                    </Suspense>
                </div>
            )}

            {leftPanelTab === 'water' && (
                <div className="panel-content" style={{ flex: '1 1 auto', padding: 0, overflow: 'auto' }}>
                    <Suspense fallback={<PanelLoadingBody message="正在加载水体监测面板..." />}>
                        <LazyWaterMonitorPanel
                            readOnly={isReadOnlyUser}
                            onShowOnMap={waterPanel.onShowOnMap}
                            onShowFloodOnMap={waterPanel.onShowFloodOnMap}
                            onToggleFloodLayer={waterPanel.onToggleFloodLayer}
                            onTaskStart={taskPanel.onTaskStart}
                            language={language}
                        />
                    </Suspense>
                </div>
            )}

            {leftPanelTab === 'health' && (
                <div className="panel-content" style={{ flex: '1 1 auto', padding: 0, overflow: 'auto' }}>
                    <Suspense fallback={<PanelLoadingBody message="正在加载运维自检面板..." />}>
                        <LazyHealthCheckPanel
                            apiEndpoint={apiEndpoint}
                            language={language}
                            currentUser={currentUser}
                        />
                    </Suspense>
                </div>
            )}

            {leftPanelTab === 'users' && (
                <div className="panel-content" style={{ flex: '1 1 auto', padding: 0, overflow: 'auto' }}>
                    {isAdmin ? (
                        <Suspense fallback={<PanelLoadingBody message="正在加载用户管理面板..." />}>
                            <LazyUserAdminPanel apiClient={apiClient} currentUser={currentUser} />
                        </Suspense>
                    ) : (
                        <div style={{ padding: '16px' }}>
                            <p className="empty-state">仅管理员可访问用户管理。</p>
                        </div>
                    )}
                </div>
            )}

            {leftPanelTab === 'audit' && (
                <div className="panel-content" style={{ flex: '1 1 auto', padding: 0, overflow: 'auto' }}>
                    {isAdmin ? (
                        <Suspense fallback={<PanelLoadingBody message="正在加载审计日志面板..." />}>
                            <LazyAuditLogPanel apiClient={apiClient} />
                        </Suspense>
                    ) : (
                        <div style={{ padding: '16px' }}>
                            <p className="empty-state">仅管理员可访问审计日志。</p>
                        </div>
                    )}
                </div>
            )}

            {leftPanelTab === 'dinsar_results' && (
                <Suspense fallback={<PanelLoadingPanel message="正在加载 D-InSAR 结果面板..." />}>
                    <LazyDinsarResultPanel
                        dinsarCurrentPage={dinsarPanel.dinsarCurrentPage}
                        dinsarTotalPages={dinsarPanel.dinsarTotalPages}
                        showDinsarPageInputError={dinsarPanel.showDinsarPageInputError}
                        dinsarPageInputValidationError={dinsarPanel.dinsarPageInputValidationError}
                        onSetAllVisibility={dinsarPanel.onSetAllVisibility}
                        onScoreFilterChange={dinsarPanel.onScoreFilterChange}
                        onPageChange={dinsarPanel.onPageChange}
                        onPageSizeChange={dinsarPanel.onPageSizeChange}
                        onGoToPage={dinsarPanel.onGoToPage}
                        onToggleVisibility={dinsarPanel.onToggleVisibility}
                        onLabel={dinsarPanel.onLabel}
                        onAnalyze={dinsarPanel.onAnalyze}
                    />
                </Suspense>
            )}

            {leftPanelTab === 'dinsar_analysis' && (
                <div className="panel-content" style={{ flex: '1 1 auto', padding: 0, overflow: 'auto' }}>
                    <div style={{ padding: '16px' }}>
                        <div className="empty-state">
                            D-InSAR 分析页已预留。
                            <br />
                            后续可在这里承接专题筛选、人工判读、统计汇总和分析报告能力。
                        </div>
                    </div>
                </div>
            )}

            {leftPanelTab === 'psinsar_results' && (
                <div className="panel-content" style={{ flex: '1 1 auto', padding: 0, overflow: 'auto' }}>
                    <Suspense fallback={<PanelLoadingBody message="正在加载时序InSAR结果目录..." />}>
                        <div style={{ padding: '16px' }}>
                            <LazyPsinsarCatalogPanel
                                readOnly
                                showActions={false}
                            />
                        </div>
                    </Suspense>
                </div>
            )}

            {leftPanelTab === 'psinsar_analysis' && (
                <div className="panel-content" style={{ flex: '1 1 auto', padding: 0, overflow: 'auto' }}>
                    <div style={{ padding: '16px' }}>
                        <div className="empty-state">
                            时序InSAR 分析页已预留。
                            <br />
                            后续可以在这里放置时序分析、速率分级、热点识别和专题统计能力。
                        </div>
                    </div>
                </div>
            )}

            {leftPanelTab === 'ai_quality' && (
                <Suspense fallback={<PanelLoadingPanel message="正在加载 AI 质量面板..." />}>
                    <LazyAiQualityPanel
                        aiStatus={aiStatus}
                        isLoading={isLoading}
                        isReadOnlyUser={isReadOnlyUser}
                        onTrain={aiPanel.onTrain}
                        onPredictAll={aiPanel.onPredictAll}
                        language={language}
                    />
                </Suspense>
            )}

            {leftPanelTab === 'ai_diagnosis' && (
                <Suspense fallback={<PanelLoadingPanel message="正在加载 AI 诊断面板..." />}>
                    <LazyAiAnalysisPanel
                        readOnly={isReadOnlyUser}
                        onJobQueued={(taskId) => taskPanel.onTaskStart(taskId, '任务已入队，等待处理...')}
                    />
                </Suspense>
            )}

            {leftPanelTab === 'landslide_segmentation' && (
                <div className="panel-content" style={{ flex: '1 1 auto', padding: 0, overflow: 'auto' }}>
                    <div style={{ padding: '16px' }}>
                        <div className="empty-state">
                            滑坡语义分割模块已预留。
                            <br />
                            后续可在这里接入光学影像分割模型、结果预览、批处理提交和专题输出。
                        </div>
                    </div>
                </div>
            )}

            {leftPanelTab === 'uav_image_analysis' && (
                <div className="panel-content" style={{ flex: '1 1 auto', padding: 0, overflow: 'auto' }}>
                    <div style={{ padding: '16px' }}>
                        <div className="empty-state">
                            无人机影像分析模块已预留。
                            <br />
                            后续可在这里集成无人机正射影像解译、目标识别和变化检测能力。
                        </div>
                    </div>
                </div>
            )}

            {leftPanelTab === 'pairs' && (
                <Suspense fallback={<PanelLoadingPanel message="正在加载配对结果面板..." />}>
                    <LazyPairsListPanel
                        onVisualizePair={pairsPanel.onVisualizePair}
                        onTogglePairVisibility={pairsPanel.onTogglePairVisibility}
                        onCreateDinsarBatch={pairsPanel.onCreateDinsarBatch}
                    />
                </Suspense>
            )}

            {leftPanelTab === 'ps_results' && (
                <Suspense fallback={<PanelLoadingPanel message="正在加载 PS 候选结果面板..." />}>
                    <LazyPsResultsPanel
                        onPreviewPsStack={psPanel.onPreviewPsStack}
                        onCreatePsBatch={psPanel.onCreatePsBatch}
                        onClearPsResults={psPanel.onClearPsResults}
                    />
                </Suspense>
            )}

            {leftPanelTab === 'batches' && (
                <Suspense fallback={<PanelLoadingPanel message="正在加载批处理面板..." />}>
                    <LazyBatchPanel />
                </Suspense>
            )}
        </aside>
    );
}
