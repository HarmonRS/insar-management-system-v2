export default function PairingPanel({
    foundPairs,
    selectedPairsCount,
    isLoading,
    isReadOnlyUser,
    hasEnoughRadarScenesForPlanning,
    onOpenPairingModal,
    onOpenPsModal,
    hasRadarSearched,
    onRefreshRadarSearch,
    onSearchAll,
    onRefreshDinsar,
    language,
}) {
    const en = language === 'en';
    return (
        <div className="panel-content" style={{ flex: '1 1 auto', overflowY: 'auto', padding: '12px' }}>
            <div className="panel-card">
                <div className="panel-card-title">{en ? 'Pair Planning' : '配对规划'}</div>
                <p className="panel-card-desc">
                    {en
                        ? 'Filter interferometric pairs by temporal baseline, spatial baseline, and overlap ratio. Optional AOI constraint.'
                        : '基于时间基线、空间基线与重叠率筛选干涉对，可选 AOI 限定范围。'
                    }
                </p>
                <div className="header-buttons" style={{ marginTop: '10px' }}>
                    <button onClick={onOpenPairingModal} disabled={isLoading || !hasEnoughRadarScenesForPlanning || isReadOnlyUser} style={{ flex: 1 }}>
                        {en ? 'Pair' : '配对'}
                    </button>
                    <button onClick={onOpenPsModal} disabled={isLoading || !hasEnoughRadarScenesForPlanning || isReadOnlyUser} style={{ flex: 1 }}>
                        {en ? 'Timeseries Prep' : '时序准备'}
                    </button>
                </div>
            </div>
            <div className="panel-card" style={{ marginTop: '12px' }}>
                <div className="panel-card-title">{en ? 'Results & Refresh' : '结果与刷新'}</div>
                <div className="panel-card-row">
                    <span>{en ? 'Generated Pairs' : '已生成配对'}</span>
                    <strong>{foundPairs.length}</strong>
                </div>
                <div className="panel-card-row">
                    <span>{en ? 'Selected' : '已选中'}</span>
                    <strong>{selectedPairsCount}</strong>
                </div>
                <div className="header-buttons" style={{ marginTop: '10px' }}>
                    <button onClick={hasRadarSearched ? onRefreshRadarSearch : onSearchAll} disabled={isLoading} style={{ flex: 1 }}>
                        {hasRadarSearched
                            ? (en ? 'Refresh Current Search' : '刷新当前检索')
                            : (en ? 'Search All Source Data' : '搜索全部源数据')
                        }
                    </button>
                    <button onClick={onRefreshDinsar} disabled={isLoading} style={{ flex: 1 }}>
                        {en ? 'Refresh Results' : '刷新结果'}
                    </button>
                </div>
            </div>
        </div>
    );
}
