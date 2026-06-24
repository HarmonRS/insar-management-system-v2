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
import { getLeftTabDescription, getLeftTabLabel } from '../../utils/appUiHelpers';
import { PanelLoadingBody, PanelLoadingPanel } from './AppLoadingFallbacks';

const LazyDataMonitorPanel = lazy(() => import('../../DataMonitorPanel'));
const LazyAssetInventoryPanel = lazy(() => import('../../AssetInventoryPanel'));
const LazyIDLAutomationPanel = lazy(() => import('../../IDLAutomationPanel'));
const LazyHazardPointPanel = lazy(() => import('../../HazardPointPanel'));
const LazyHealthCheckPanel = lazy(() => import('../../HealthCheckPanel'));
const LazyFloodAnalysisWorkspace = lazy(() => import('../../FloodAnalysisWorkspace'));
const LazyUserAdminPanel = lazy(() => import('../../UserAdminPanel'));
const LazyAuditLogPanel = lazy(() => import('../../AuditLogPanel'));
const LazyAiQualityPanel = lazy(() => import('../../panels/AiQualityPanel'));
const LazyAiAnalysisPanel = lazy(() => import('../../AiAnalysisPanel'));
const LazyDinsarAnalysisPanel = lazy(() => import('../../panels/DinsarAnalysisPanel'));
const LazyDinsarResultPanel = lazy(() => import('../../panels/DinsarResultPanel'));
const LazyPsinsarCatalogPanel = lazy(() => import('../PsinsarCatalogPanel'));
const LazySbasInsarMapAnalysisPanel = lazy(() => import('../../panels/SbasInsarMapAnalysisPanel'));
const LazyProductionWorkspace = lazy(() => import('../../ProductionWorkspace'));
const LazyStatisticsDashboard = lazy(() => import('../../StatisticsDashboard'));
const LazyResultExtractionPanel = lazy(() => import('../../ResultExtractionPanel'));

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
    floodPanel,
    dinsarPanel,
    aiPanel,
    pairsPanel,
    sbasAnalysisPanel,
}) {
    const isProductionWorkspace = PRODUCTION_WORKSPACE_ROUTE_TABS.has(leftPanelTab);
    const activeLeftGroup = LEFT_TAB_GROUP[leftPanelTab] || 'data';
    const leftTabLabelContext = {
        pairCount: foundPairs.length,
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
        ? '这里统一承载 D-InSAR 与 Gamma SBAS-InSAR 的数据准备、生产运行、质量检查和成果发布。'
        : getLeftTabDescription(leftPanelTab);

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

            {leftPanelTab === 'statistics' && (
                <div className="panel-content" style={{ flex: '1 1 auto', padding: 0, overflow: 'auto' }}>
                    <Suspense fallback={<PanelLoadingBody message="正在加载综合统计..." />}>
                        <LazyStatisticsDashboard />
                    </Suspense>
                </div>
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

            {leftPanelTab === 'asset_inventory' && (
                <div className="panel-content" style={{ flex: '1 1 auto', padding: 0, overflow: 'auto' }}>
                    <Suspense fallback={<PanelLoadingBody message="正在加载资产台账..." />}>
                        <LazyAssetInventoryPanel
                            readOnly={isReadOnlyUser}
                            onTaskStart={taskPanel.onTaskStart}
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
                                apiEndpoint={apiEndpoint}
                                language={language}
                                foundPairs={foundPairs}
                                selectedPairsCount={selectedPairsCount}
                                isLoading={isLoading}
                                hasEnoughRadarScenesForPlanning={hasEnoughRadarScenesForPlanning}
                                hasRadarSearched={hasRadarSearched}
                                pairingPanel={pairingPanel}
                                radarPanel={radarPanel}
                                pairsPanel={pairsPanel}
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

            {leftPanelTab === 'flood_analysis' && (
                <div className="panel-content" style={{ flex: '1 1 auto', padding: 0, overflow: 'auto' }}>
                    <Suspense fallback={<PanelLoadingBody message="正在加载洪涝灾害分析工作台..." />}>
                        <LazyFloodAnalysisWorkspace
                            readOnly={isReadOnlyUser}
                            onTaskStart={taskPanel.onTaskStart}
                            floodPanel={floodPanel}
                        />
                    </Suspense>
                </div>
            )}

            {leftPanelTab === 'health' && (
                <div className="panel-content" style={{ flex: '1 1 auto', padding: 0, overflow: 'auto' }}>
                    <Suspense fallback={<PanelLoadingBody message="正在加载运行维护面板..." />}>
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
                        onOpenResultExtraction={() => setLeftPanelTab('result_extraction')}
                    />
                </Suspense>
            )}

            {leftPanelTab === 'dinsar_analysis' && (
                <Suspense fallback={<PanelLoadingPanel message="正在加载 D-InSAR 分析面板..." />}>
                    <LazyDinsarAnalysisPanel
                        aiStatus={aiStatus}
                        isLoading={isLoading}
                        isReadOnlyUser={isReadOnlyUser}
                        aiPanel={aiPanel}
                        language={language}
                        onJobQueued={(taskId) => taskPanel.onTaskStart(taskId, '任务已入队，等待处理...')}
                    />
                </Suspense>
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
                    <Suspense fallback={<PanelLoadingBody message="正在加载时序InSAR地图分析..." />}>
                        <LazySbasInsarMapAnalysisPanel
                            readOnly={isReadOnlyUser}
                            {...sbasAnalysisPanel}
                        />
                    </Suspense>
                </div>
            )}

            {leftPanelTab === 'result_extraction' && (
                <div className="panel-content" style={{ flex: '1 1 auto', padding: 0, overflow: 'auto' }}>
                    <Suspense fallback={<PanelLoadingBody message="正在加载结果提取工作台..." />}>
                        <LazyResultExtractionPanel readOnly={isReadOnlyUser} />
                    </Suspense>
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

        </aside>
    );
}
