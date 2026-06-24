import { useCallback, useEffect, useState } from 'react';
import { useI18n } from './i18n/I18nContext';
import {
  getPromptTemplates,
  createDiagnosis,
  listDiagnoses,
  deleteDiagnosis,
  getAiStatus,
} from './api/ai';
import { getDinsarResults } from './api/dinsar';
import AiDiagnosisModal from './components/AiDiagnosisModal';
import TaskStatusPanel from './components/tasks/TaskStatusPanel';
import useTaskMonitor from './hooks/useTaskMonitor';

const cardStyle = {
  background: '#fff',
  padding: '12px',
  borderRadius: '8px',
  border: '1px solid #e2e8f0',
  marginBottom: '12px',
};

export default function AiAnalysisPanel({ readOnly = false, onJobQueued }) {
  const { en } = useI18n();
  const aiTaskMonitor = useTaskMonitor({
    taskTypes: ['AI_DIAGNOSIS'],
    showRecent: true,
    recentLimit: 1,
  });

  // 状态
  const [aiStatus, setAiStatus] = useState(null);
  const [promptTemplates, setPromptTemplates] = useState({});
  const [dinsarResults, setDinsarResults] = useState([]);
  const [diagnoses, setDiagnoses] = useState([]);
  const [totalDiagnoses, setTotalDiagnoses] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize] = useState(20);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');

  // 表单状态
  const [selectedResultId, setSelectedResultId] = useState('');
  const [selectedModel, setSelectedModel] = useState('llama3.2-vision');
  const [selectedTemplate, setSelectedTemplate] = useState('standard');
  const [customPrompt, setCustomPrompt] = useState('');
  const [useCustomPrompt, setUseCustomPrompt] = useState(false);

  // 过滤器
  const [filterResultId, setFilterResultId] = useState('');
  const [filterRiskLevel, setFilterRiskLevel] = useState('');

  // Modal
  const [selectedDiagnosis, setSelectedDiagnosis] = useState(null);

  // 加载初始数据
  const loadInitialData = useCallback(async () => {
    try {
      const [status, templates, results] = await Promise.all([
        getAiStatus(),
        getPromptTemplates(),
        getDinsarResults({ limit: 100, offset: 0 }),
      ]);
      setAiStatus(status);
      setPromptTemplates(templates);

      // 兼容不同的响应格式
      const resultsList = results.results || results.items || [];
      setDinsarResults(resultsList);

      if (resultsList.length === 0) {
        setMessage(en ? 'No D-InSAR results found. Please scan data first.' : '未找到 D-InSAR 结果，请先扫描数据。');
      }
    } catch (error) {
      console.error('Failed to load initial data:', error);
      setMessage(en ? `Failed to load data: ${error.message}` : `加载数据失败: ${error.message}`);
    }
  }, [en]);

  useEffect(() => {
    loadInitialData();
  }, [loadInitialData]);

  useEffect(() => {
    const models = aiStatus?.ollama_vlm_models || [];
    if (models.length > 0 && !models.includes(selectedModel)) {
      setSelectedModel(aiStatus?.default_vlm_model && models.includes(aiStatus.default_vlm_model)
        ? aiStatus.default_vlm_model
        : models[0]);
    }
  }, [aiStatus, selectedModel]);

  // 加载诊断列表
  const loadDiagnoses = useCallback(async () => {
    setLoading(true);
    try {
      const params = {
        page: currentPage,
        page_size: pageSize,
      };
      if (filterResultId) params.result_id = parseInt(filterResultId);
      if (filterRiskLevel) params.risk_level = filterRiskLevel;

      const data = await listDiagnoses(params);
      setDiagnoses(data.items || []);
      setTotalDiagnoses(data.total || 0);
    } catch (error) {
      console.error('Failed to load diagnoses:', error);
      setMessage(en ? 'Failed to load diagnoses' : '加载诊断列表失败');
    } finally {
      setLoading(false);
    }
  }, [currentPage, pageSize, filterResultId, filterRiskLevel, en]);

  useEffect(() => {
    loadDiagnoses();
  }, [loadDiagnoses]);

  // 创建诊断
  const handleCreateDiagnosis = async () => {
    if (readOnly) {
      setMessage(en ? 'Read-only account cannot create diagnosis' : '只读账号无法创建诊断');
      return;
    }
    if (!selectedResultId) {
      setMessage(en ? 'Please select a D-InSAR result' : '请选择 D-InSAR 结果');
      return;
    }
    if (!aiStatus?.ollama_online) {
      setMessage(en ? 'Failed: Ollama is offline' : '失败: Ollama 未在线');
      return;
    }
    if (!aiStatus?.ollama_vlm_models?.length) {
      setMessage(en ? 'Failed: no local Ollama vision model is installed' : '失败: 未检测到本机 Ollama 视觉模型');
      return;
    }

    setLoading(true);
    setMessage('');
    try {
      const payload = {
        result_id: parseInt(selectedResultId),
        model_name: selectedModel,
        prompt_template: selectedTemplate,
      };
      if (useCustomPrompt && customPrompt.trim()) {
        payload.custom_prompt = customPrompt.trim();
      }

      const diagnosis = await createDiagnosis(payload);
      setMessage(en ? 'Diagnosis task created' : '诊断任务已创建');
      if (diagnosis.task_id) {
        onJobQueued?.(diagnosis.task_id);
      }
      // 刷新列表
      setTimeout(() => loadDiagnoses(), 1000);
    } catch (error) {
      const detail = error?.response?.data?.detail || error?.message || (en ? 'Unknown error' : '未知错误');
      setMessage(`${en ? 'Failed' : '失败'}: ${detail}`);
    } finally {
      setLoading(false);
    }
  };

  // 删除诊断
  const handleDeleteDiagnosis = async (diagnosisId) => {
    if (readOnly) {
      setMessage(en ? 'Read-only account cannot delete' : '只读账号无法删除');
      return;
    }
    if (!confirm(en ? 'Delete this diagnosis?' : '确认删除此诊断记录？')) return;

    try {
      await deleteDiagnosis(diagnosisId);
      setMessage(en ? 'Diagnosis deleted' : '诊断记录已删除');
      loadDiagnoses();
    } catch {
      setMessage(en ? 'Failed to delete' : '删除失败');
    }
  };

  // 查看诊断详情
  const handleViewDiagnosis = (diagnosis) => {
    setSelectedDiagnosis(diagnosis);
  };

  const riskLevelColors = {
    low: '#48bb78',
    medium: '#ed8936',
    high: '#f56565',
    critical: '#c53030',
  };

  const ollamaVlmModels = aiStatus?.ollama_vlm_models || [];
  const modelOptions = ollamaVlmModels.length > 0
    ? ollamaVlmModels
    : [aiStatus?.default_vlm_model || selectedModel].filter(Boolean);
  const canCreateDiagnosis = !!selectedResultId && !!aiStatus?.ollama_online && ollamaVlmModels.length > 0;
  const totalPages = Math.ceil(totalDiagnoses / pageSize);

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* Header */}
      <div style={{ padding: '12px', borderBottom: '1px solid #e2e8f0', flexShrink: 0 }}>
        <h2 style={{ margin: 0, fontSize: '18px', fontWeight: 600 }}>
          {en ? 'D-InSAR Diagnosis' : 'D-InSAR诊断'}
        </h2>
      </div>

      {/* Content */}
      <div style={{ flex: 1, overflow: 'auto', padding: '12px' }}>
        {/* Ollama Status */}
        <div style={cardStyle}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
            <span style={{ fontSize: '16px', fontWeight: 600 }}>
              {en ? 'Ollama Status' : 'Ollama 状态'}
            </span>
            <span
              style={{
                display: 'inline-block',
                width: '8px',
                height: '8px',
                borderRadius: '50%',
                backgroundColor: aiStatus?.ollama_online ? '#48bb78' : '#f56565',
              }}
            />
          </div>
          {aiStatus && (
            <div style={{ fontSize: '13px', color: '#718096' }}>
              {en ? 'Online' : '在线'}: {aiStatus.ollama_online ? (en ? 'Yes' : '是') : (en ? 'No' : '否')}
            </div>
          )}
        </div>

        {/* Create Diagnosis Form */}
        <div style={cardStyle}>
          <h3 style={{ margin: '0 0 12px 0', fontSize: '15px', fontWeight: 600 }}>
            {en ? 'Create Diagnosis' : '创建诊断'}
          </h3>

          <TaskStatusPanel
            title={en ? 'D-InSAR Diagnosis Task' : 'D-InSAR诊断任务'}
            activeTasks={aiTaskMonitor.activeTasks}
            recentTasks={aiTaskMonitor.recentTasks}
            latestTask={aiTaskMonitor.latestTask}
            isBusy={aiTaskMonitor.isBusy}
            idleText={en ? 'No D-InSAR diagnosis task is running.' : '当前没有正在执行的 D-InSAR 诊断任务。'}
            compact
          />

          {/* D-InSAR Result Selection */}
          <div style={{ marginBottom: '12px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
              <label style={{ fontSize: '13px', color: '#4a5568' }}>
                {en ? 'D-InSAR Result' : 'D-InSAR 结果'}
              </label>
              <button
                onClick={loadInitialData}
                style={{
                  padding: '2px 8px',
                  fontSize: '12px',
                  backgroundColor: '#edf2f7',
                  border: '1px solid #cbd5e0',
                  borderRadius: '4px',
                  cursor: 'pointer',
                }}
              >
                🔄 {en ? 'Refresh' : '刷新'}
              </button>
            </div>
            <select
              value={selectedResultId}
              onChange={(e) => setSelectedResultId(e.target.value)}
              style={{
                width: '100%',
                padding: '6px 8px',
                border: '1px solid #cbd5e0',
                borderRadius: '4px',
                fontSize: '13px',
              }}
            >
              <option value="">
                {dinsarResults.length === 0
                  ? (en ? 'No results available (scan data first)' : '无可用结果（请先扫描数据）')
                  : (en ? 'Select a result...' : '选择结果...')}
              </option>
              {dinsarResults.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </select>
            {dinsarResults.length === 0 && (
              <div style={{ marginTop: '6px', fontSize: '12px', color: '#e53e3e' }}>
                {en
                  ? 'No D-InSAR results found. Please go to "D-InSAR Results" tab and scan data first.'
                  : '未找到 D-InSAR 结果。请前往"D-InSAR 结果"选项卡先扫描数据。'}
              </div>
            )}
          </div>

          {/* Model Selection */}
          <div style={{ marginBottom: '12px' }}>
            <label style={{ display: 'block', fontSize: '13px', marginBottom: '4px', color: '#4a5568' }}>
              {en ? 'Model' : '模型'}
            </label>
            <select
              value={selectedModel}
              onChange={(e) => setSelectedModel(e.target.value)}
              style={{
                width: '100%',
                padding: '6px 8px',
                border: '1px solid #cbd5e0',
                borderRadius: '4px',
                fontSize: '13px',
              }}
            >
              {modelOptions.map((modelName) => (
                <option key={modelName} value={modelName}>{modelName}</option>
              ))}
            </select>
            {aiStatus?.ollama_online && ollamaVlmModels.length === 0 && (
              <div style={{ marginTop: '6px', fontSize: '12px', color: '#e53e3e' }}>
                {en ? 'Ollama is online, but no local vision model is installed.' : 'Ollama 已在线，但未检测到本机视觉模型。'}
              </div>
            )}
          </div>

          {/* Prompt Template Selection */}
          <div style={{ marginBottom: '12px' }}>
            <label style={{ display: 'block', fontSize: '13px', marginBottom: '4px', color: '#4a5568' }}>
              {en ? 'Prompt Template' : 'Prompt 模板'}
            </label>
            <select
              value={selectedTemplate}
              onChange={(e) => setSelectedTemplate(e.target.value)}
              disabled={useCustomPrompt}
              style={{
                width: '100%',
                padding: '6px 8px',
                border: '1px solid #cbd5e0',
                borderRadius: '4px',
                fontSize: '13px',
                opacity: useCustomPrompt ? 0.5 : 1,
              }}
            >
              {Object.entries(promptTemplates).map(([key, value]) => (
                <option key={key} value={key}>
                  {key} - {value.description}
                </option>
              ))}
            </select>
          </div>

          {/* Custom Prompt Toggle */}
          <div style={{ marginBottom: '12px' }}>
            <label style={{ display: 'flex', alignItems: 'center', fontSize: '13px', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={useCustomPrompt}
                onChange={(e) => setUseCustomPrompt(e.target.checked)}
                style={{ marginRight: '6px' }}
              />
              {en ? 'Use custom prompt' : '使用自定义 Prompt'}
            </label>
          </div>

          {/* Custom Prompt Textarea */}
          {useCustomPrompt && (
            <div style={{ marginBottom: '12px' }}>
              <textarea
                value={customPrompt}
                onChange={(e) => setCustomPrompt(e.target.value)}
                placeholder={en ? 'Enter custom prompt...' : '输入自定义 Prompt...'}
                style={{
                  width: '100%',
                  minHeight: '100px',
                  padding: '8px',
                  border: '1px solid #cbd5e0',
                  borderRadius: '4px',
                  fontSize: '13px',
                  fontFamily: 'monospace',
                  resize: 'vertical',
                }}
              />
            </div>
          )}

          {/* Submit Button */}
          <button
            onClick={handleCreateDiagnosis}
            disabled={loading || aiTaskMonitor.isBusy || !canCreateDiagnosis}
            style={{
              width: '100%',
              padding: '8px',
              backgroundColor: loading || aiTaskMonitor.isBusy || !canCreateDiagnosis ? '#cbd5e0' : '#3182ce',
              color: '#fff',
              border: 'none',
              borderRadius: '4px',
              fontSize: '14px',
              fontWeight: 500,
              cursor: loading || aiTaskMonitor.isBusy || !canCreateDiagnosis ? 'not-allowed' : 'pointer',
            }}
          >
            {loading || aiTaskMonitor.isBusy ? (en ? 'Creating...' : '创建中...') : (en ? 'Create Diagnosis' : '创建诊断')}
          </button>

          {/* Message */}
          {message && (
            <div
              style={{
                marginTop: '12px',
                padding: '8px',
                backgroundColor: message.includes('失败') || message.includes('Failed') ? '#fed7d7' : '#c6f6d5',
                color: message.includes('失败') || message.includes('Failed') ? '#c53030' : '#22543d',
                borderRadius: '4px',
                fontSize: '13px',
              }}
            >
              {message}
            </div>
          )}
        </div>

        {/* Filters */}
        <div style={cardStyle}>
          <h3 style={{ margin: '0 0 12px 0', fontSize: '15px', fontWeight: 600 }}>
            {en ? 'Filters' : '过滤器'}
          </h3>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
            <div>
              <label style={{ display: 'block', fontSize: '13px', marginBottom: '4px', color: '#4a5568' }}>
                {en ? 'Result ID' : '结果 ID'}
              </label>
              <input
                type="number"
                value={filterResultId}
                onChange={(e) => setFilterResultId(e.target.value)}
                placeholder={en ? 'Filter by result ID' : '按结果 ID 过滤'}
                style={{
                  width: '100%',
                  padding: '6px 8px',
                  border: '1px solid #cbd5e0',
                  borderRadius: '4px',
                  fontSize: '13px',
                }}
              />
            </div>
            <div>
              <label style={{ display: 'block', fontSize: '13px', marginBottom: '4px', color: '#4a5568' }}>
                {en ? 'Risk Level' : '风险等级'}
              </label>
              <select
                value={filterRiskLevel}
                onChange={(e) => setFilterRiskLevel(e.target.value)}
                style={{
                  width: '100%',
                  padding: '6px 8px',
                  border: '1px solid #cbd5e0',
                  borderRadius: '4px',
                  fontSize: '13px',
                }}
              >
                <option value="">{en ? 'All' : '全部'}</option>
                <option value="low">{en ? 'Low' : '低'}</option>
                <option value="medium">{en ? 'Medium' : '中'}</option>
                <option value="high">{en ? 'High' : '高'}</option>
                <option value="critical">{en ? 'Critical' : '极高'}</option>
              </select>
            </div>
          </div>
        </div>

        {/* Diagnoses Table */}
        <div style={cardStyle}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
            <h3 style={{ margin: 0, fontSize: '15px', fontWeight: 600 }}>
              {en ? 'Diagnosis Records' : '诊断记录'} ({totalDiagnoses})
            </h3>
            <button
              onClick={loadDiagnoses}
              disabled={loading}
              style={{
                padding: '4px 12px',
                backgroundColor: '#edf2f7',
                border: '1px solid #cbd5e0',
                borderRadius: '4px',
                fontSize: '13px',
                cursor: loading ? 'not-allowed' : 'pointer',
              }}
            >
              {loading ? '⟳' : '🔄'} {en ? 'Refresh' : '刷新'}
            </button>
          </div>

          {diagnoses.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '24px', color: '#a0aec0', fontSize: '14px' }}>
              {en ? 'No diagnosis records' : '暂无诊断记录'}
            </div>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', fontSize: '13px', borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ backgroundColor: '#f7fafc', borderBottom: '2px solid #e2e8f0' }}>
                    <th style={{ padding: '8px', textAlign: 'left', fontWeight: 600 }}>ID</th>
                    <th style={{ padding: '8px', textAlign: 'left', fontWeight: 600 }}>
                      {en ? 'Result' : '结果名称'}
                    </th>
                    <th style={{ padding: '8px', textAlign: 'left', fontWeight: 600 }}>
                      {en ? 'Model' : '模型'}
                    </th>
                    <th style={{ padding: '8px', textAlign: 'left', fontWeight: 600 }}>
                      {en ? 'Template' : '模板'}
                    </th>
                    <th style={{ padding: '8px', textAlign: 'left', fontWeight: 600 }}>
                      {en ? 'Risk' : '风险'}
                    </th>
                    <th style={{ padding: '8px', textAlign: 'left', fontWeight: 600 }}>
                      {en ? 'Hazards' : '隐患点'}
                    </th>
                    <th style={{ padding: '8px', textAlign: 'left', fontWeight: 600 }}>
                      {en ? 'Created' : '创建时间'}
                    </th>
                    <th style={{ padding: '8px', textAlign: 'center', fontWeight: 600 }}>
                      {en ? 'Actions' : '操作'}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {diagnoses.map((d) => (
                    <tr key={d.id} style={{ borderBottom: '1px solid #e2e8f0' }}>
                      <td style={{ padding: '8px' }}>{d.id}</td>
                      <td style={{ padding: '8px', maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {d.result_name || 'N/A'}
                      </td>
                      <td style={{ padding: '8px' }}>{d.model_name}</td>
                      <td style={{ padding: '8px' }}>{d.prompt_template}</td>
                      <td style={{ padding: '8px' }}>
                        {d.risk_level ? (
                          <span
                            style={{
                              display: 'inline-block',
                              padding: '2px 8px',
                              borderRadius: '12px',
                              fontSize: '12px',
                              fontWeight: 500,
                              color: '#fff',
                              backgroundColor: riskLevelColors[d.risk_level] || '#a0aec0',
                            }}
                          >
                            {d.risk_level}
                          </span>
                        ) : (
                          <span style={{ color: '#a0aec0' }}>-</span>
                        )}
                      </td>
                      <td style={{ padding: '8px' }}>{d.hazards_found}</td>
                      <td style={{ padding: '8px', fontSize: '12px', color: '#718096' }}>
                        {new Date(d.created_at).toLocaleString()}
                      </td>
                      <td style={{ padding: '8px', textAlign: 'center' }}>
                        <button
                          onClick={() => handleViewDiagnosis(d)}
                          style={{
                            padding: '4px 8px',
                            marginRight: '4px',
                            backgroundColor: '#3182ce',
                            color: '#fff',
                            border: 'none',
                            borderRadius: '4px',
                            fontSize: '12px',
                            cursor: 'pointer',
                          }}
                        >
                          {en ? 'View' : '查看'}
                        </button>
                        <button
                          onClick={() => handleDeleteDiagnosis(d.id)}
                          disabled={readOnly}
                          style={{
                            padding: '4px 8px',
                            backgroundColor: readOnly ? '#cbd5e0' : '#f56565',
                            color: '#fff',
                            border: 'none',
                            borderRadius: '4px',
                            fontSize: '12px',
                            cursor: readOnly ? 'not-allowed' : 'pointer',
                          }}
                        >
                          {en ? 'Delete' : '删除'}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Pagination */}
          {totalPages > 1 && (
            <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: '8px', marginTop: '12px' }}>
              <button
                onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                disabled={currentPage === 1}
                style={{
                  padding: '4px 12px',
                  backgroundColor: currentPage === 1 ? '#edf2f7' : '#3182ce',
                  color: currentPage === 1 ? '#a0aec0' : '#fff',
                  border: 'none',
                  borderRadius: '4px',
                  fontSize: '13px',
                  cursor: currentPage === 1 ? 'not-allowed' : 'pointer',
                }}
              >
                {en ? 'Prev' : '上一页'}
              </button>
              <span style={{ fontSize: '13px', color: '#4a5568' }}>
                {currentPage} / {totalPages}
              </span>
              <button
                onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                disabled={currentPage === totalPages}
                style={{
                  padding: '4px 12px',
                  backgroundColor: currentPage === totalPages ? '#edf2f7' : '#3182ce',
                  color: currentPage === totalPages ? '#a0aec0' : '#fff',
                  border: 'none',
                  borderRadius: '4px',
                  fontSize: '13px',
                  cursor: currentPage === totalPages ? 'not-allowed' : 'pointer',
                }}
              >
                {en ? 'Next' : '下一页'}
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Diagnosis Modal */}
      {selectedDiagnosis && (
        <AiDiagnosisModal
          diagnosis={selectedDiagnosis}
          onClose={() => setSelectedDiagnosis(null)}
        />
      )}
    </div>
  );
}
