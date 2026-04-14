import { useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import { useI18n } from '../i18n/I18nContext';

/**
 * AI 诊断报告查看 Modal
 * @param {Object} props
 * @param {Object|null} props.diagnosis - 诊断记录对象
 * @param {Function} props.onClose - 关闭回调
 */
export default function AiDiagnosisModal({ diagnosis, onClose }) {
  const { en } = useI18n();

  useEffect(() => {
    const handleEsc = (e) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleEsc);
    return () => window.removeEventListener('keydown', handleEsc);
  }, [onClose]);

  if (!diagnosis) return null;

  const riskLevelMap = {
    low: { label: en ? 'Low' : '低', color: '#48bb78' },
    medium: { label: en ? 'Medium' : '中', color: '#ed8936' },
    high: { label: en ? 'High' : '高', color: '#f56565' },
    critical: { label: en ? 'Critical' : '极高', color: '#c53030' },
  };

  const riskInfo = riskLevelMap[diagnosis.risk_level] || { label: en ? 'Unknown' : '未知', color: '#a0aec0' };

  return (
    <div className="modal-overlay visible" onClick={onClose}>
      <div className="ai-diagnosis-modal" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="ai-diagnosis-modal-header">
          <div className="ai-diagnosis-modal-title">
            <span className="ai-diagnosis-icon">🔍</span>
            <span>{en ? 'AI Diagnosis Report' : 'AI 诊断报告'}</span>
          </div>
          <button className="modal-close-btn" onClick={onClose}>×</button>
        </div>

        {/* Meta Info */}
        <div className="ai-diagnosis-meta">
          <div className="ai-diagnosis-meta-row">
            <span className="ai-diagnosis-meta-label">{en ? 'Result' : '结果名称'}:</span>
            <span className="ai-diagnosis-meta-value">{diagnosis.result_name || 'N/A'}</span>
          </div>
          <div className="ai-diagnosis-meta-row">
            <span className="ai-diagnosis-meta-label">{en ? 'Date Range' : '监测周期'}:</span>
            <span className="ai-diagnosis-meta-value">{diagnosis.date_range || 'N/A'}</span>
          </div>
          <div className="ai-diagnosis-meta-row">
            <span className="ai-diagnosis-meta-label">{en ? 'Model' : '模型'}:</span>
            <span className="ai-diagnosis-meta-value">{diagnosis.model_name}</span>
          </div>
          <div className="ai-diagnosis-meta-row">
            <span className="ai-diagnosis-meta-label">{en ? 'Template' : '模板'}:</span>
            <span className="ai-diagnosis-meta-value">{diagnosis.prompt_template}</span>
          </div>
          {diagnosis.risk_level && (
            <div className="ai-diagnosis-meta-row">
              <span className="ai-diagnosis-meta-label">{en ? 'Risk Level' : '风险等级'}:</span>
              <span
                className="ai-diagnosis-risk-badge"
                style={{ backgroundColor: riskInfo.color }}
              >
                {riskInfo.label}
              </span>
            </div>
          )}
          {diagnosis.confidence_score !== null && (
            <div className="ai-diagnosis-meta-row">
              <span className="ai-diagnosis-meta-label">{en ? 'Confidence' : '置信度'}:</span>
              <span className="ai-diagnosis-meta-value">
                {(diagnosis.confidence_score * 100).toFixed(0)}%
              </span>
            </div>
          )}
          {diagnosis.quality_score !== null && (
            <div className="ai-diagnosis-meta-row">
              <span className="ai-diagnosis-meta-label">{en ? 'Quality Score' : '质量评分'}:</span>
              <span className="ai-diagnosis-meta-value">
                {diagnosis.quality_score.toFixed(1)} / 10
              </span>
            </div>
          )}
          <div className="ai-diagnosis-meta-row">
            <span className="ai-diagnosis-meta-label">{en ? 'Hazards Found' : '隐患点数'}:</span>
            <span className="ai-diagnosis-meta-value">{diagnosis.hazards_found}</span>
          </div>
          {diagnosis.duration_seconds !== null && (
            <div className="ai-diagnosis-meta-row">
              <span className="ai-diagnosis-meta-label">{en ? 'Duration' : '耗时'}:</span>
              <span className="ai-diagnosis-meta-value">
                {diagnosis.duration_seconds.toFixed(1)}s
              </span>
            </div>
          )}
          <div className="ai-diagnosis-meta-row">
            <span className="ai-diagnosis-meta-label">{en ? 'Created At' : '创建时间'}:</span>
            <span className="ai-diagnosis-meta-value">
              {new Date(diagnosis.created_at).toLocaleString()}
            </span>
          </div>
        </div>

        {/* Markdown Content */}
        <div className="ai-diagnosis-content">
          {diagnosis.error_message ? (
            <div className="ai-diagnosis-error">
              <strong>{en ? 'Error' : '错误'}:</strong> {diagnosis.error_message}
            </div>
          ) : diagnosis.diagnosis_markdown ? (
            <ReactMarkdown className="ai-diagnosis-markdown">
              {diagnosis.diagnosis_markdown}
            </ReactMarkdown>
          ) : (
            <div className="ai-diagnosis-empty">
              {en ? 'Diagnosis in progress...' : '诊断进行中...'}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="ai-diagnosis-modal-footer">
          <button className="btn-secondary" onClick={onClose}>
            {en ? 'Close' : '关闭'}
          </button>
        </div>
      </div>
    </div>
  );
}
