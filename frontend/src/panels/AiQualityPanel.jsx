export default function AiQualityPanel({ aiStatus, isLoading, isReadOnlyUser, onTrain, onPredictAll, language }) {
    const en = language === 'en';
    return (
        <div className="panel-content ai-panel">
            <div className="ai-panel">
                <h4>{en ? 'AI Quality Assessment' : 'AI 质量评估'}</h4>
                <p className="ai-desc">{en
                    ? 'Train a random forest model based on labeled results and predict quality for all results.'
                    : '基于标注结果训练随机森林模型，并对所有结果进行质量预测。'
                }</p>

                <div className="ai-stats">
                    <div className="stat-item">
                        <span className="stat-label">{en ? 'Labeled' : '已标注'}</span>
                        <span className="stat-value">{aiStatus?.labeled_count || 0}</span>
                    </div>
                    <div className="stat-item">
                        <span className="stat-label">{en ? 'Good' : '良好'}</span>
                        <span className="stat-value good">{aiStatus?.good_count || 0}</span>
                    </div>
                    <div className="stat-item">
                        <span className="stat-label">{en ? 'Poor' : '欠佳'}</span>
                        <span className="stat-value bad">{aiStatus?.bad_count || 0}</span>
                    </div>
                </div>

                <div className="ai-actions">
                    <button
                        onClick={onTrain}
                        disabled={isLoading || (aiStatus?.labeled_count || 0) < 2 || isReadOnlyUser}
                        className="primary-btn"
                    >
                        {isLoading ? (en ? 'Processing...' : '处理中...') : (en ? 'Train Model' : '训练模型')}
                    </button>
                    <p className="hint">{en
                        ? 'At least 2 samples (good and bad) are required to train.'
                        : '至少需要2个样本 (包含好和坏) 才能训练。'
                    }</p>

                    <hr />

                    <button
                        onClick={onPredictAll}
                        disabled={isLoading || !aiStatus?.is_model_trained || isReadOnlyUser}
                        className="secondary-btn"
                    >
                        {isLoading ? (en ? 'Processing...' : '处理中...') : (en ? 'Predict All Results' : '预测所有结果')}
                    </button>
                    {!aiStatus?.is_model_trained && (
                        <p className="hint">{en ? 'Please train the model first.' : '请先训练模型。'}</p>
                    )}
                </div>
            </div>
        </div>
    );
}
