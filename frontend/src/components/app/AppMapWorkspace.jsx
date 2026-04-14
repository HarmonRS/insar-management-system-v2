import { memo } from 'react';
import { BASE_LAYERS } from '../../config/appConstants';
import { getSelectedRegionTreeId } from '../../utils/appUiHelpers';

function AppMapWorkspace({
    language,
    showMapRegionLocator,
    toggleMapRegionLocator,
    mapRegionOptions,
    mapRegionSelection,
    mapRegionLoading,
    mapRegionLocating,
    mapRegionError,
    mapRegionLocatedName,
    onMapRegionProvinceChange,
    onMapRegionCityChange,
    onLocateSelectedRegion,
    onClearMapRegionHighlight,
    baseLayerKey,
    setBaseLayerKey,
    onOpenExportModal,
}) {
    const en = language === 'en';

    return (
        <div className="center-container">
            <main id="map-container">
                <div id="map"></div>
                <div className="map-region-locator">
                    <button
                        type="button"
                        className={`map-region-toggle-btn ${showMapRegionLocator ? 'active' : ''}`}
                        onClick={toggleMapRegionLocator}
                    >
                        {showMapRegionLocator ? (en ? 'Collapse' : '收起') : (en ? 'Region Locator' : '区域定位')}
                    </button>
                    {showMapRegionLocator && (
                        <div className="map-region-locator-panel">
                            <div className="map-region-locator-title">{en ? 'Region Locator' : '区域定位'}</div>
                            <div className="aoi-region-select-grid map-region-grid">
                                <select
                                    value={mapRegionSelection.province}
                                    onChange={(e) => onMapRegionProvinceChange(e.target.value)}
                                    disabled={mapRegionLoading || mapRegionLocating}
                                >
                                    <option value="">{en ? '-- Province --' : '-- 省级 --'}</option>
                                    {mapRegionOptions.provinces.map((item) => (
                                        <option key={item.tree_id} value={item.tree_id}>{item.name}</option>
                                    ))}
                                </select>
                                <select
                                    value={mapRegionSelection.city}
                                    onChange={(e) => onMapRegionCityChange(e.target.value)}
                                    disabled={mapRegionLoading || mapRegionLocating || !mapRegionSelection.province}
                                >
                                    <option value="">{en ? '-- City --' : '-- 地市 --'}</option>
                                    {mapRegionOptions.cities.map((item) => (
                                        <option key={item.tree_id} value={item.tree_id}>{item.name}</option>
                                    ))}
                                </select>
                            </div>
                            <div className="map-region-locator-hint">
                                {en ? 'You can locate by province or city level.' : '可只选到省/市级进行定位。'}
                            </div>
                            {mapRegionLocatedName && (
                                <div className="map-region-locator-current">
                                    {en ? 'Current: ' : '当前定位：'}{mapRegionLocatedName}
                                </div>
                            )}
                            {mapRegionError && (
                                <div className="map-region-locator-error">{mapRegionError}</div>
                            )}
                            <div className="map-region-locator-actions">
                                <button
                                    type="button"
                                    className="map-region-locate-btn"
                                    onClick={onLocateSelectedRegion}
                                    disabled={mapRegionLoading || mapRegionLocating || !getSelectedRegionTreeId(mapRegionSelection)}
                                >
                                    {mapRegionLocating ? (en ? 'Locating...' : '定位中...') : (en ? 'Locate Selected Region' : '定位到选中区域')}
                                </button>
                                <button
                                    type="button"
                                    className="map-region-clear-btn"
                                    onClick={onClearMapRegionHighlight}
                                    disabled={mapRegionLocating || !mapRegionLocatedName}
                                >
                                    {en ? 'Clear Highlight' : '清除定位高亮'}
                                </button>
                            </div>
                        </div>
                    )}
                </div>
                <div className="map-layer-switch">
                    <div className="map-layer-title">{en ? 'Base Map' : '底图'}</div>
                    <div className="map-layer-buttons">
                        {Object.entries(BASE_LAYERS).map(([key, layer]) => (
                            <button
                                key={key}
                                type="button"
                                className={baseLayerKey === key ? 'active' : ''}
                                onClick={() => setBaseLayerKey(key)}
                            >
                                {layer.label}
                            </button>
                        ))}
                    </div>
                </div>
                <button type="button" className="map-export-btn" onClick={onOpenExportModal}>
                    {en ? 'Export' : '导出'}
                </button>
            </main>
        </div>
    );
}

export default memo(AppMapWorkspace);
