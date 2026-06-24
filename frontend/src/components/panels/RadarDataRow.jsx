import { memo } from 'react';
import { getPreviewStatusClass, getPreviewStatusText } from '../../utils/appUiHelpers';

function RadarDataRow({
    item,
    language,
    isAdmin,
    isRebuilding,
    onFlyTo,
    onShowDataInfo,
    onTogglePreview,
    onRebuildPreview,
    onToggleLayer,
}) {
    return (
        <li className="data-item radar-data-item" onClick={() => onFlyTo(item)}>
            <span className="data-item-name" title={item.displayName}>
                {item.displayName}
            </span>
            <div className="data-item-controls">
                <span
                    className={`preview-status-chip ${getPreviewStatusClass(item)}`}
                    title={item.previewMessage || item.previewError || '源影像预览缓存状态'}
                >
                    {getPreviewStatusText(item)}
                </span>
                <button
                    className="data-info-btn"
                    type="button"
                    title={language === 'en' ? 'View source data details' : '查看影像信息'}
                    onClick={(event) => onShowDataInfo(item, event)}
                >
                    {language === 'en' ? 'Info' : '详情'}
                </button>
                <button
                    className={`data-preview-btn ${item.isPreviewVisible ? 'active' : ''}`}
                    type="button"
                    title={language === 'en' ? 'Show or hide source preview on map' : '在地图上显示/隐藏源影像缓存'}
                    onClick={(event) => {
                        event.stopPropagation();
                        onTogglePreview(item.id);
                    }}
                >
                    {language === 'en' ? 'Preview' : '影像'}
                </button>
                {isAdmin && (
                    <button
                        className="data-rebuild-btn"
                        type="button"
                        title={language === 'en' ? 'Rebuild source preview cache' : '管理员重建源影像预览缓存'}
                        disabled={isRebuilding}
                        onClick={(event) => onRebuildPreview(item.id, event)}
                    >
                        {isRebuilding
                            ? (language === 'en' ? 'Rebuilding' : '重建中')
                            : (language === 'en' ? 'Rebuild' : '重建')}
                    </button>
                )}
                <span
                    className={`orbit-status ${item.has_orbit_data ? 'has-orbit' : ''}`}
                    title={item.has_orbit_data
                        ? (language === 'en' ? 'Precise orbit available' : '有精轨')
                        : (language === 'en' ? 'Precise orbit unavailable' : '无精轨')}
                >
                    ●
                </span>
                <input
                    type="checkbox"
                    checked={item.isVisible}
                    onChange={() => onToggleLayer(item.id)}
                    onClick={(event) => event.stopPropagation()}
                    title={language === 'en' ? 'Show or hide on map' : '在地图上显示/隐藏'}
                />
            </div>
        </li>
    );
}

export default memo(RadarDataRow);
