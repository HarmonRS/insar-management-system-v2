import { useRef, useState, useEffect } from 'react';
import { usePairingStore, useRadarStore, useAuthStore } from '../store';
import { useI18n } from '../i18n/I18nContext';
import UnifiedDatePicker from './UnifiedDatePicker';
import { getSelectedRegionTreeId } from '../utils/appUiHelpers';
import { getAvailableSatellites } from '../api/radar';

// 配对策略说明
const STRATEGY_DESCRIPTIONS = {
    all: {
        title: '全部配对（默认）',
        description: '列出所有满足约束条件的候选干涉对，由用户自行筛选。',
        details: [
            '• 系统遍历所有影像组合，保留满足时间基线、空间基线和重叠率阈值的配对',
            '• 结果按时间排序，用户可在配对列表中逐一勾选或取消',
            '• 适用于研究型场景，需要精确控制每一对干涉组合',
            '• 配对数量可能较多，建议配合 AOI 和日期范围缩小结果'
        ],
        params: '参数：时间基线范围、空间基线上限、最小重叠率'
    },
    sbas: {
        title: 'SBAS (短基线子集)',
        description: '基于短基线原则的配对策略，通过覆盖优化算法自动筛选配对。',
        details: [
            '• 优先选择时间和空间基线都较短的配对',
            '• 通过覆盖优化算法，去除冗余配对，确保时间序列连续性',
            '• 适用于大范围、长时间序列的形变监测',
            '• 配对数量会比"全部配对"少，但覆盖更均匀'
        ],
        params: '参数：时间基线、空间基线、重叠率阈值、覆盖多样性惩罚'
    },
    sequential: {
        title: 'Sequential (顺序配对)',
        description: '每个影像与后续 N 个影像配对，形成时间序列链。',
        details: [
            '• 按时间顺序连接影像，形成连续的干涉链',
            '• 连接数可调（1-10），数值越大配对越密集',
            '• 适用于快速形变监测和时序分析',
            '• 计算效率高，配对数量可控'
        ],
        params: '参数：连接数（每个影像连接的后续影像数）'
    },
    star: {
        title: 'Star (星型配对)',
        description: '所有影像与一个参考影像配对，形成星型结构。',
        details: [
            '• 选择一个高质量影像作为参考（通常选时间居中的影像）',
            '• 所有其他影像都与参考影像配对',
            '• 适用于单次事件监测（如地震、滑坡）',
            '• 便于差分结果的直接对比'
        ],
        params: '参数：参考影像（不指定则自动选择时间居中的影像）'
    }
};

