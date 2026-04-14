import { useEffect, useRef } from 'react';
import { useI18n } from '../i18n/I18nContext';

export default function MapExportModal({
    showExportModal, exportTitle, setExportTitle,
    exportFormat, setExportFormat,
    exportResolution, RESOLUTIONS, handleResolutionChange,
    showLegend, setShowLegend,
    showScaleBar, setShowScaleBar,
    showNorthArrow, setShowNorthArrow,
    legendItems, previewUrl,
    isCapturing, isExporting,
    exportOrg, setExportOrg,
    logoDataUrl, handleLogoUpload, removeLogo,
    closeExportModal, refreshPreview, executeExport,
    updateLegendItem, removeLegendItem, addLegendItem,
}) {
    const { language } = useI18n();
    const en = language === 'en';
    const logoInputRef = useRef(null);

    useEffect(() => {
        if (!showExportModal) return;
        const timer = setTimeout(() => {
            refreshPreview({
                title: exportTitle,
                format: exportFormat,
                showLegend,
                showScaleBar,
                showNorthArrow,
                legendItems,
                orgName: exportOrg,
            });
        }, 300);
        return () => clearTimeout(timer);
    }, [exportTitle, exportFormat, showLegend, showScaleBar, showNorthArrow,
        legendItems, exportOrg, logoDataUrl, showExportModal, refreshPreview]);

    if (!showExportModal) return null;

    return (
        <div className="modal-overlay visible map-export-modal">
            <div className="modal-content export-content">
                <div className="export-header">
                    <h3>{en ? 'Export Map' : '导出地图'}</h3>
                    <button type="button" className="export-close-btn" onClick={closeExportModal}>&times;</button>
                </div>

                <div className="export-body">
                    <div className="export-config">
                        {/* Title */}
                        <div className="export-section">
                            <label className="export-label">{en ? 'Map Title' : '图名'}</label>
                            <input
                                type="text"
                                className="export-input"
                                value={exportTitle}
                                onChange={(e) => setExportTitle(e.target.value)}
                                placeholder={en ? 'Optional title...' : '可选标题...'}
                            />
                        </div>

                        {/* Org */}
                        <div className="export-section">
                            <label className="export-label">{en ? 'Organization' : '制图单位'}</label>
                            <input
                                type="text"
                                className="export-input"
                                value={exportOrg}
                                onChange={(e) => setExportOrg(e.target.value)}
                                placeholder={en ? 'Organization name...' : '单位名称...'}
                            />
                        </div>

                        {/* Logo */}
                        <div className="export-section">
                            <label className="export-label">Logo</label>
                            {logoDataUrl ? (
                                <div className="export-logo-row">
                                    <img src={logoDataUrl} alt="logo" className="export-logo-thumb" />
                                    <button type="button" className="export-legend-remove" onClick={removeLogo}>&times;</button>
                                </div>
                            ) : (
                                <button
                                    type="button"
                                    className="export-legend-add"
                                    onClick={() => logoInputRef.current?.click()}
                                >
                                    + {en ? 'Upload Logo' : '上传 Logo'}
                                </button>
                            )}
                            <input
                                ref={logoInputRef}
                                type="file"
                                accept="image/*"
                                style={{ display: 'none' }}
                                onChange={handleLogoUpload}
                            />
                        </div>

                        {/* Resolution */}
                        <div className="export-section">
                            <label className="export-label">{en ? 'Resolution' : '输出分辨率'}</label>
                            <div className="export-format-btns">
                                {Object.entries(RESOLUTIONS).map(([key]) => (
                                    <button
                                        key={key}
                                        type="button"
                                        className={exportResolution === key ? 'active' : ''}
                                        onClick={() => handleResolutionChange(key)}
                                        disabled={isCapturing}
                                        style={{ fontSize: '11px', padding: '5px 4px' }}
                                    >
                                        {key.replace('x', '×')}
                                    </button>
                                ))}
                            </div>
                        </div>

                        {/* Format */}
                        <div className="export-section">
                            <label className="export-label">{en ? 'Format' : '格式'}</label>
                            <div className="export-format-btns">
                                <button
                                    type="button"
                                    className={exportFormat === 'png' ? 'active' : ''}
                                    onClick={() => setExportFormat('png')}
                                >PNG</button>
                                <button
                                    type="button"
                                    className={exportFormat === 'jpeg' ? 'active' : ''}
                                    onClick={() => setExportFormat('jpeg')}
                                >JPEG</button>
                            </div>
                        </div>

                        {/* Toggles */}
                        <div className="export-section">
                            <label className="export-label">{en ? 'Elements' : '地图要素'}</label>
                            <div className="export-toggles">
                                <label className="export-toggle">
                                    <input type="checkbox" checked={showLegend} onChange={(e) => setShowLegend(e.target.checked)} />
                                    <span>{en ? 'Legend' : '图例'}</span>
                                </label>
                                <label className="export-toggle">
                                    <input type="checkbox" checked={showScaleBar} onChange={(e) => setShowScaleBar(e.target.checked)} />
                                    <span>{en ? 'Scale Bar' : '比例尺'}</span>
                                </label>
                                <label className="export-toggle">
                                    <input type="checkbox" checked={showNorthArrow} onChange={(e) => setShowNorthArrow(e.target.checked)} />
                                    <span>{en ? 'North Arrow' : '指北针'}</span>
                                </label>
                            </div>
                        </div>

                        {/* Legend editor */}
                        {showLegend && (
                            <div className="export-section">
                                <label className="export-label">{en ? 'Legend Items' : '图例项'}</label>
                                <div className="export-legend-list">
                                    {legendItems.map((item) => (
                                        <div key={item.id} className="export-legend-item">
                                            {item.type === 'colorbar' ? (
                                                <div className="export-legend-colorbar-preview" title={en ? 'D-InSAR colormap' : 'D-InSAR 色表'} />
                                            ) : (
                                                <input
                                                    type="color"
                                                    value={item.color}
                                                    onChange={(e) => updateLegendItem(item.id, { color: e.target.value })}
                                                    className="export-legend-color"
                                                />
                                            )}
                                            <input
                                                type="text"
                                                value={item.label}
                                                onChange={(e) => updateLegendItem(item.id, { label: e.target.value })}
                                                className="export-legend-label-input"
                                            />
                                            <button
                                                type="button"
                                                className="export-legend-remove"
                                                onClick={() => removeLegendItem(item.id)}
                                                title={en ? 'Remove' : '删除'}
                                            >&times;</button>
                                        </div>
                                    ))}
                                    {legendItems.length === 0 && (
                                        <div className="export-legend-empty">
                                            {en ? 'No visible layers detected' : '未检测到可见图层'}
                                        </div>
                                    )}
                                </div>
                                <button type="button" className="export-legend-add" onClick={addLegendItem}>
                                    + {en ? 'Add Item' : '添加图例'}
                                </button>
                            </div>
                        )}
                    </div>

                    {/* Right preview */}
                    <div className="export-preview">
                        {isCapturing ? (
                            <div className="export-preview-loading">
                                {en ? 'Capturing map...' : '正在截取地图...'}
                            </div>
                        ) : previewUrl ? (
                            <img src={previewUrl} alt="preview" className="export-preview-img" />
                        ) : (
                            <div className="export-preview-loading">
                                {en ? 'No preview' : '无预览'}
                            </div>
                        )}
                    </div>
                </div>

                <div className="export-footer">
                    <button type="button" className="export-btn-cancel" onClick={closeExportModal}>
                        {en ? 'Cancel' : '取消'}
                    </button>
                    <button
                        type="button"
                        className="export-btn-submit"
                        onClick={executeExport}
                        disabled={isExporting || isCapturing}
                    >
                        {isExporting
                            ? (en ? 'Exporting...' : '导出中...')
                            : (en ? 'Export' : '导出')}
                    </button>
                </div>
            </div>
        </div>
    );
}
