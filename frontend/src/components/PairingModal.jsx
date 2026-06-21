import { useEffect, useState } from 'react';
import { usePairingStore, useRadarStore, useAuthStore } from '../store';
import { useI18n } from '../i18n/I18nContext';
import UnifiedDatePicker from './UnifiedDatePicker';
import { getSelectedRegionTreeId } from '../utils/appUiHelpers';

const SENSOR_FAMILIES = [
    { value: 'LT1', label: 'LT-1' },
    { value: 'S1', label: 'Sentinel-1' },
];

const PAIRING_CENTER_DISTANCE_MAX_METERS = 20000000;

const inputValue = (value) => value ?? '';

const parseNumericField = (params, key, label, { integer = false, min = -Infinity, max = Infinity } = {}) => {
    const rawValue = String(params[key] ?? '').trim();
    if (rawValue === '') {
        return { error: `${label}不能为空。` };
    }
    const parsed = Number(rawValue);
    if (!Number.isFinite(parsed) || (integer && !Number.isInteger(parsed))) {
        return { error: `${label}必须是${integer ? '整数' : '数字'}。` };
    }
    if (parsed < min || parsed > max) {
        return { error: `${label}必须在 ${min} 到 ${max} 之间。` };
    }
    return { value: parsed };
};

const normalizePairingParamsForSubmit = (params) => {
    const timeMin = parseNumericField(params, 'time_baseline_min', '最小时间基线', { integer: true, min: 0, max: 3650 });
    if (timeMin.error) return timeMin;
    const timeMax = parseNumericField(params, 'time_baseline_max', '最大时间基线', { integer: true, min: 1, max: 3650 });
    if (timeMax.error) return timeMax;
    if (timeMin.value > timeMax.value) {
        return { error: '最小时间基线不能大于最大时间基线。' };
    }
    const overlap = parseNumericField(params, 'overlap_threshold', '两景最小重叠率', { min: 0, max: 1 });
    if (overlap.error) return overlap;
    const centerDistance = parseNumericField(params, 'spatial_baseline_max_meters', 'footprint 中心距离上限', {
        integer: true,
        min: 0,
        max: PAIRING_CENTER_DISTANCE_MAX_METERS,
    });
    if (centerDistance.error) return centerDistance;
    const aoiOverlap = parseNumericField(params, 'aoi_overlap_threshold', 'AOI 覆盖率阈值', { min: 0, max: 1 });
    if (aoiOverlap.error) return aoiOverlap;

    return {
        value: {
            ...params,
            time_baseline_min: timeMin.value,
            time_baseline_max: timeMax.value,
            overlap_threshold: overlap.value,
            spatial_baseline_max_meters: centerDistance.value,
            aoi_overlap_threshold: aoiOverlap.value,
        },
    };
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
    const [selectedFamilies, setSelectedFamilies] = useState(pairingParams.allowed_satellites || []);

    const availableDates = (
        radarImagingDates.length > 0
            ? radarImagingDates
            : [...new Set(allData.map(item => item.imaging_date))].sort()
    ).filter(Boolean);

    useEffect(() => {
        setPairingParams(prev => ({
            ...prev,
            strategy: 'dinsar_production',
            time_baseline_max: prev.time_baseline_max ?? 30,
            spatial_baseline_max_meters: prev.spatial_baseline_max_meters ?? 5000,
            limit_footprint_center_distance: true,
            cross_satellite_pairing: false,
            allowed_satellites: selectedFamilies.length > 0 ? selectedFamilies : null,
        }));
    }, [selectedFamilies, setPairingParams]);

    const updateParam = (patch) => setPairingParams({
        ...pairingParams,
        ...patch,
        strategy: 'dinsar_production',
        cross_satellite_pairing: false,
    });

    const toggleFamily = (family) => {
        setSelectedFamilies(prev => (
            prev.includes(family)
                ? prev.filter(item => item !== family)
                : [...prev, family]
        ));
    };

    const handleSubmit = (event) => {
        event.preventDefault();
        if (isReadOnlyUser) {
            setPairingRegionError('当前账号为只读用户，不能执行配对。');
            return;
        }
        setPairingRegionError('');
        const normalized = normalizePairingParamsForSubmit(pairingParams);
        if (normalized.error) {
            setPairingRegionError(normalized.error);
            return;
        }
        const submitParams = {
            ...normalized.value,
            strategy: 'dinsar_production',
            limit_footprint_center_distance: true,
            cross_satellite_pairing: false,
            allowed_satellites: selectedFamilies.length > 0 ? selectedFamilies : null,
        };
        setPairingParams(submitParams);
        const requireOrbitRef = { current: { checked: true } };
        onSubmit(event, requireOrbitRef, submitParams);
    };

    if (!showPairingModal) return null;

    return (
        <div className="modal-overlay visible">
            <div className="modal-content pairing-modal-wide">
                <div className="pairing-modal-layout">
                    <div className="pairing-modal-form">
                        <h3>D-InSAR 生产配对</h3>
                        <form onSubmit={handleSubmit} noValidate>
                            <div className="form-group">
                                <label>主影像时间范围</label>
                                <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                                    <UnifiedDatePicker
                                        value={pairingParams.master_date_from || ''}
                                        onChange={(value) => updateParam({ master_date_from: value ? value.replace(/-/g, '') : null })}
                                        language={language}
                                        placeholder="开始日期"
                                        enabledDates={availableDates}
                                        allowClear
                                    />
                                    <span>至</span>
                                    <UnifiedDatePicker
                                        value={pairingParams.master_date_to || ''}
                                        onChange={(value) => updateParam({ master_date_to: value ? value.replace(/-/g, '') : null })}
                                        language={language}
                                        placeholder="结束日期"
                                        enabledDates={availableDates}
                                        allowClear
                                    />
                                </div>
                            </div>

                            <div className="form-group">
                                <label>从影像时间范围</label>
                                <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                                    <UnifiedDatePicker
                                        value={pairingParams.slave_date_from || ''}
                                        onChange={(value) => updateParam({ slave_date_from: value ? value.replace(/-/g, '') : null })}
                                        language={language}
                                        placeholder="开始日期"
                                        enabledDates={availableDates}
                                        allowClear
                                    />
                                    <span>至</span>
                                    <UnifiedDatePicker
                                        value={pairingParams.slave_date_to || ''}
                                        onChange={(value) => updateParam({ slave_date_to: value ? value.replace(/-/g, '') : null })}
                                        language={language}
                                        placeholder="结束日期"
                                        enabledDates={availableDates}
                                        allowClear
                                    />
                                </div>
                            </div>

                            <div className="form-group">
                                <label>限定数据体系</label>
                                <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
                                    {SENSOR_FAMILIES.map(item => (
                                        <label key={item.value} style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                                            <input
                                                type="checkbox"
                                                checked={selectedFamilies.includes(item.value)}
                                                onChange={() => toggleFamily(item.value)}
                                            />
                                            {item.label}
                                        </label>
                                    ))}
                                </div>
                            </div>

                            <div className="form-group">
                                <label>最小时间基线（天）</label>
                                <input
                                    type="number"
                                    min="0"
                                    max="3650"
                                    value={inputValue(pairingParams.time_baseline_min)}
                                    onChange={e => updateParam({ time_baseline_min: e.target.value })}
                                />
                            </div>
                            <div className="form-group">
                                <label>最大时间基线（天，默认 30，可修改）</label>
                                <input
                                    type="number"
                                    min="1"
                                    max="3650"
                                    value={inputValue(pairingParams.time_baseline_max)}
                                    onChange={e => updateParam({ time_baseline_max: e.target.value })}
                                />
                            </div>
                            <div className="form-group">
                                <label>两景最小重叠率</label>
                                <input
                                    type="number"
                                    step="0.05"
                                    min="0"
                                    max="1"
                                    value={inputValue(pairingParams.overlap_threshold)}
                                    onChange={e => updateParam({ overlap_threshold: e.target.value })}
                                />
                            </div>
                            <div className="form-group">
                                <label>footprint 中心距离上限（米）</label>
                                <input
                                    type="number"
                                    min="0"
                                    max={PAIRING_CENTER_DISTANCE_MAX_METERS}
                                    value={inputValue(pairingParams.spatial_baseline_max_meters)}
                                    onChange={e => updateParam({ spatial_baseline_max_meters: e.target.value })}
                                />
                            </div>

                            <div className="form-group">
                                <label>AOI 来源（可选）</label>
                                <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap' }}>
                                    <label style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                                        <input
                                            type="radio"
                                            name="pairing-aoi-mode"
                                            value="shp"
                                            checked={pairingAoiMode === 'shp'}
                                            onChange={() => onAoiModeChange('shp')}
                                        />
                                        上传 SHP
                                    </label>
                                    <label style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                                        <input
                                            type="radio"
                                            name="pairing-aoi-mode"
                                            value="region"
                                            checked={pairingAoiMode === 'region'}
                                            onChange={() => onAoiModeChange('region')}
                                        />
                                        行政区
                                    </label>
                                </div>
                            </div>

                            {pairingAoiMode === 'shp' ? (
                                <div className="form-group">
                                    <label>限定范围（可选）</label>
                                    <input
                                        type="file"
                                        multiple
                                        onChange={e => setPairingFiles(e.target.files)}
                                        style={{ display: 'none' }}
                                        id="shp-upload"
                                    />
                                    <label htmlFor="shp-upload" className="file-upload-button">选择文件...</label>
                                    {pairingFiles && pairingFiles.length > 0 && (
                                        <div className="file-list">
                                            {Array.from(pairingFiles).map(file => file.name).join(', ')}
                                        </div>
                                    )}
                                </div>
                            ) : (
                                <div className="form-group">
                                    <label>行政区范围</label>
                                    <div className="aoi-region-select-grid">
                                        <select
                                            value={pairingRegionSelection.province}
                                            onChange={(event) => onProvinceChange(event.target.value)}
                                            disabled={pairingRegionLoading}
                                        >
                                            <option value="">-- 省级 --</option>
                                            {pairingRegionOptions.provinces.map(item => (
                                                <option key={item.tree_id} value={item.tree_id}>{item.name}</option>
                                            ))}
                                        </select>
                                        <select
                                            value={pairingRegionSelection.city}
                                            onChange={(event) => onCityChange(event.target.value)}
                                            disabled={pairingRegionLoading || !pairingRegionSelection.province}
                                        >
                                            <option value="">-- 地市 --</option>
                                            {pairingRegionOptions.cities.map(item => (
                                                <option key={item.tree_id} value={item.tree_id}>{item.name}</option>
                                            ))}
                                        </select>
                                    </div>
                                </div>
                            )}

                            {pairingRegionError && (
                                <div style={{ marginBottom: '10px', color: '#b91c1c', fontSize: '12px', whiteSpace: 'pre-line' }}>
                                    {pairingRegionError}
                                </div>
                            )}

                            <div className="form-group">
                                <label>AOI 覆盖率阈值（未选 AOI 时不生效）</label>
                                <input
                                    type="number"
                                    step="0.01"
                                    min="0"
                                    max="1"
                                    value={inputValue(pairingParams.aoi_overlap_threshold)}
                                    onChange={e => updateParam({ aoi_overlap_threshold: e.target.value })}
                                />
                            </div>

                            <div className="modal-actions">
                                <button
                                    type="button"
                                    onClick={() => {
                                        setShowPairingModal(false);
                                        setPairingFiles(null);
                                        setPairingRegionError('');
                                    }}
                                >
                                    取消
                                </button>
                                <button type="submit">
                                    开始配对
                                </button>
                            </div>
                        </form>
                    </div>

                    <div className="pairing-modal-info">
                        <div className="strategy-info-panel">
                            <h4>单一生产配对规则</h4>
                            <p className="strategy-description">
                                GF3 不参与 D-InSAR 配对；LT-1 只和 LT-1 配对，LT1A/LT1B 可互配；Sentinel-1 只和 Sentinel-1 配对。
                            </p>
                            <div className="strategy-details">
                                <p>A 级：相对轨道一致且中心距离较小，优先生产。</p>
                                <p>B 级：相对轨道缺失但其他几何条件满足，可生产但需关注配准质量。</p>
                                <p>C 级：仅作为候选，不建议直接批量生产。</p>
                            </div>
                            <div className="strategy-params">
                                <strong>AOI 规则：</strong>
                                <p>未选择 AOI 时按全库条件配对；选择 AOI 后使用两景重叠区与 AOI 的交集筛选。</p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}

export default PairingModal;
