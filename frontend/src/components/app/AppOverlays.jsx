import { Suspense, lazy } from 'react';
import { useShallow } from 'zustand/react/shallow';
import LicenseOverlay from '../LicenseOverlay';
import { useDinsarStore, usePairingStore, useUiStore } from '../../store';
import { useI18n } from '../../i18n/I18nContext';
import { formatYmd } from '../../utils/appUiHelpers';
import { ModalLoadingFallback } from './AppLoadingFallbacks';

const LazyPairingModal = lazy(() => import('../PairingModal'));
const LazyPsStackModal = lazy(() => import('../PsStackModal'));
const LazyDataInfoModal = lazy(() => import('../DataInfoModal'));
const LazyActiveTasksOverlay = lazy(() => import('../ActiveTasksOverlay'));
const LazyStatisticsDashboard = lazy(() => import('../../StatisticsDashboard'));
const LazyAiReportModal = lazy(() => import('../AiReportModal'));
const LazyMapExportModal = lazy(() => import('../MapExportModal'));

export default function AppOverlays({
    onPairingSubmit,
    onPairingAoiModeChange,
    onPairingProvinceChange,
    onPairingCityChange,
    onPsSubmit,
    onPsAoiModeChange,
    onPsProvinceChange,
    onPsCityChange,
    licenseLoading,
    licenseStatus,
    isAdmin,
    licenseFileRef,
    onUploadFile,
    onRefreshLicenseStatus,
    licenseFileName,
    licenseUploadStatus,
    isGlobalLocked,
    activeTasks,
    showForceUnlock,
    forceUnlockPwd,
    onShowForceUnlock,
    onForceUnlockPwdChange,
    onForceUnlockConfirm,
    onCancelForceUnlock,
    mapExport,
}) {
    const { language, t } = useI18n();
    const { showPairingModal, showPsModal } = usePairingStore(useShallow((state) => ({
        showPairingModal: state.showPairingModal,
        showPsModal: state.showPsModal,
    })));
    const {
        showStats,
        setShowStats,
        showDataInfo,
        setShowDataInfo,
        selectedDataInfo,
    } = useUiStore(useShallow((state) => ({
        showStats: state.showStats,
        setShowStats: state.setShowStats,
        showDataInfo: state.showDataInfo,
        setShowDataInfo: state.setShowDataInfo,
        selectedDataInfo: state.selectedDataInfo,
    })));
    const { activeAiReport, setActiveAiReport } = useDinsarStore(useShallow((state) => ({
        activeAiReport: state.activeAiReport,
        setActiveAiReport: state.setActiveAiReport,
    })));

    return (
        <>
            {showPairingModal && (
                <Suspense fallback={<ModalLoadingFallback message="正在加载组网参数弹窗..." />}>
                    <LazyPairingModal
                        onSubmit={onPairingSubmit}
                        onAoiModeChange={onPairingAoiModeChange}
                        onProvinceChange={onPairingProvinceChange}
                        onCityChange={onPairingCityChange}
                    />
                </Suspense>
            )}

            {showPsModal && (
                <Suspense fallback={<ModalLoadingFallback message="正在加载 PS 参数弹窗..." />}>
                    <LazyPsStackModal
                        onSubmit={onPsSubmit}
                        onAoiModeChange={onPsAoiModeChange}
                        onProvinceChange={onPsProvinceChange}
                        onCityChange={onPsCityChange}
                    />
                </Suspense>
            )}

            {activeAiReport && (
                <Suspense fallback={<ModalLoadingFallback message="正在加载 AI 报告..." />}>
                    <LazyAiReportModal
                        report={activeAiReport}
                        onClose={() => setActiveAiReport(null)}
                    />
                </Suspense>
            )}

            {showStats && (
                <Suspense fallback={<ModalLoadingFallback message="正在加载统计看板..." />}>
                    <LazyStatisticsDashboard onClose={() => setShowStats(false)} />
                </Suspense>
            )}

            <LicenseOverlay
                licenseLoading={licenseLoading}
                licenseStatus={licenseStatus}
                isAdmin={isAdmin}
                licenseFileRef={licenseFileRef}
                onUploadFile={onUploadFile}
                onRefreshStatus={onRefreshLicenseStatus}
                licenseFileName={licenseFileName}
                licenseUploadStatus={licenseUploadStatus}
            />

            {showDataInfo && (
                <Suspense fallback={<ModalLoadingFallback message="正在加载数据详情..." />}>
                    <LazyDataInfoModal
                        visible={showDataInfo}
                        dataInfo={selectedDataInfo}
                        language={language}
                        formatYmd={(value) => formatYmd(value, language)}
                        onClose={() => setShowDataInfo(false)}
                    />
                </Suspense>
            )}

            {isGlobalLocked && (
                <Suspense fallback={<ModalLoadingFallback message="正在加载任务控制面板..." />}>
                    <LazyActiveTasksOverlay
                        isVisible={isGlobalLocked}
                        activeTasks={activeTasks}
                        t={t}
                        isAdmin={isAdmin}
                        showForceUnlock={showForceUnlock}
                        forceUnlockPwd={forceUnlockPwd}
                        onShowForceUnlock={onShowForceUnlock}
                        onForceUnlockPwdChange={onForceUnlockPwdChange}
                        onForceUnlockConfirm={onForceUnlockConfirm}
                        onCancelForceUnlock={onCancelForceUnlock}
                    />
                </Suspense>
            )}

            {mapExport.showExportModal && (
                <Suspense fallback={<ModalLoadingFallback message="正在加载地图导出工具..." />}>
                    <LazyMapExportModal {...mapExport} />
                </Suspense>
            )}
        </>
    );
}
