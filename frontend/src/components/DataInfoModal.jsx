const formatDateTime = (value) => {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString();
};

const joinChannels = (value) => {
  if (Array.isArray(value)) {
    const items = value.map((item) => String(item || '').trim()).filter(Boolean);
    return items.length ? items.join(' / ') : '-';
  }
  if (value === null || value === undefined || value === '') return '-';
  return String(value);
};

const yesNo = (flag, language) => {
  if (flag) return language === 'en' ? 'Yes' : '是';
  return language === 'en' ? 'No' : '否';
};

const field = (label, value, extra = {}) => ({
  label,
  value: value === null || value === undefined || value === '' ? '-' : value,
  ...extra,
});

const createSentinelRows = (dataInfo, language, formatYmd) => {
  const metadata = dataInfo.metadata_json || {};
  const polarizationChannels = metadata.polarization_channels || metadata.manifest_polarizations;

  return [
    field(language === 'en' ? 'Satellite:' : '卫星：', dataInfo.satellite),
    field(language === 'en' ? 'Satellite Family:' : '卫星系列：', dataInfo.satellite_family),
    field(language === 'en' ? 'Source Format:' : '源格式：', dataInfo.source_format),
    field(language === 'en' ? 'Imaging Date:' : '成像日期：', formatYmd(dataInfo.imaging_date)),
    field(language === 'en' ? 'Acquisition Start:' : '采集开始：', formatDateTime(dataInfo.acquisition_start_time_utc)),
    field(language === 'en' ? 'Acquisition Stop:' : '采集结束：', formatDateTime(dataInfo.acquisition_stop_time_utc)),
    field(language === 'en' ? 'Imaging Mode:' : '成像模式：', dataInfo.imaging_mode),
    field(language === 'en' ? 'Product Type:' : '产品类型：', dataInfo.product_type),
    field(language === 'en' ? 'Product Level:' : '产品级别：', dataInfo.product_level),
    field(language === 'en' ? 'Orbit Direction:' : '轨道方向：', dataInfo.orbit_direction),
    field(language === 'en' ? 'Relative Orbit:' : '相对轨道：', dataInfo.relative_orbit),
    field(language === 'en' ? 'Absolute Orbit:' : '绝对轨道：', dataInfo.absolute_orbit),
    field(language === 'en' ? 'Polarization:' : '极化方式：', dataInfo.polarization),
    field(language === 'en' ? 'Polarization Channels:' : '极化通道：', joinChannels(polarizationChannels)),
    field(language === 'en' ? 'Datatake:' : '数据采集号：', metadata.filename_datatake),
    field(language === 'en' ? 'Scene Center Lon:' : '场景中心经度：', dataInfo.scene_center_lon),
    field(language === 'en' ? 'Scene Center Lat:' : '场景中心纬度：', dataInfo.scene_center_lat),
    field(language === 'en' ? 'Product Unique ID:' : '产品唯一ID：', dataInfo.product_unique_id, {
      valueStyle: { wordBreak: 'break-all' },
    }),
    field(language === 'en' ? 'Has Orbit:' : '有精轨：', yesNo(dataInfo.has_orbit_data, language)),
    field(language === 'en' ? 'Orbit File:' : '轨道文件：', dataInfo.orbit_file_path, {
      valueStyle: { wordBreak: 'break-all' },
    }),
  ];
};

const createDefaultRows = (dataInfo, language, formatYmd) => [
  field(language === 'en' ? 'Satellite:' : '卫星：', dataInfo.satellite),
  field(language === 'en' ? 'Satellite Mode:' : '卫星模式：', dataInfo.satellite_mode),
  field(language === 'en' ? 'Receiving Station:' : '接收站：', dataInfo.receiving_station),
  field(language === 'en' ? 'Imaging Date:' : '成像日期：', formatYmd(dataInfo.imaging_date)),
  field(language === 'en' ? 'Imaging Mode:' : '成像模式：', dataInfo.imaging_mode),
  field(language === 'en' ? 'Orbit Circle:' : '轨道圈号：', dataInfo.orbit_circle),
  field(language === 'en' ? 'Scene Center Lon:' : '场景中心经度：', dataInfo.scene_center_lon),
  field(language === 'en' ? 'Scene Center Lat:' : '场景中心纬度：', dataInfo.scene_center_lat),
  field(language === 'en' ? 'Acquisition Time:' : '采集时间：', dataInfo.acquisition_time_utc),
  field(language === 'en' ? 'Product Type:' : '产品类型：', dataInfo.product_type),
  field(language === 'en' ? 'Polarization:' : '极化方式：', dataInfo.polarization),
  field(language === 'en' ? 'Product Level:' : '产品级别：', dataInfo.product_level),
  field(language === 'en' ? 'Product Unique ID:' : '产品唯一ID：', dataInfo.product_unique_id),
  field(language === 'en' ? 'Orbit Direction:' : '轨道方向：', dataInfo.orbit_direction),
  field(language === 'en' ? 'Has Orbit:' : '有精轨：', yesNo(dataInfo.has_orbit_data, language)),
  field(language === 'en' ? 'Orbit File:' : '轨道文件：', dataInfo.orbit_file_path, {
    valueStyle: { wordBreak: 'break-all' },
  }),
];

const createRows = (dataInfo, language, formatYmd) => {
  if ((dataInfo.satellite_family || '').toUpperCase() === 'S1') {
    return createSentinelRows(dataInfo, language, formatYmd);
  }
  return createDefaultRows(dataInfo, language, formatYmd);
};

export default function DataInfoModal({
  visible,
  dataInfo,
  language,
  formatYmd,
  onClose,
}) {
  if (!visible || !dataInfo) {
    return null;
  }

  const rows = createRows(dataInfo, language, formatYmd);

  return (
    <div className="modal-overlay visible">
      <div className="modal-content">
        <h3>{language === 'en' ? 'Image Information' : '影像信息'}</h3>
        {rows.map((row) => (
          <div key={row.label} className="form-group">
            <label>{row.label}</label>
            <div style={row.valueStyle}>{row.value}</div>
          </div>
        ))}
        <div className="modal-actions">
          <button type="button" onClick={onClose}>
            {language === 'en' ? 'Close' : '关闭'}
          </button>
        </div>
      </div>
    </div>
  );
}
