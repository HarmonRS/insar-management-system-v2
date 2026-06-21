import { Suspense, lazy } from 'react';
import { useShallow } from 'zustand/react/shallow';
import LicenseOverlay from '../LicenseOverlay';
import { useDinsarStore, usePairingStore, useUiStore } from '../../store';
import { useI18n } from '../../i18n/I18nContext';
import { formatYmd } from '../../utils/appUiHelpers';
import { ModalLoadingFallback } from './AppLoadingFallbacks';

const LazyPairingModal = lazy(() => import('../PairingModal'));
const LazyDataInfoModal = lazy(() => import('../DataInfoModal'));
const LazyGlobalTaskCenter = lazy(() => import('../GlobalTaskCenter'));
const LazyStatisticsDashboard = lazy(() => import('../../StatisticsDashboard'));
const LazyAiReportModal = lazy(() => import('../AiReportModal'));
const LazyMapExportModal = lazy(() => import('../MapExportModal'));

export default function AppOverlays({
    onPairingSubmit,
    onPairingAoiModeChange,
    onPairingProvinceChange,
    onPairingCityChange,
    licenseLoading,
    licenseStatus,
    isAdmin,
    licenseFileRef,
    onUploadFile,
    onRefreshLicenseStatus,
    licenseFileName,
    licenseUploadStatus,
    activeTasks,
    showCancelTask,
    cancelTaskPwd,
    onShowCancelTask,
    onCancelTaskPwdChange,
    onCancelTaskConfirm,
    onCloseCancelTask,
    mapExport,
}) {
    const { language, t } = useI18n();
    const { showPairingModal } = usePairingStore(useShallow((state) => ({
        showPairingModal: state.showPairingModal,
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

            {activeTasks.length > 0 && (
                <Suspense fallback={<ModalLoadingFallback message="正在加载任务中心..." />}>
                    <LazyGlobalTaskCenter
                        isVisible={activeTasks.length > 0}
                        activeTasks={activeTasks}
                        t={t}
                        isAdmin={isAdmin}
                        showCancelTask={showCancelTask}
                        cancelTaskPwd={cancelTaskPwd}
                        onShowCancelTask={onShowCancelTask}
                        onCancelTaskPwdChange={onCancelTaskPwdChange}
                        onCancelTaskConfirm={onCancelTaskConfirm}
                        onCloseCancelTask={onCloseCancelTask}
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
