import { useMemo, useState } from 'react';
import { exportDinsarResults } from '../api/dinsar';
import { getDinsarEngineMeta } from '../utils/dinsarEngines';

const EXAMPLE_TARGET_DIR = String.raw`例如: D:\Export\Results 或 \\server\share\results`;

export default function ResultExportModal({ results = [], onClose }) {
    const [targetDir, setTargetDir] = useState('');
    const [selectedIds, setSelectedIds] = useState(() => new Set(results.map((result) => result.id)));
    const [exporting, setExporting] = useState(false);
    const [exportResult, setExportResult] = useState(null);
    const [error, setError] = useState('');

    const selectedCount = selectedIds.size;
    const sortedResults = useMemo(() => [...results], [results]);

    const toggleSelect = (id) => {
        setSelectedIds((prev) => {
            const next = new Set(prev);
            if (next.has(id)) {
                next.delete(id);
            } else {
                next.add(id);
            }
            return next;
        });
    };

    const toggleAll = () => {
        if (selectedIds.size === results.length) {
            setSelectedIds(new Set());
            return;
        }
        setSelectedIds(new Set(results.map((result) => result.id)));
    };

    const handleExport = async () => {
        const dir = targetDir.trim();
        if (!dir) {
            setError('请输入目标路径。');
            return;
        }
        if (selectedIds.size === 0) {
            setError('请至少选择一个结果。');
            return;
        }

        setError('');
        setExporting(true);
        setExportResult(null);
        try {
            const response = await exportDinsarResults([...selectedIds], dir);
            setExportResult(response);
        } catch (eventualError) {
            setError(eventualError.response?.data?.detail || eventualError.message || '提取失败。');
        } finally {
            setExporting(false);
        }
    };

    return (
        <div className="modal-overlay visible" onClick={onClose}>
            <div className="modal-content result-export-modal" onClick={(event) => event.stopPropagation()}>
                <div className="modal-header">
                    <h3>提取 D-InSAR 结果</h3>
                    <button type="button" className="modal-close-btn" onClick={onClose} aria-label="关闭">
                        &times;
                    </button>
                </div>

                <div className="modal-body">
                    <div className="export-path-section">
                        <label>目标路径，支持本地盘符或 UNC 路径，例如 `D:\Export\Results`。</label>
                        <input
                            type="text"
                            value={targetDir}
                            onChange={(event) => setTargetDir(event.target.value)}
                            placeholder={EXAMPLE_TARGET_DIR}
                            disabled={exporting}
                            className="export-path-input"
                        />
                    </div>

                    <div className="export-select-section">
                        <div className="export-select-header">
                            <label>
                                <input
                                    type="checkbox"
                                    checked={selectedIds.size === results.length && results.length > 0}
                                    onChange={toggleAll}
                                    disabled={exporting || results.length === 0}
                                />
                                全选 ({selectedCount}/{results.length})
                            </label>
                        </div>

                        <div className="export-select-hint">
                            导出时会优先按任务名创建子目录；如果同名结果已存在且内容不同，会自动追加后缀避免覆盖。
                        </div>

                        <ul className="export-result-list">
                            {sortedResults.map((result) => {
                                const engineMeta = getDinsarEngineMeta(result.engine_code);
                                return (
                                    <li key={result.id} className="export-result-item">
                                        <label>
                                            <input
                                                type="checkbox"
                                                checked={selectedIds.has(result.id)}
                                                onChange={() => toggleSelect(result.id)}
                                                disabled={exporting}
                                            />
                                            <span className="export-result-name" title={result.file_path || result.name}>
                                                {result.name}
                                            </span>
                                            <span className={`dinsar-engine-badge tone-${engineMeta.tone}`}>
                                                {engineMeta.shortLabel}
                                            </span>
                                        </label>
                                    </li>
                                );
                            })}
                        </ul>
                    </div>

                    {error && <div className="export-error">{error}</div>}

                    {exportResult && (
                        <div className="export-summary">
                            <div className="export-summary-title">提取完成</div>
                            <div className="export-summary-stats">
                                <span className="stat-ok">复制: {exportResult.copied}</span>
                                <span className="stat-skip">跳过: {exportResult.skipped}</span>
                                {exportResult.failed > 0 && (
                                    <span className="stat-fail">失败: {exportResult.failed}</span>
                                )}
                            </div>
                            <div className="export-summary-dir">
                                目标目录: {exportResult.target_dir}
                            </div>
                        </div>
                    )}
                </div>

                <div className="modal-footer">
                    <button type="button" className="btn-secondary" onClick={onClose} disabled={exporting}>
                        关闭
                    </button>
                    <button
                        type="button"
                        onClick={handleExport}
                        disabled={exporting || selectedIds.size === 0 || !targetDir.trim()}
                        className="btn-primary"
                    >
                        {exporting ? '提取中...' : `确定提取 ${selectedIds.size} 个结果`}
                    </button>
                </div>
            </div>
        </div>
    );
}
