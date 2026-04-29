import { usePairingStore, useUiStore, useAuthStore } from '../store';
import { getSelectedRegionTreeId } from '../utils/appUiHelpers';

const PARAM_METADATA = {
    initial_overlap_threshold: {
        label: '单景 AOI 覆盖率',
        title: '单景影像覆盖 AOI 的最低比例。0.30 表示影像至少覆盖 AOI 面积的 30%。',
        hint: '先过滤明显不覆盖研究区的影像；AOI 很大时可适当降低。',
    },
    final_overlap_threshold: {
        label: '栈覆盖一致性',
        title: '最终候选栈的公共覆盖区 / 栈内最小单景 AOI 覆盖区。0.95 表示栈内场景覆盖范围基本一致，不要求覆盖整个行政区。',
        hint: '控制时序栈内部覆盖稳定性；若同轨同模式影像仍被过滤，可尝试 0.85-0.90。',
    },
};

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
                <h3>准备时序InSAR候选栈</h3>
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
                            <label title={PARAM_METADATA[key]?.title || key}>
                                {PARAM_METADATA[key]?.label || key.replace(/_/g, ' ')}:
                            </label>
                            <input
                                type="number"
                                step="0.01"
                                min="0"
                                max="1"
                                value={value}
                                onChange={e => setPsParams({...psParams, [key]: parseFloat(e.target.value)})}
                            />
                            {PARAM_METADATA[key]?.hint && (
                                <div style={{ marginTop: '4px', fontSize: '12px', color: '#6b7280', lineHeight: 1.4 }}>
                                    {PARAM_METADATA[key].hint}
                                </div>
                            )}
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
