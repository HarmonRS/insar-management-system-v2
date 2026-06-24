import React, { useState, useEffect, useCallback } from 'react';
import { listLogs, getLogContent, deleteLog } from './api/logs';

const PAGE_SIZE = 1000;

const LogManagementPanel = ({ isAdmin }) => {
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selectedLog, setSelectedLog] = useState(null);
  const [logContent, setLogContent] = useState('');
  const [showModal, setShowModal] = useState(false);
  const [filterType, setFilterType] = useState('');
  const [totalLines, setTotalLines] = useState(0);
  const [currentOffset, setCurrentOffset] = useState(0);
  const [searchTerm, setSearchTerm] = useState('');
  const [message, setMessage] = useState('');
  const [errorMessage, setErrorMessage] = useState('');

  const getErrorText = error => error.response?.data?.detail || error.message || '操作失败';

  const loadLogs = useCallback(async () => {
    setLoading(true);
    setErrorMessage('');
    try {
      const data = await listLogs(filterType || null);
      setLogs(Array.isArray(data) ? data : []);
    } catch (error) {
      console.error('加载日志列表失败:', error);
      setErrorMessage(`日志列表加载失败：${getErrorText(error)}`);
    } finally {
      setLoading(false);
    }
  }, [filterType]);

  const loadLogContent = useCallback(async (logPath, offset = 0) => {
    setErrorMessage('');
    try {
      const data = await getLogContent(logPath, offset, PAGE_SIZE);
      setLogContent(data.content || '');
      setTotalLines(data.total_lines || 0);
      setCurrentOffset(offset);
    } catch (error) {
      console.error('加载日志内容失败:', error);
      setErrorMessage(`日志内容加载失败：${getErrorText(error)}`);
    }
  }, []);

  useEffect(() => {
    loadLogs();
  }, [loadLogs]);

  const handleViewLog = async log => {
    setSelectedLog(log);
    setShowModal(true);
    setCurrentOffset(0);
    setSearchTerm('');
    setMessage('');
    await loadLogContent(log.path, 0);
  };

  const handleDeleteLog = async log => {
    if (!isAdmin) {
      setErrorMessage('当前账号没有日志删除权限。');
      return;
    }

    if (!window.confirm(`确定要删除日志文件“${log.name}”吗？\n\n此操作不可恢复。`)) {
      return;
    }

    setMessage('');
    setErrorMessage('');
    try {
      await deleteLog(log.path);
      setMessage('日志文件已删除。');
      await loadLogs();
      if (selectedLog && selectedLog.path === log.path) {
        setShowModal(false);
        setSelectedLog(null);
        setLogContent('');
        setTotalLines(0);
        setCurrentOffset(0);
      }
    } catch (error) {
      console.error('删除日志失败:', error);
      setErrorMessage(`日志删除失败：${getErrorText(error)}`);
    }
  };

  const handlePrevPage = () => {
    if (selectedLog && currentOffset > 0) {
      loadLogContent(selectedLog.path, Math.max(0, currentOffset - PAGE_SIZE));
    }
  };

  const handleNextPage = () => {
    if (selectedLog && currentOffset + PAGE_SIZE < totalLines) {
      loadLogContent(selectedLog.path, currentOffset + PAGE_SIZE);
    }
  };

  const formatSize = bytes => {
    const size = Number(bytes) || 0;
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
    return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  };

  const getTypeLabel = type => {
    const labels = {
      app: '应用日志',
      task: '任务日志',
      error: '错误日志',
      other: '其他',
    };
    return labels[type] || type || '其他';
  };

  const filteredContent = searchTerm
    ? logContent
        .split('\n')
        .filter(line => line.toLowerCase().includes(searchTerm.toLowerCase()))
        .join('\n')
    : logContent;

  const pageStart = totalLines === 0 ? 0 : currentOffset + 1;
  const pageEnd = Math.min(currentOffset + PAGE_SIZE, totalLines);

  return (
    <div className="log-management-panel">
      <div className="log-management-header">
        <div>
          <h3>日志审计</h3>
          <p>集中查看应用、任务与错误日志；删除操作仅限管理员。</p>
        </div>
        <div className="log-management-controls">
          <label className="ops-field">
            <span>日志类型</span>
            <select value={filterType} onChange={event => setFilterType(event.target.value)}>
              <option value="">全部日志</option>
              <option value="app">应用日志</option>
              <option value="task">任务日志</option>
              <option value="error">错误日志</option>
            </select>
          </label>
          <button className="ops-button ops-button--primary" onClick={loadLogs} disabled={loading}>
            {loading ? '刷新中...' : '刷新'}
          </button>
        </div>
      </div>

      {errorMessage && <div className="ops-message ops-message--error">{errorMessage}</div>}
      {message && <div className="ops-message ops-message--success">{message}</div>}

      {logs.length === 0 ? (
        <div className="log-empty-state">{loading ? '正在读取日志目录...' : '暂无可展示的日志文件'}</div>
      ) : (
        <div className="log-table-wrap">
          <table className="log-table">
            <thead>
              <tr>
                <th>文件名</th>
                <th>类型</th>
                <th>大小</th>
                <th>修改时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {logs.map(log => (
                <tr key={log.path || log.name}>
                  <td className="log-file-name">{log.name}</td>
                  <td>
                    <span className={`log-type-badge log-type-badge--${log.type || 'other'}`}>
                      {getTypeLabel(log.type)}
                    </span>
                  </td>
                  <td className="log-number-cell">{formatSize(log.size)}</td>
                  <td>{log.modified_at || '-'}</td>
                  <td>
                    <div className="log-row-actions">
                      <button className="ops-button ops-button--secondary ops-button--sm" onClick={() => handleViewLog(log)}>
                        查看
                      </button>
                      {isAdmin && (
                        <button className="ops-button ops-button--danger ops-button--sm" onClick={() => handleDeleteLog(log)}>
                          删除
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showModal && selectedLog && (
        <div className="log-modal-backdrop">
          <div className="log-modal" role="dialog" aria-modal="true" aria-label="日志内容">
            <div className="log-modal-header">
              <div>
                <h3>{selectedLog.name}</h3>
                <p>
                  大小：{formatSize(selectedLog.size)} · 修改时间：{selectedLog.modified_at || '-'} · 总行数：{totalLines}
                </p>
              </div>
              <button className="ops-button ops-button--secondary" onClick={() => setShowModal(false)}>
                关闭
              </button>
            </div>

            <div className="log-modal-tools">
              <input
                type="text"
                placeholder="搜索日志内容"
                value={searchTerm}
                onChange={event => setSearchTerm(event.target.value)}
              />
              <div className="log-page-info">
                显示行 {pageStart} - {pageEnd}
              </div>
              <button className="ops-button ops-button--secondary ops-button--sm" onClick={handlePrevPage} disabled={currentOffset === 0}>
                上一页
              </button>
              <button
                className="ops-button ops-button--secondary ops-button--sm"
                onClick={handleNextPage}
                disabled={currentOffset + PAGE_SIZE >= totalLines}
              >
                下一页
              </button>
            </div>

            <div className="log-content-view">
              <pre>{filteredContent || '（空日志）'}</pre>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default LogManagementPanel;
