const createRows = (dataInfo, language, formatYmd) => [
  { label: language === 'en' ? 'Satellite:' : '卫星：', value: dataInfo.satellite || '-' },
  { label: language === 'en' ? 'Satellite Mode:' : '卫星模式：', value: dataInfo.satellite_mode || '-' },
  { label: language === 'en' ? 'Receiving Station:' : '接收站：', value: dataInfo.receiving_station || '-' },
  { label: language === 'en' ? 'Imaging Date:' : '成像日期：', value: formatYmd(dataInfo.imaging_date) },
  { label: language === 'en' ? 'Imaging Mode:' : '成像模式：', value: dataInfo.imaging_mode || '-' },
  { label: language === 'en' ? 'Orbit Circle:' : '轨道圈号：', value: dataInfo.orbit_circle || '-' },
  { label: language === 'en' ? 'Scene Center Lon:' : '场景中心经度：', value: dataInfo.scene_center_lon ?? '-' },
  { label: language === 'en' ? 'Scene Center Lat:' : '场景中心纬度：', value: dataInfo.scene_center_lat ?? '-' },
  { label: language === 'en' ? 'Acquisition Time:' : '采集时间：', value: dataInfo.acquisition_time_utc || '-' },
  { label: language === 'en' ? 'Product Type:' : '产品类型：', value: dataInfo.product_type || '-' },
  { label: language === 'en' ? 'Polarization:' : '极化方式：', value: dataInfo.polarization || '-' },
  { label: language === 'en' ? 'Product Level:' : '产品级别：', value: dataInfo.product_level || '-' },
  { label: language === 'en' ? 'Product Unique ID:' : '产品唯一ID：', value: dataInfo.product_unique_id || '-' },
  { label: language === 'en' ? 'Orbit Direction:' : '轨道方向：', value: dataInfo.orbit_direction || '-' },
  {
    label: language === 'en' ? 'Has Orbit:' : '有精轨：',
    value: dataInfo.has_orbit_data ? (language === 'en' ? 'Yes' : '是') : (language === 'en' ? 'No' : '否'),
  },
  {
    label: language === 'en' ? 'Orbit File:' : '轨道文件：',
    value: dataInfo.orbit_file_path || '-',
    valueStyle: { wordBreak: 'break-all' },
  },
  {
    label: language === 'en' ? 'ENVI Processed:' : 'ENVI已处理：',
    value: dataInfo.is_envi_processed ? (language === 'en' ? 'Yes' : '是') : (language === 'en' ? 'No' : '否'),
  },
];

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
