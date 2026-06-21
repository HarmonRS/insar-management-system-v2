import { useState } from 'react';
import AiQualityPanel from './AiQualityPanel';
import AiAnalysisPanel from '../AiAnalysisPanel';

const MODES = {
    quality: 'quality',
    diagnosis: 'diagnosis',
};

export default function DinsarAnalysisPanel({
    aiStatus,
    isLoading,
    isReadOnlyUser,
    aiPanel,
    language,
    onJobQueued,
}) {
    const [activeMode, setActiveMode] = useState(MODES.quality);
    const en = language === 'en';

    const tabs = [
        {
            key: MODES.quality,
            label: en ? 'AI Quality Assessment' : 'AI质量评估',
        },
        {
            key: MODES.diagnosis,
            label: en ? 'D-InSAR Diagnosis' : 'D-InSAR诊断',
        },
    ];

    return (
        <div className="panel-content dinsar-analysis-panel">
            <div className="dinsar-analysis-toolbar">
                <div>
                    <h3>{en ? 'D-InSAR Analysis' : 'D-InSAR分析'}</h3>
                    <p>
                        {en
                            ? 'Quality assessment and diagnosis are managed under the D-InSAR workflow.'
                            : '质量评估和智能诊断统一归口到 D-InSAR 分析。'}
                    </p>
                </div>
                <div className="dinsar-analysis-tabs" role="tablist" aria-label={en ? 'D-InSAR analysis mode' : 'D-InSAR分析模式'}>
                    {tabs.map((tab) => (
                        <button
                            key={tab.key}
                            type="button"
                            className={activeMode === tab.key ? 'active-tab' : ''}
                            onClick={() => setActiveMode(tab.key)}
                            role="tab"
                            aria-selected={activeMode === tab.key}
                        >
                            {tab.label}
                        </button>
                    ))}
                </div>
            </div>

            <div className="dinsar-analysis-body">
                {activeMode === MODES.quality ? (
                    <AiQualityPanel
                        aiStatus={aiStatus}
                        isLoading={isLoading}
                        isReadOnlyUser={isReadOnlyUser}
                        onTrain={aiPanel.onTrain}
                        onPredictAll={aiPanel.onPredictAll}
                        language={language}
                    />
                ) : (
                    <AiAnalysisPanel
                        readOnly={isReadOnlyUser}
                        onJobQueued={onJobQueued}
                    />
                )}
            </div>
        </div>
    );
}
