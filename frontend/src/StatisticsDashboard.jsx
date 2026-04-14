import React, { useState, useEffect } from 'react';
import { Chart as ChartJS, ArcElement, Tooltip, Legend, CategoryScale, LinearScale, BarElement, Title } from 'chart.js';
import { Pie, Bar } from 'react-chartjs-2';
import { statsApi } from './api';

ChartJS.register(ArcElement, Tooltip, Legend, CategoryScale, LinearScale, BarElement, Title);

const chartOptions = {
  responsive: true,
  plugins: {
    legend: {
      position: 'top',
    },
    title: {
      display: true,
      font: {
        size: 16
      }
    },
  },
};

const num = (value) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
};

const StatisticsDashboard = ({ onClose }) => {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    const fetchStatistics = async () => {
      try {
        setLoading(true);
        const data = await statsApi.getStatistics();
        setStats(data);
        setError('');
      } catch (err) {
        setError('无法加载统计数据，请确认后端服务正常。');
        console.error(err);
      } finally {
        setLoading(false);
      }
    };
    fetchStatistics();
  }, []);

  const generateChartData = (title, data, colors = null) => {
    const labels = Object.keys(data || {});
    const values = Object.values(data || {}).map(num);
    const defaultBg = [
      'rgba(255, 99, 132, 0.7)',
      'rgba(54, 162, 235, 0.7)',
      'rgba(255, 206, 86, 0.7)',
      'rgba(75, 192, 192, 0.7)',
      'rgba(153, 102, 255, 0.7)',
      'rgba(255, 159, 64, 0.7)',
    ];
    const defaultBorder = [
      'rgba(255, 99, 132, 1)',
      'rgba(54, 162, 235, 1)',
      'rgba(255, 206, 86, 1)',
      'rgba(75, 192, 192, 1)',
      'rgba(153, 102, 255, 1)',
      'rgba(255, 159, 64, 1)',
    ];
    return {
      labels,
      datasets: [
        {
          label: title,
          data: values,
          backgroundColor: colors?.backgroundColor || defaultBg,
          borderColor: colors?.borderColor || defaultBorder,
          borderWidth: 1,
        },
      ],
    };
  };

  if (loading) {
    return (
      <div className="statistics-modal">
        <div className="statistics-content">
          <h2>正在加载统计数据...</h2>
          <button onClick={onClose} className="close-button">关闭</button>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="statistics-modal">
        <div className="statistics-content">
          <h2 style={{ color: 'red' }}>{error}</h2>
          <button onClick={onClose} className="close-button">关闭</button>
        </div>
      </div>
    );
  }

  if (!stats) return null;

  const {
    dinsar_results_overview = {},
    source_data_overview = {},
    by_satellite = {},
    ai_quality_overview = {},
    ai_prediction_overview = {},
    dinsar_cache_consistency = {},
    source_preview_consistency = {},
    source_xml_consistency = {},
  } = stats;

  const sourceDataOrbit = generateChartData(
    '源数据精轨状态',
    {
      有精轨数据: num(source_data_overview.with_orbit_data_count),
      无精轨数据: Math.max(0, num(source_data_overview.total_count) - num(source_data_overview.with_orbit_data_count)),
    },
    {
      backgroundColor: ['rgba(75, 192, 192, 0.7)', 'rgba(201, 203, 207, 0.7)'],
      borderColor: ['rgba(75, 192, 192, 1)', 'rgba(201, 203, 207, 1)'],
    }
  );

  const dinsarData = generateChartData(
    'D-InSAR 缓存状态',
    {
      已缓存: num(dinsar_results_overview.cached_count),
      未缓存: num(dinsar_results_overview.uncached_count),
    },
    {
      backgroundColor: ['rgba(75, 192, 192, 0.7)', 'rgba(255, 99, 132, 0.7)'],
      borderColor: ['rgba(75, 192, 192, 1)', 'rgba(255, 99, 132, 1)'],
    }
  );

  const sourceDataEnvi = generateChartData(
    '源数据 ENVI 处理状态',
    {
      有ENVI结果: num(source_data_overview.envi_processed_count),
      无ENVI结果: Math.max(0, num(source_data_overview.total_count) - num(source_data_overview.envi_processed_count)),
    },
    {
      backgroundColor: ['rgba(54, 162, 235, 0.7)', 'rgba(201, 203, 207, 0.7)'],
      borderColor: ['rgba(54, 162, 235, 1)', 'rgba(201, 203, 207, 1)'],
    }
  );

  const aiQualityData = generateChartData(
    '人工标记质量分布',
    {
      人工标记良好: num(ai_quality_overview.good_count),
      人工标记欠佳: num(ai_quality_overview.bad_count),
      未人工标记: num(ai_quality_overview.unlabeled_count),
    },
    {
      backgroundColor: ['rgba(40, 167, 69, 0.7)', 'rgba(220, 53, 69, 0.7)', 'rgba(108, 117, 125, 0.7)'],
      borderColor: ['rgba(40, 167, 69, 1)', 'rgba(220, 53, 69, 1)', 'rgba(108, 117, 125, 1)'],
    }
  );

  const aiPredictionData = generateChartData(
    'AI 预测质量分布',
    {
      'AI预测良好 (>=0.7)': num(ai_prediction_overview.good_count),
      'AI预测欠佳 (<0.4)': num(ai_prediction_overview.bad_count),
      AI预测中等: num(ai_prediction_overview.medium_count),
      未预测: num(ai_prediction_overview.unpredicted_count),
    },
    {
      backgroundColor: ['rgba(40, 167, 69, 0.7)', 'rgba(220, 53, 69, 0.7)', 'rgba(255, 193, 7, 0.7)', 'rgba(108, 117, 125, 0.7)'],
      borderColor: ['rgba(40, 167, 69, 1)', 'rgba(220, 53, 69, 1)', 'rgba(255, 193, 7, 1)', 'rgba(108, 117, 125, 1)'],
    }
  );

  const dinsarConsistencyData = generateChartData(
    'D-InSAR 缓存一致性',
    {
      '库缓存且文件存在': num(dinsar_cache_consistency.db_cached_and_file_exists_count),
      '库缓存但文件缺失': num(dinsar_cache_consistency.db_cached_but_file_missing_count),
      '库未缓存但文件存在': num(dinsar_cache_consistency.db_uncached_but_file_exists_count),
      '库未缓存且文件缺失': num(dinsar_cache_consistency.db_uncached_and_file_missing_count),
    },
    {
      backgroundColor: ['rgba(40, 167, 69, 0.7)', 'rgba(220, 53, 69, 0.7)', 'rgba(255, 193, 7, 0.7)', 'rgba(108, 117, 125, 0.7)'],
      borderColor: ['rgba(40, 167, 69, 1)', 'rgba(220, 53, 69, 1)', 'rgba(255, 193, 7, 1)', 'rgba(108, 117, 125, 1)'],
    }
  );

  const sourcePreviewConsistencyData = generateChartData(
    '源影像预览一致性',
    {
      '存在预览缓存': num(source_preview_consistency.preview_exists_count),
      '预览缓存缺失': num(source_preview_consistency.preview_missing_count),
      'DB READY且有缓存': num(source_preview_consistency.db_ready_and_cache_exists_count),
      'DB READY但缺缓存': num(source_preview_consistency.db_ready_but_cache_missing_count),
    },
    {
      backgroundColor: ['rgba(40, 167, 69, 0.7)', 'rgba(220, 53, 69, 0.7)', 'rgba(54, 162, 235, 0.7)', 'rgba(255, 159, 64, 0.7)'],
      borderColor: ['rgba(40, 167, 69, 1)', 'rgba(220, 53, 69, 1)', 'rgba(54, 162, 235, 1)', 'rgba(255, 159, 64, 1)'],
    }
  );

  const sourceXmlConsistencyData = generateChartData(
    '源影像 XML 读取一致性',
    {
      '检测到XML并解析': num(source_xml_consistency.xml_parsed_ok_count),
      '检测到XML未解析': num(source_xml_consistency.xml_detected_but_unparsed_count),
      '缺少XML': num(source_xml_consistency.xml_missing_count),
    },
    {
      backgroundColor: ['rgba(40, 167, 69, 0.7)', 'rgba(255, 193, 7, 0.7)', 'rgba(220, 53, 69, 0.7)'],
      borderColor: ['rgba(40, 167, 69, 1)', 'rgba(255, 193, 7, 1)', 'rgba(220, 53, 69, 1)'],
    }
  );

  const issues = [];
  if (num(dinsar_cache_consistency.db_cached_but_file_missing_count) > 0) {
    issues.push({ level: 'error', text: `D-InSAR: 数据库标记已缓存但文件缺失 ${num(dinsar_cache_consistency.db_cached_but_file_missing_count)} 条` });
  }
  if (num(dinsar_cache_consistency.db_uncached_but_file_exists_count) > 0) {
    issues.push({ level: 'warn', text: `D-InSAR: 数据库未标记缓存但文件已存在 ${num(dinsar_cache_consistency.db_uncached_but_file_exists_count)} 条` });
  }
  if (num(dinsar_cache_consistency.manifest_missing_file_count) > 0) {
    issues.push({ level: 'warn', text: `D-InSAR: manifest 引用缺失文件 ${num(dinsar_cache_consistency.manifest_missing_file_count)} 条` });
  }
  if (num(source_preview_consistency.db_ready_but_cache_missing_count) > 0) {
    issues.push({ level: 'error', text: `源影像: DB READY 但预览缓存缺失 ${num(source_preview_consistency.db_ready_but_cache_missing_count)} 条` });
  }
  if (num(source_xml_consistency.xml_detected_but_unparsed_count) > 0) {
    issues.push({ level: 'warn', text: `源影像: 检测到 XML 但关键字段未入库 ${num(source_xml_consistency.xml_detected_but_unparsed_count)} 条` });
  }
  if (num(source_xml_consistency.xml_missing_count) > 0) {
    issues.push({ level: 'warn', text: `源影像: 未检测到 XML ${num(source_xml_consistency.xml_missing_count)} 条` });
  }

  return (
    <div className="statistics-modal">
      <div className="statistics-content">
        <div className="statistics-header">
          <h1>数据统计仪表盘</h1>
          <button onClick={onClose} className="close-button">&times;</button>
        </div>

        <div className="statistics-kpi-grid">
          <div className="statistics-kpi-card">
            <div className="statistics-kpi-label">D-InSAR总数</div>
            <div className="statistics-kpi-value">{num(dinsar_results_overview.total_count)}</div>
          </div>
          <div className="statistics-kpi-card">
            <div className="statistics-kpi-label">D-InSAR缓存文件存在</div>
            <div className="statistics-kpi-value">{num(dinsar_cache_consistency.cache_file_exists_count)}</div>
          </div>
          <div className="statistics-kpi-card">
            <div className="statistics-kpi-label">源影像预览存在</div>
            <div className="statistics-kpi-value">{num(source_preview_consistency.preview_exists_count)}</div>
          </div>
          <div className="statistics-kpi-card">
            <div className="statistics-kpi-label">XML解析成功</div>
            <div className="statistics-kpi-value">{num(source_xml_consistency.xml_parsed_ok_count)}</div>
          </div>
        </div>

        <div className="statistics-alerts">
          {issues.length === 0 ? (
            <div className="statistics-alert-item ok">一致性检查通过，当前未发现异常项。</div>
          ) : (
            issues.map((item, index) => (
              <div key={`${item.level}-${index}`} className={`statistics-alert-item ${item.level}`}>
                {item.text}
              </div>
            ))
          )}
        </div>

        <div className="statistics-grid">
          <div className="chart-container">
            <Pie options={{ ...chartOptions, plugins: { ...chartOptions.plugins, title: { ...chartOptions.plugins.title, text: `AI 预测质量分布 (总计: ${num(dinsar_results_overview.total_count)})` } } }} data={aiPredictionData} />
          </div>

          <div className="chart-container">
            <Pie options={{ ...chartOptions, plugins: { ...chartOptions.plugins, title: { ...chartOptions.plugins.title, text: `人工标记质量分布 (总计: ${num(dinsar_results_overview.total_count)})` } } }} data={aiQualityData} />
          </div>

          <div className="chart-container">
            <Pie options={{ ...chartOptions, plugins: { ...chartOptions.plugins, title: { ...chartOptions.plugins.title, text: `D-InSAR 缓存状态 (总计: ${num(dinsar_results_overview.total_count)})` } } }} data={dinsarData} />
          </div>

          <div className="chart-container">
            <Pie options={{ ...chartOptions, plugins: { ...chartOptions.plugins, title: { ...chartOptions.plugins.title, text: 'D-InSAR 缓存一致性' } } }} data={dinsarConsistencyData} />
          </div>

          <div className="chart-container">
            <Pie options={{ ...chartOptions, plugins: { ...chartOptions.plugins, title: { ...chartOptions.plugins.title, text: '源影像预览一致性' } } }} data={sourcePreviewConsistencyData} />
          </div>

          <div className="chart-container">
            <Pie options={{ ...chartOptions, plugins: { ...chartOptions.plugins, title: { ...chartOptions.plugins.title, text: '源影像 XML 读取一致性' } } }} data={sourceXmlConsistencyData} />
          </div>

          <div className="chart-container">
            {num(source_data_overview.total_count) > 0 && <Pie options={{ ...chartOptions, plugins: { ...chartOptions.plugins, title: { ...chartOptions.plugins.title, text: `源数据 ENVI 处理状态 (总计: ${num(source_data_overview.total_count)})` } } }} data={sourceDataEnvi} />}
          </div>

          <div className="chart-container">
            {Object.keys(by_satellite).length > 0 && <Bar options={{ ...chartOptions, plugins: { ...chartOptions.plugins, title: { ...chartOptions.plugins.title, text: '源数据按卫星统计' } } }} data={generateChartData('卫星数据量', by_satellite)} />}
          </div>

          <div className="chart-container">
            {num(source_data_overview.total_count) > 0 && <Pie options={{ ...chartOptions, plugins: { ...chartOptions.plugins, title: { ...chartOptions.plugins.title, text: `源数据精轨状态 (总计: ${num(source_data_overview.total_count)})` } } }} data={sourceDataOrbit} />}
          </div>
        </div>
      </div>
    </div>
  );
};

export default StatisticsDashboard;
