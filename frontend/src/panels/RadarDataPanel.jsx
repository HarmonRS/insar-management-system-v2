import { useMemo } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { useRadarStore, useUiStore, useAuthStore } from '../store';
import { useI18n } from '../i18n/I18nContext';
import VirtualizedList from '../components/common/VirtualizedList';
import RadarDataRow from '../components/panels/RadarDataRow';
import UnifiedDatePicker from '../components/UnifiedDatePicker';
import { PAGE_SIZE_OPTIONS, SATELLITE_GROUPS } from '../config/appConstants';
import { getPageHintText } from '../utils/appUiHelpers';

const RADAR_ROW_HEIGHT = 72;

export default function RadarDataPanel({
    radarCurrentPage,
    radarTotalPages,
    showRadarPageInputError,
    radarPageInputValidationError,
    // handlers
    onSearchAll,
    onShowStats,
    onSearch,
    onReset,
    onAoiModeChange,
    onProvinceChange,
    onCityChange,
    onSetRadarSearchFiles,
    updateDraft,
    onPageChange,
    onPageSizeChange,
    onGoToPage,
    onSelectAllVisibility,
    onSetAllPreviewVisibility,
    onToggleLayer,
    onTogglePreview,
    onRebuildPreview,
    onShowDataInfo,
    onFlyTo,
    onChangeSatelliteGroup,
}) {
    const { language } = useI18n();
    const {
        allData, radarPagination,
        radarPageInput, setRadarPageInput, setRadarPageInputTouched,
        hasRadarSearched, rebuildingPreviewIds,
        radarSearchDraft, radarSearchOptions, radarSearchOptionsLoading,
        radarSearchAoiMode,
        radarSearchRegionOptions, radarSearchRegionSelection,
        radarSearchRegionLoading, radarSearchRegionError,
        selectedSatelliteGroup,
    } = useRadarStore(useShallow((state) => ({
        allData: state.allData,
        radarPagination: state.radarPagination,
        radarPageInput: state.radarPageInput,
        setRadarPageInput: state.setRadarPageInput,
        setRadarPageInputTouched: state.setRadarPageInputTouched,
        hasRadarSearched: state.hasRadarSearched,
        rebuildingPreviewIds: state.rebuildingPreviewIds,
        radarSearchDraft: state.radarSearchDraft,
        radarSearchOptions: state.radarSearchOptions,
        radarSearchOptionsLoading: state.radarSearchOptionsLoading,
        radarSearchAoiMode: state.radarSearchAoiMode,
        radarSearchRegionOptions: state.radarSearchRegionOptions,
        radarSearchRegionSelection: state.radarSearchRegionSelection,
        radarSearchRegionLoading: state.radarSearchRegionLoading,
        radarSearchRegionError: state.radarSearchRegionError,
        selectedSatelliteGroup: state.selectedSatelliteGroup,
    })));
    const isLoading = useUiStore((state) => state.isLoading);
    const { currentUser } = useAuthStore();
    const isAdmin = currentUser?.role === 'admin';
    const allVisibleOnCurrentPage = useMemo(
        () => allData.length > 0 && allData.every((item) => item.isVisible),
        [allData]
    );

    // Build visible satellite group buttons based on available satellites in the database
    const visibleSatelliteGroups = useMemo(() => {
        const allSatellites = radarSearchOptions.satellite || [];
        return SATELLITE_GROUPS.filter((group) =>
            group.prefixes.some((prefix) => allSatellites.some((sat) => sat.startsWith(prefix)))
        );
    }, [radarSearchOptions.satellite]);

    return (
        <>
            <div style={{ padding: '10px', borderBottom: '1px solid #eee', flex: '0 0 auto' }}>
                <div className="header-buttons">
                    <button onClick={onSearchAll} disabled={isLoading} style={{ flex: 1 }}>
                        {language === 'en' ? 'Search All Source Data' : '搜索全部源数据'}
                    </button>
                    <button onClick={onShowStats} disabled={isLoading} style={{ width: 'auto' }}>
                        {language === 'en' ? 'Statistics' : '统计'}
                    </button>
                </div>
                <div style={{ marginTop: '10px', border: '1px solid #e2e8f0', borderRadius: '8px', padding: '10px', background: '#f8fafc' }}>
                    <div style={{ fontWeight: 600, marginBottom: '8px', fontSize: '13px' }}>
                        {language === 'en' ? 'Source Data Search' : '源数据检索'}
                    </div>
                    {visibleSatelliteGroups.length > 0 && (
                        <div style={{ display: 'flex', gap: '6px', marginBottom: '8px' }}>
                            <button
                                type="button"
                                onClick={() => onChangeSatelliteGroup('all')}
                                disabled={radarSearchOptionsLoading}
                                style={{
                                    flex: 1,
                                    padding: '6px 0',
                                    fontSize: '13px',
                                    fontWeight: selectedSatelliteGroup === 'all' ? 700 : 400,
                                    background: selectedSatelliteGroup === 'all' ? '#3b82f6' : '#f1f5f9',
                                    color: selectedSatelliteGroup === 'all' ? '#fff' : '#334155',
                                    border: selectedSatelliteGroup === 'all' ? '1px solid #2563eb' : '1px solid #cbd5e1',
                                    borderRadius: '6px',
                                    cursor: 'pointer',
                                }}
                            >
                                {language === 'en' ? 'All' : '全部'}
                            </button>
                            {visibleSatelliteGroups.map((group) => (
                                <button
                                    key={group.key}
                                    type="button"
                                    onClick={() => onChangeSatelliteGroup(group.key)}
                                    disabled={radarSearchOptionsLoading}
                                    style={{
                                        flex: 1,
                                        padding: '6px 0',
                                        fontSize: '13px',
                                        fontWeight: selectedSatelliteGroup === group.key ? 700 : 400,
                                        background: selectedSatelliteGroup === group.key ? '#3b82f6' : '#f1f5f9',
                                        color: selectedSatelliteGroup === group.key ? '#fff' : '#334155',
                                        border: selectedSatelliteGroup === group.key ? '1px solid #2563eb' : '1px solid #cbd5e1',
                                        borderRadius: '6px',
                                        cursor: 'pointer',
                                    }}
                                >
                                    {group.label}
                                </button>
                            ))}
                        </div>
                    )}
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', marginBottom: '8px' }}>
                        <select value={radarSearchDraft.imaging_mode} onChange={(e) => updateDraft('imaging_mode', e.target.value)} disabled={radarSearchOptionsLoading}>
                            <option value="">{language === 'en' ? 'Imaging Mode: All' : '成像模式：全部'}</option>
                            {radarSearchOptions.imaging_mode.map((item) => (
                                <option key={item} value={item}>{item}</option>
                            ))}
                        </select>
                        <UnifiedDatePicker
                            value={radarSearchDraft.imaging_date_from}
                            onChange={(nextValue) => updateDraft('imaging_date_from', nextValue)}
                            language={language}
                            title={language === 'en' ? 'Imaging Date From: Any' : '成像时间起：不限'}
                            ariaLabel={language === 'en' ? 'Imaging Date From' : '成像时间起'}
                            placeholder={language === 'en' ? 'Select start date' : '选择起始日期'}
                        />
                        <UnifiedDatePicker
                            value={radarSearchDraft.imaging_date_to}
                            onChange={(nextValue) => updateDraft('imaging_date_to', nextValue)}
                            language={language}
                            title={language === 'en' ? 'Imaging Date To: Any' : '成像时间止：不限'}
                            ariaLabel={language === 'en' ? 'Imaging Date To' : '成像时间止'}
                            placeholder={language === 'en' ? 'Select end date' : '选择结束日期'}
                        />
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', marginBottom: '8px' }}>
                        <select value={radarSearchDraft.polarization} onChange={(e) => updateDraft('polarization', e.target.value)} disabled={radarSearchOptionsLoading}>
                            <option value="">{language === 'en' ? 'Polarization: All' : '极化方式：全部'}</option>
                            {radarSearchOptions.polarization.map((item) => (
                                <option key={item} value={item}>{item}</option>
                            ))}
                        </select>
                        <select value={radarSearchDraft.product_level} onChange={(e) => updateDraft('product_level', e.target.value)} disabled={radarSearchOptionsLoading}>
                            <option value="">{language === 'en' ? 'Product Level: All' : '产品级别：全部'}</option>
                            {radarSearchOptions.product_level.map((item) => (
                                <option key={item} value={item}>{item}</option>
                            ))}
                        </select>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', marginBottom: '8px' }}>
                        <select value={radarSearchAoiMode} onChange={(e) => onAoiModeChange(e.target.value)}>
                            <option value="none">{language === 'en' ? 'AOI: Any' : '空间范围：不限'}</option>
                            <option value="region">{language === 'en' ? 'AOI: Region' : '空间范围：行政区'}</option>
                            <option value="shp">{language === 'en' ? 'AOI: Upload SHP' : '空间范围：上传SHP'}</option>
                        </select>
                        <div style={{ display: 'flex', gap: '8px' }}>
                            <button type="button" onClick={onSearch} disabled={isLoading} style={{ flex: 1 }}>
                                {language === 'en' ? 'Search' : '搜索'}
                            </button>
                            <button type="button" onClick={onReset} disabled={isLoading} style={{ flex: 1 }}>
                                {language === 'en' ? 'Reset' : '重置'}
                            </button>
                        </div>
                    </div>
                    {radarSearchAoiMode === 'region' && (
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', marginBottom: '8px' }}>
                            <select
                                value={radarSearchRegionSelection.province}
                                onChange={(e) => onProvinceChange(e.target.value)}
                                disabled={radarSearchRegionLoading}
                            >
                                <option value="">{language === 'en' ? 'Select Province' : '选择省份'}</option>
                                {radarSearchRegionOptions.provinces.map((item) => (
                                    <option key={item.tree_id} value={item.tree_id}>{item.name}</option>
                                ))}
                            </select>
                            <select
                                value={radarSearchRegionSelection.city}
                                onChange={(e) => onCityChange(e.target.value)}
                                disabled={radarSearchRegionLoading || !radarSearchRegionSelection.province}
                            >
                                <option value="">{language === 'en' ? 'Select City (Optional)' : '选择地市（可选）'}</option>
                                {radarSearchRegionOptions.cities.map((item) => (
                                    <option key={item.tree_id} value={item.tree_id}>{item.name}</option>
                                ))}
                            </select>
                        </div>
                    )}
                    {radarSearchAoiMode === 'region' && radarSearchRegionError && (
                        <div style={{ color: '#dc2626', fontSize: '12px', marginBottom: '8px' }}>{radarSearchRegionError}</div>
                    )}
                    {radarSearchAoiMode === 'shp' && (
                        <div style={{ marginBottom: '8px' }}>
                            <input
                                type="file"
                                multiple
                                accept=".shp,.dbf,.shx,.prj,.cpg,.geojson,.json"
                                onChange={(e) => onSetRadarSearchFiles(e.target.files)}
                            />
                        </div>
                    )}
                    <details>
                        <summary style={{ cursor: 'pointer', fontSize: '12px', color: '#334155' }}>
                            {language === 'en' ? 'Advanced Fields' : '高级字段'}
                        </summary>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', marginTop: '8px' }}>
                            <select value={radarSearchDraft.satellite_mode} onChange={(e) => updateDraft('satellite_mode', e.target.value)} disabled={radarSearchOptionsLoading}>
                                <option value="">{language === 'en' ? 'Satellite Mode: All' : '卫星模式：全部'}</option>
                                {radarSearchOptions.satellite_mode.map((item) => (
                                    <option key={item} value={item}>{item}</option>
                                ))}
                            </select>
                            <select value={radarSearchDraft.receiving_station} onChange={(e) => updateDraft('receiving_station', e.target.value)} disabled={radarSearchOptionsLoading}>
                                <option value="">{language === 'en' ? 'Receiving Station: All' : '接收站：全部'}</option>
                                {radarSearchOptions.receiving_station.map((item) => (
                                    <option key={item} value={item}>{item}</option>
                                ))}
                            </select>
                            <select value={radarSearchDraft.orbit_circle} onChange={(e) => updateDraft('orbit_circle', e.target.value)} disabled={radarSearchOptionsLoading}>
                                <option value="">{language === 'en' ? 'Orbit Circle: All' : '轨道圈号：全部'}</option>
                                {radarSearchOptions.orbit_circle.map((item) => (
                                    <option key={item} value={item}>{item}</option>
                                ))}
                            </select>
                            <select value={radarSearchDraft.acquisition_time_utc} onChange={(e) => updateDraft('acquisition_time_utc', e.target.value)} disabled={radarSearchOptionsLoading}>
                                <option value="">{language === 'en' ? 'Acquisition Time: All' : '采集时间：全部'}</option>
                                {radarSearchOptions.acquisition_time_utc.map((item) => (
                                    <option key={item} value={item}>{item}</option>
                                ))}
                            </select>
                            <select value={radarSearchDraft.product_type} onChange={(e) => updateDraft('product_type', e.target.value)} disabled={radarSearchOptionsLoading}>
                                <option value="">{language === 'en' ? 'Product Type: All' : '产品类型：全部'}</option>
                                {radarSearchOptions.product_type.map((item) => (
                                    <option key={item} value={item}>{item}</option>
                                ))}
                            </select>
                            <select value={radarSearchDraft.product_unique_id} onChange={(e) => updateDraft('product_unique_id', e.target.value)} disabled={radarSearchOptionsLoading}>
                                <option value="">{language === 'en' ? 'Product Unique ID: All' : '产品唯一ID：全部'}</option>
                                {radarSearchOptions.product_unique_id.map((item) => (
                                    <option key={item} value={item}>{item}</option>
                                ))}
                            </select>
                            <select value={radarSearchDraft.orbit_direction} onChange={(e) => updateDraft('orbit_direction', e.target.value)} disabled={radarSearchOptionsLoading}>
                                <option value="">{language === 'en' ? 'Orbit Direction: All' : '轨道方向：全部'}</option>
                                {radarSearchOptions.orbit_direction.map((item) => (
                                    <option key={item} value={item}>{item}</option>
                                ))}
                            </select>
                            <select value={radarSearchDraft.has_orbit_data} onChange={(e) => updateDraft('has_orbit_data', e.target.value)}>
                                <option value="">{language === 'en' ? 'Precise Orbit: All' : '有精轨：全部'}</option>
                                <option value="true">{language === 'en' ? 'Precise Orbit: Yes' : '有精轨：是'}</option>
                                <option value="false">{language === 'en' ? 'Precise Orbit: No' : '有精轨：否'}</option>
                            </select>
                            <select value={radarSearchDraft.is_envi_processed} onChange={(e) => updateDraft('is_envi_processed', e.target.value)}>
                                <option value="">{language === 'en' ? 'ENVI Processed: All' : 'ENVI已处理：全部'}</option>
                                <option value="true">{language === 'en' ? 'ENVI Processed: Yes' : 'ENVI已处理：是'}</option>
                                <option value="false">{language === 'en' ? 'ENVI Processed: No' : 'ENVI已处理：否'}</option>
                            </select>
                        </div>
                    </details>
                    {radarSearchOptionsLoading && (
                        <div style={{ marginTop: '8px', fontSize: '12px', color: '#64748b' }}>
                            {language === 'en' ? 'Loading source data search options...' : '源数据检索选项加载中...'}
                        </div>
                    )}
                </div>
            </div>
            <div className="panel-content panel-scroll-shell">
                {allData.length === 0 && !isLoading ? (
                    <div className="empty-state">
                        <p>
                            {!hasRadarSearched
                                ? (language === 'en'
                                    ? 'No query yet. Please run Search or Search All first.'
                                    : '尚未执行检索，请先点击"搜索"或"搜索全部"。')
                                : (language === 'en'
                                    ? 'No data matched this query.'
                                    : '当前检索未命中数据。')}
                        </p>
                    </div>
                ) : (
                    <>
                        <div className="list-toolbar column-layout">
                            <div className="toolbar-row">
                                <input
                                    type="checkbox"
                                    id="select-all-visibility"
                                    checked={allVisibleOnCurrentPage}
                                    onChange={onSelectAllVisibility}
                                />
                                <label htmlFor="select-all-visibility">
                                    {language === 'en'
                                        ? `Toggle all coverage (Current page ${allData.length} items / Total ${radarPagination.total} items)`
                                        : `覆盖面全部显示/隐藏（当前页 ${allData.length} 条 / 总计 ${radarPagination.total} 条）`}
                                </label>
                            </div>
                            <div className="toolbar-row">
                                <button type="button" onClick={() => onSetAllPreviewVisibility(true)} disabled={allData.length === 0}>
                                    {language === 'en' ? 'Show All Source Previews' : '源影像一键显示'}
                                </button>
                                <button type="button" onClick={() => onSetAllPreviewVisibility(false)} disabled={allData.length === 0}>
                                    {language === 'en' ? 'Hide All Source Previews' : '源影像一键隐藏'}
                                </button>
                            </div>
                            <div className="toolbar-row">
                                <button type="button" onClick={() => onPageChange(-1)} disabled={isLoading || !hasRadarSearched || radarPagination.offset <= 0}>
                                    {language === 'en' ? 'Previous' : '上一页'}
                                </button>
                                <span style={{ fontSize: '12px', color: '#4a5568' }}>
                                    {language === 'en'
                                        ? `Page ${radarCurrentPage}/${radarTotalPages}`
                                        : `第 ${radarCurrentPage}/${radarTotalPages} 页`}
                                </span>
                                <button type="button" onClick={() => onPageChange(1)} disabled={isLoading || !hasRadarSearched || !radarPagination.hasMore}>
                                    {language === 'en' ? 'Next' : '下一页'}
                                </button>
                            </div>
                            <div className="toolbar-row">
                                <label style={{ fontSize: '12px', color: '#4a5568' }}>
                                    {language === 'en' ? 'Per page' : '每页'}
                                    <select
                                        value={radarPagination.limit}
                                        onChange={onPageSizeChange}
                                        disabled={isLoading || !hasRadarSearched}
                                        style={{ marginLeft: '6px', marginRight: '6px' }}
                                    >
                                        {PAGE_SIZE_OPTIONS.map(size => (
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
                                        max={radarTotalPages}
                                        value={radarPageInput}
                                        onChange={(e) => {
                                            setRadarPageInput(e.target.value);
                                            setRadarPageInputTouched(false);
                                        }}
                                        onBlur={() => setRadarPageInputTouched(true)}
                                        onKeyDown={(e) => {
                                            if (e.key === 'Enter') {
                                                e.preventDefault();
                                                onGoToPage();
                                            }
                                        }}
                                        disabled={isLoading || !hasRadarSearched}
                                        style={{
                                            width: '70px',
                                            marginLeft: '6px',
                                            marginRight: '6px',
                                            borderColor: showRadarPageInputError ? '#e53e3e' : undefined,
                                            boxShadow: showRadarPageInputError ? '0 0 0 1px rgba(229,62,62,0.25)' : undefined,
                                        }}
                                    />
                                    {language === 'en' ? 'page' : '页'}
                                </label>
                                <button type="button" onClick={onGoToPage} disabled={isLoading || !hasRadarSearched}>
                                    {language === 'en' ? 'Jump' : '跳转'}
                                </button>
                            </div>
                            <div className="toolbar-row">
                                <span style={{ fontSize: '12px', color: showRadarPageInputError ? '#e53e3e' : '#718096' }}>
                                    {showRadarPageInputError
                                        ? radarPageInputValidationError
                                        : (hasRadarSearched
                                            ? getPageHintText(radarTotalPages, language)
                                            : (language === 'en'
                                                ? 'Run a search first to enable pagination.'
                                                : '请先执行检索，再使用分页。'))}
                                </span>
                            </div>
                        </div>
                        <VirtualizedList
                            items={allData}
                            itemHeight={RADAR_ROW_HEIGHT}
                            getKey={(item) => item.id}
                            renderItem={(item, index, key) => (
                                <RadarDataRow
                                    key={key || `${item.id}-${index}`}
                                    item={item}
                                    language={language}
                                    isAdmin={isAdmin}
                                    isRebuilding={!!rebuildingPreviewIds[item.id]}
                                    onFlyTo={onFlyTo}
                                    onShowDataInfo={onShowDataInfo}
                                    onTogglePreview={onTogglePreview}
                                    onRebuildPreview={onRebuildPreview}
                                    onToggleLayer={onToggleLayer}
                                />
                            )}
                        />
                    </>
                )}
            </div>
        </>
    );
}
