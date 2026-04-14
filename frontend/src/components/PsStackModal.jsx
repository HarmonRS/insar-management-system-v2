import { usePairingStore, useUiStore, useAuthStore } from '../store';
import { getSelectedRegionTreeId } from '../utils/appUiHelpers';

function PsStackModal({
    onSubmit,
    onAoiModeChange,
    onProvinceChange,
    onCityChange,
}) {
    const {
        showPsModal, setShowPsModal,
        psFiles, setPsFiles,
        psAoiMode,
        psRegionOptions,
        psRegionSelection,
        psRegionLoading,
        psRegionError, setPsRegionError,
        psParams, setPsParams,
    } = usePairingStore();

    const { isLoading } = useUiStore();
    const { currentUser } = useAuthStore();
    const isReadOnlyUser = !!currentUser && currentUser.role !== 'admin';

    if (!showPsModal) return null;

    return (
        <div className="modal-overlay visible">
            <div className="modal-content">
                <h3>准备PS时序数据栈</h3>
                <form onSubmit={onSubmit}>
                    <div className="form-group">
                        <label>研究区域来源:</label>
                        <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap' }}>
                            <label style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                                <input
                                    type="radio"
                                    name="ps-aoi-mode"
                                    value="shp"
                                    checked={psAoiMode === 'shp'}
                                    onChange={() => onAoiModeChange('shp')}
                                />
                                上传SHP
                            </label>
                            <label style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                                <input
                                    type="radio"
                                    name="ps-aoi-mode"
                                    value="region"
                                    checked={psAoiMode === 'region'}
                                    onChange={() => onAoiModeChange('region')}
                                />
                                行政区选择
                            </label>
                        </div>
                    </div>
                    {psAoiMode === 'shp' ? (
                        <div className="form-group">
                            <label>研究区域 (Shapefile):</label>
                            <input
                                type="file"
                                multiple
                                onChange={e => setPsFiles(e.target.files)}
                                style={{ display: 'none' }}
                                id="ps-shp-upload"
                            />
                            <label htmlFor="ps-shp-upload" className="file-upload-button">
                                选择文件...
                            </label>
                            {psFiles && psFiles.length > 0 && (
                                <div className="file-list">
                                    {Array.from(psFiles).map(f => f.name).join(', ')}
                                </div>
                            )}
                        </div>
                    ) : (
                        <div className="form-group">
                            <label>行政区范围:</label>
                            <div className="aoi-region-select-grid">
                                <select
                                    value={psRegionSelection.province}
                                    onChange={(e) => onProvinceChange(e.target.value)}
                                    disabled={psRegionLoading}
                                >
                                    <option value="">-- 省级 --</option>
                                    {psRegionOptions.provinces.map(item => (
                                        <option key={item.tree_id} value={item.tree_id}>{item.name}</option>
                                    ))}
                                </select>
                                <select
                                    value={psRegionSelection.city}
                                    onChange={(e) => onCityChange(e.target.value)}
                                    disabled={psRegionLoading || !psRegionSelection.province}
                                >
                                    <option value="">-- 地市 --</option>
                                    {psRegionOptions.cities.map(item => (
                                        <option key={item.tree_id} value={item.tree_id}>{item.name}</option>
                                    ))}
                                </select>
                            </div>
                            <div style={{ marginTop: '6px', fontSize: '12px', color: '#6b7280' }}>
                                可只选到省/市级，系统将自动使用当前选中层级边界。
                            </div>
                            {psRegionError && (
                                <div style={{ marginTop: '6px', color: '#b91c1c', fontSize: '12px' }}>
                                    {psRegionError}
                                </div>
                            )}
                        </div>
                    )}
                    {Object.entries(psParams).map(([key, value]) => (
                        <div className="form-group" key={key}>
                            <label title={
                                key === 'initial_overlap_threshold'
                                ? '影像与AOI重叠面积 / AOI面积'
                                : '影像与公共重叠区面积 / 公共重叠区面积'
                            }>
                                {key.replace(/_/g, ' ')}:
                            </label>
                            <input
                                type="number"
                                step="0.01"
                                min="0"
                                max="1"
                                value={value}
                                onChange={e => setPsParams({...psParams, [key]: parseFloat(e.target.value)})}
                            />
                        </div>
                    ))}
                    <div className="modal-actions">
                        <button type="button" onClick={() => { setShowPsModal(false); setPsFiles(null); setPsRegionError(''); }}>取消</button>
                        <button
                            type="submit"
                            disabled={
                                isLoading
                                || isReadOnlyUser
                                || (psAoiMode === 'shp'
                                    ? !psFiles || psFiles.length === 0
                                    : !getSelectedRegionTreeId(psRegionSelection))
                            }
                        >
                            {isLoading ? '处理中...' : '准备并导出'}
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
}

export default PsStackModal;