function PairingModal({
    onSubmit,
    onAoiModeChange,
    onProvinceChange,
    onCityChange,
}) {
    const { language } = useI18n();

    const {
        pairingParams, setPairingParams,
        pairingAoiMode,
        showPairingModal, setShowPairingModal,
        pairingFiles, setPairingFiles,
        pairingRegionOptions,
        pairingRegionSelection,
        pairingRegionLoading,
        pairingRegionError, setPairingRegionError,
    } = usePairingStore();

    const { radarImagingDates, allData } = useRadarStore();
    const { currentUser } = useAuthStore();
    const isReadOnlyUser = !!currentUser && currentUser.role !== 'admin';

    const requireOrbitRef = useRef(null);
    const [availableSatellites, setAvailableSatellites] = useState([]);
    const [selectedSatellites, setSelectedSatellites] = useState(pairingParams.allowed_satellites || []);
    const [referenceImageOptions, setReferenceImageOptions] = useState([]);

    // 获取可用日期列表（用于日期选择器）
    const availableDates = (
        radarImagingDates.length > 0
            ? radarImagingDates
            : [...new Set(allData.map(item => item.imaging_date))].sort()
    ).filter(Boolean);

    // 加载可用卫星列表
    useEffect(() => {
        if (showPairingModal) {
            getAvailableSatellites()
                .then(data => {
                    setAvailableSatellites(data.satellites || []);
                })
                .catch(err => {
                    console.error('Failed to load satellites:', err);
                });
        }
    }, [showPairingModal]);

    // 同步 selectedSatellites 到 pairingParams
    useEffect(() => {
        if (selectedSatellites.length > 0) {
            setPairingParams(prev => ({ ...prev, allowed_satellites: selectedSatellites }));
        } else {
            setPairingParams(prev => ({ ...prev, allowed_satellites: null }));
        }
    }, [selectedSatellites, setPairingParams]);

    // 生成参考影像选项（用于 Star 策略）
    useEffect(() => {
        if (showPairingModal && allData.length > 0) {
            const options = allData.map(item => ({
                id: item.id,
                label: (item.file_path || '').split(/[\\/]/).pop() || `ID_${item.id}`,
                date: item.imaging_date
            })).sort((a, b) => a.date.localeCompare(b.date));
            setReferenceImageOptions(options);
        }
    }, [showPairingModal, allData]);

    const handleSatelliteToggle = (satellite) => {
        setSelectedSatellites(prev => {
            if (prev.includes(satellite)) {
                return prev.filter(s => s !== satellite);
            } else {
                return [...prev, satellite];
            }
        });
    };

    const handleSubmit = (e) => {
        e.preventDefault();
        onSubmit(e, requireOrbitRef);
    };

    if (!showPairingModal) return null;

    const currentStrategy = STRATEGY_DESCRIPTIONS[pairingParams.strategy] || STRATEGY_DESCRIPTIONS.sbas;

    return (
        <div className="modal-overlay visible">
            <div className="modal-content pairing-modal-wide">
                <div className="pairing-modal-layout">
                    {/* 左侧：参数表单 */}
                    <div className="pairing-modal-form">
                        <h3>D-InSAR 配对参数</h3>
                        <form onSubmit={handleSubmit}>
                            {/* 配对策略选择 */}
                            <div className="form-group">
                                <label>配对策略:</label>
                                <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap' }}>
                                    <label style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                                        <input
                                            type="radio"
                                            name="strategy"
                                            value="all"
                                            checked={pairingParams.strategy === 'all'}
                                            onChange={(e) => setPairingParams({ ...pairingParams, strategy: e.target.value })}
                                        />
                                        全部配对
                                    </label>
                                    <label style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                                        <input
                                            type="radio"
                                            name="strategy"
                                            value="sbas"
                                            checked={pairingParams.strategy === 'sbas'}
                                            onChange={(e) => setPairingParams({ ...pairingParams, strategy: e.target.value })}
                                        />
                                        SBAS (短基线)
                                    </label>
                                    <label style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                                        <input
                                            type="radio"
                                            name="strategy"
                                            value="sequential"
                                            checked={pairingParams.strategy === 'sequential'}
                                            onChange={(e) => setPairingParams({ ...pairingParams, strategy: e.target.value })}
                                        />
                                        Sequential (顺序)
                                    </label>
                                    <label style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                                        <input
                                            type="radio"
                                            name="strategy"
                                            value="star"
                                            checked={pairingParams.strategy === 'star'}
                                            onChange={(e) => setPairingParams({ ...pairingParams, strategy: e.target.value })}
                                        />
                                        Star (星型)
                                    </label>
                                </div>
                            </div>

                            {/* 主影像时间范围 */}
                            <div className="form-group">
                                <label>主影像时间范围:</label>
                                <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                                    <UnifiedDatePicker
                                        value={pairingParams.master_date_from || ''}
                                        onChange={(value) => setPairingParams({ ...pairingParams, master_date_from: value ? value.replace(/-/g, '') : null })}
                                        language={language}
                                        placeholder="起始日期"
                                        enabledDates={availableDates}
                                        allowClear={true}
                                    />
                                    <span>至</span>
                                    <UnifiedDatePicker
                                        value={pairingParams.master_date_to || ''}
                                        onChange={(value) => setPairingParams({ ...pairingParams, master_date_to: value ? value.replace(/-/g, '') : null })}
                                        language={language}
                                        placeholder="结束日期"
                                        enabledDates={availableDates}
                                        allowClear={true}
                                    />
                                </div>
                            </div>

                            {/* 从影像时间范围 */}
                            <div className="form-group">
                                <label>从影像时间范围:</label>
                                <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                                    <UnifiedDatePicker
                                        value={pairingParams.slave_date_from || ''}
                                        onChange={(value) => setPairingParams({ ...pairingParams, slave_date_from: value ? value.replace(/-/g, '') : null })}
                                        language={language}
                                        placeholder="起始日期"
                                        enabledDates={availableDates}
                                        allowClear={true}
                                    />
                                    <span>至</span>
                                    <UnifiedDatePicker
                                        value={pairingParams.slave_date_to || ''}
                                        onChange={(value) => setPairingParams({ ...pairingParams, slave_date_to: value ? value.replace(/-/g, '') : null })}
                                        language={language}
                                        placeholder="结束日期"
                                        enabledDates={availableDates}
                                        allowClear={true}
                                    />
                                </div>
                            </div>

                            {/* Star 策略专用：参考影像 */}
                            {pairingParams.strategy === 'star' && (
                                <div className="form-group">
                                    <label>参考影像 (Star 策略中心影像):</label>
                                    <select
                                        value={pairingParams.reference_image_id || ''}
                                        onChange={(e) => setPairingParams({ ...pairingParams, reference_image_id: e.target.value ? parseInt(e.target.value) : null })}
                                        style={{ width: '100%' }}
                                    >
                                        <option value="">-- 自动选择时间居中的影像 --</option>
                                        {referenceImageOptions.map(opt => (
                                            <option key={opt.id} value={opt.id}>{opt.label}</option>
                                        ))}
                                    </select>
                                </div>
                            )}

                            {/* Sequential 策略专用：连接数 */}
                            {pairingParams.strategy === 'sequential' && (
                                <div className="form-group">
                                    <label>连接数 (1-10):</label>
                                    <input
                                        type="number"
                                        min="1"
                                        max="10"
                                        value={pairingParams.num_connections || 1}
                                        onChange={(e) => setPairingParams({ ...pairingParams, num_connections: e.target.value ? parseInt(e.target.value) : 1 })}
                                        placeholder="每个影像连接的后续影像数"
                                    />
                                </div>
                            )}

                    {/* 卫星选择器 */}
                    {availableSatellites.length > 0 && (
                        <div className="form-group">
                            <label>限定卫星 (不选则不限制):</label>
                            <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
                                {availableSatellites.map(sat => (
                                    <label key={sat} style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                                        <input
                                            type="checkbox"
                                            checked={selectedSatellites.includes(sat)}
                                            onChange={() => handleSatelliteToggle(sat)}
                                        />
                                        {sat}
                                    </label>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* 跨卫星配对选项 */}
                    {selectedSatellites.length > 1 && (
                        <div className="form-group checkbox-group">
                            <input
                                type="checkbox"
                                id="cross-satellite-pairing"
                                checked={pairingParams.cross_satellite_pairing}
                                onChange={e => setPairingParams({
                                    ...pairingParams,
                                    cross_satellite_pairing: e.target.checked
                                })}
                            />
                            <label htmlFor="cross-satellite-pairing">允许跨卫星配对</label>
                        </div>
                    )}

                    {/* 基线和重叠率约束 */}
                    <div className="form-group">
                        <label>时间基线最小值 (天):</label>
                        <input type="number" min="0" value={pairingParams.time_baseline_min}
                            onChange={e => setPairingParams({...pairingParams, time_baseline_min: parseInt(e.target.value) || 0})} />
                    </div>
                    <div className="form-group">
                        <label>时间基线最大值 (天):</label>
                        <input type="number" min="1" value={pairingParams.time_baseline_max}
                            onChange={e => setPairingParams({...pairingParams, time_baseline_max: parseInt(e.target.value) || 90})} />
                    </div>
                    <div className="form-group">
                        <label>最小重叠率 (0-1):</label>
                        <input type="number" step="0.1" min="0" max="1" value={pairingParams.overlap_threshold}
                            onChange={e => setPairingParams({...pairingParams, overlap_threshold: parseFloat(e.target.value) || 0})} />
                    </div>
                    <div className="form-group">
                        <label>空间基线上限 (米):</label>
                        <input type="number" min="0" value={pairingParams.spatial_baseline_max_meters}
                            onChange={e => setPairingParams({...pairingParams, spatial_baseline_max_meters: parseInt(e.target.value) || 3000})} />
                    </div>
                    <div className="form-group">
                        <label>覆盖多样性惩罚 (0-1):</label>
                        <input type="number" step="0.1" min="0" max="1"
                            value={pairingParams.coverage_diversity_penalty}
                            onChange={e => setPairingParams({...pairingParams, coverage_diversity_penalty: parseFloat(e.target.value) || 0})}
                            disabled={pairingParams.strategy !== 'sbas'}
                        />
                        {pairingParams.strategy !== 'sbas' && (
                            <div style={{ fontSize: '12px', color: '#6b7280', marginTop: '4px' }}>仅 SBAS 策略使用此参数</div>
                        )}
                    </div>
                    <div className="form-group">
                        <label>AOI 来源:</label>
                        <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap' }}>
                            <label style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                                <input
                                    type="radio"
                                    name="pairing-aoi-mode"
                                    value="shp"
                                    checked={pairingAoiMode === 'shp'}
                                    onChange={() => onAoiModeChange('shp')}
                                />
                                上传SHP
                            </label>
                            <label style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                                <input
                                    type="radio"
                                    name="pairing-aoi-mode"
                                    value="region"
                                    checked={pairingAoiMode === 'region'}
                                    onChange={() => onAoiModeChange('region')}
                                />
                                行政区选择
                            </label>
                        </div>
                    </div>
                    {pairingAoiMode === 'shp' ? (
                        <div className="form-group">
                            <label>限定范围 (Shapefile，可选):</label>
                            <input
                                type="file"
                                multiple
                                onChange={e => setPairingFiles(e.target.files)}
                                style={{ display: 'none' }}
                                id="shp-upload"
                            />
                            <label htmlFor="shp-upload" className="file-upload-button">
                                选择文件...
                            </label>
                            {pairingFiles && pairingFiles.length > 0 && (
                                <div className="file-list">
                                    {Array.from(pairingFiles).map(f => f.name).join(', ')}
                                </div>
                            )}
                        </div>
                    ) : (
                        <div className="form-group">
                            <label>行政区范围:</label>
                            <div className="aoi-region-select-grid">
                                <select
                                    value={pairingRegionSelection.province}
                                    onChange={(e) => onProvinceChange(e.target.value)}
                                    disabled={pairingRegionLoading}
                                >
                                    <option value="">-- 省级 --</option>
                                    {pairingRegionOptions.provinces.map(item => (
                                        <option key={item.tree_id} value={item.tree_id}>{item.name}</option>
                                    ))}
                                </select>
                                <select
                                    value={pairingRegionSelection.city}
                                    onChange={(e) => onCityChange(e.target.value)}
                                    disabled={pairingRegionLoading || !pairingRegionSelection.province}
                                >
                                    <option value="">-- 地市 --</option>
                                    {pairingRegionOptions.cities.map(item => (
                                        <option key={item.tree_id} value={item.tree_id}>{item.name}</option>
                                    ))}
                                </select>
                            </div>
                            <div style={{ marginTop: '6px', fontSize: '12px', color: '#6b7280' }}>
                                可只选到省/市级，系统将自动使用当前选中层级边界。
                            </div>
                            {pairingRegionError && (
                                <div style={{ marginTop: '6px', color: '#b91c1c', fontSize: '12px' }}>
                                    {pairingRegionError}
                                </div>
                            )}
                        </div>
                    )}
                    <div className="form-group">
                        <label>AOI 覆盖率阈值 (0 表示不限制):</label>
                        <input
                            type="number"
                            step="0.01"
                            min="0"
                            max="1"
                            value={pairingParams.aoi_overlap_threshold}
                            onChange={e => setPairingParams({
                                ...pairingParams,
                                aoi_overlap_threshold: parseFloat(e.target.value) || 0
                            })}
                        />
                    </div>
                    <div className="form-group checkbox-group">
                        <input
                            type="checkbox"
                            id="require-imaging-mode"
                            checked={pairingParams.require_same_imaging_mode}
                            onChange={e => setPairingParams({
                                ...pairingParams,
                                require_same_imaging_mode: e.target.checked
                            })}
                        />
                        <label htmlFor="require-imaging-mode">成像模式一致</label>
                    </div>
                    <div className="form-group checkbox-group">
                        <input
                            type="checkbox"
                            id="require-polarization"
                            checked={pairingParams.require_same_polarization}
                            onChange={e => setPairingParams({
                                ...pairingParams,
                                require_same_polarization: e.target.checked
                            })}
                        />
                        <label htmlFor="require-polarization">极化一致</label>
                    </div>
                    <div className="form-group checkbox-group">
                        <input
                            type="checkbox"
                            id="require-orbit"
                            ref={requireOrbitRef}
                            defaultChecked={true}
                        />
                        <label htmlFor="require-orbit">仅使用有精轨数据的影像</label>
                    </div>
                    <div className="modal-actions">
                        <button type="button" onClick={() => { setShowPairingModal(false); setPairingFiles(null); setPairingRegionError(''); }}>取消</button>
                        <button
                            type="submit"
                            disabled={isReadOnlyUser || (pairingAoiMode === 'region' && !getSelectedRegionTreeId(pairingRegionSelection))}
                        >
                            开始配对
                        </button>
                    </div>
                </form>
            </div>

            {/* 右侧：策略介绍面板 */}
            <div className="pairing-modal-info">
                <div className="strategy-info-panel">
                    <h4>{currentStrategy.title}</h4>
                    <p className="strategy-description">{currentStrategy.description}</p>
                    <div className="strategy-details">
                        {currentStrategy.details.map((detail, idx) => (
                            <p key={idx}>{detail}</p>
                        ))}
                    </div>
                    <div className="strategy-params">
                        <strong>配置参数：</strong>
                        <p>{currentStrategy.params}</p>
                    </div>
                </div>
            </div>
        </div>
    </div>
</div>
    );
}

export default PairingModal;
