import { useState } from 'react';
import { exportDinsarResults } from '../api/dinsar';

export default function ResultExportModal({ results, onClose }) {
    const [targetDir, setTargetDir] = useState('');
    const [selectedIds, setSelectedIds] = useState(() => new Set(results.map(r => r.id)));
    const [exporting, setExporting] = useState(false);
    const [exportResult, setExportResult] = useState(null);
    const [error, setError] = useState('');

    const toggleSelect = (id) => {
        setSelectedIds(prev => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
    };

    const toggleAll = () => {
        if (selectedIds.size === results.length) {
            setSelectedIds(new Set());
        } else {
            setSelectedIds(new Set(results.map(r => r.id)));
        }
    };

    const handleExport = async () => {
        const dir = targetDir.trim();
        if (!dir) {
            setError('请输入目标路径');
            return;
        }
        if (selectedIds.size === 0) {
            setError('请至少选择一个结果');
            return;
        }
        setError('');
        setExporting(true);
        setExportResult(null);
        try {
            const res = await exportDinsarResults([...selectedIds], dir);
            setExportResult(res);
        } catch (e) {
            setError(e.response?.data?.detail || e.message || '导出失败');
        } finally {
            setExporting(false);
        }
    };

    return (
        <div className="modal-overlay visible" onClick={onClose}>
            <div className="modal-content result-export-modal" onClick={e => e.stopPropagation()}>
                <div className="modal-header">
                    <h3>提取 D-InSAR 结果</h3>
                    <button className="modal-close-btn" onClick={onClose}>&times;</button>
                </div>

                <div className="modal-body">
                    <div className="export-path-section">
                        <label>目标路径（支持 UNC 路径，如 \\\\server\\share\\path）</label>
                        <input
                            type="text"
                            value={targetDir}
                            onChange={e => setTargetDir(e.target.value)}
                            placeholder="例如: D:\Export\Results 或 \\server\share\results"
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
                                    disabled={exporting}
                                />
                                全选 ({selectedIds.size}/{results.length})
                            </label>
                        </div>
                        <ul className="export-result-list">
                            {results.map(r => (
                                <li key={r.id} className="export-result-item">
                                    <label>
                                        <input
                                            type="checkbox"
                                            checked={selectedIds.has(r.id)}
                                            onChange={() => toggleSelect(r.id)}
                                            disabled={exporting}
                                        />
                                        <span className="export-result-name" title={r.file_path || r.name}>
                                            {r.name}
                                        </span>
                                    </label>
                                </li>
                            ))}
                        </ul>
                    </div>

                    {error && <div className="export-error">{error}</div>}

                    {exportResult && (
                        <div className="export-summary">
                            <div className="export-summary-title">导出完成</div>
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
                    <button onClick={onClose} disabled={exporting}>关闭</button>
                    <button
                        onClick={handleExport}
                        disabled={exporting || selectedIds.size === 0 || !targetDir.trim()}
                        className="btn-primary"
                    >
                        {exporting ? '导出中...' : `提取 ${selectedIds.size} 个结果`}
                    </button>
                </div>
            </div>
        </div>
    );
}
