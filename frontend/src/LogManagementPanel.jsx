import React, { useState, useEffect, useCallback } from 'react';
import { listLogs, getLogContent, deleteLog } from './api/logs';

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

  const loadLogs = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listLogs(filterType || null);
      setLogs(data);
    } catch (error) {
      console.error('加载日志列表失败:', error);
      alert(`加载日志列表失败: ${error.response?.data?.detail || error.message}`);
    } finally {
      setLoading(false);
    }
  }, [filterType]);

  useEffect(() => {
    loadLogs();
  }, [loadLogs]);

  const handleViewLog = async (log) => {
    setSelectedLog(log);
    setShowModal(true);
    setCurrentOffset(0);
    setSearchTerm('');
    await loadLogContent(log.path, 0);
  };

  const loadLogContent = async (logPath, offset = 0) => {
    try {
      const data = await getLogContent(logPath, offset, 1000);
      setLogContent(data.content);
      setTotalLines(data.total_lines);
      setCurrentOffset(offset);
    } catch (error) {
      console.error('加载日志内容失败:', error);
      alert(`加载日志内容失败: ${error.response?.data?.detail || error.message}`);
    }
  };

  const handleDeleteLog = async (log) => {
    if (!isAdmin) {
      alert('只有管理员可以删除日志');
      return;
    }

    if (!window.confirm(`确定要删除日志文件 "${log.name}" 吗？\n\n此操作不可恢复！`)) {
      return;
    }

    try {
      await deleteLog(log.path);
      alert('日志文件已删除');
      loadLogs();
      if (selectedLog && selectedLog.path === log.path) {
        setShowModal(false);
      }
    } catch (error) {
      console.error('删除日志失败:', error);
      alert(`删除日志失败: ${error.response?.data?.detail || error.message}`);
    }
  };

  const handlePrevPage = () => {
    if (currentOffset > 0) {
      const newOffset = Math.max(0, currentOffset - 1000);
      loadLogContent(selectedLog.path, newOffset);
    }
  };

  const handleNextPage = () => {
    if (currentOffset + 1000 < totalLines) {
      const newOffset = currentOffset + 1000;
      loadLogContent(selectedLog.path, newOffset);
    }
  };

  const formatSize = (bytes) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const getTypeLabel = (type) => {
    const labels = {
      app: '应用日志',
      task: '任务日志',
      error: '错误日志',
      other: '其他'
    };
    return labels[type] || type;
  };

  const getTypeColor = (type) => {
    const colors = {
      app: '#3b82f6',
      task: '#10b981',
      error: '#ef4444',
      other: '#6b7280'
    };
    return colors[type] || '#6b7280';
  };

  const filteredContent = searchTerm
    ? logContent.split('\n').filter(line => line.toLowerCase().includes(searchTerm.toLowerCase())).join('\n')
    : logContent;

  return (
    <div style={{ padding: '20px' }}>
      <div style={{ marginBottom: '20px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h3 style={{ margin: 0 }}>日志管理</h3>
        <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
          <label>类型过滤:</label>
          <select
            value={filterType}
            onChange={(e) => setFilterType(e.target.value)}
            style={{ padding: '5px 10px', borderRadius: '4px', border: '1px solid #ddd' }}
          >
            <option value="">全部</option>
            <option value="app">应用日志</option>
            <option value="task">任务日志</option>
            <option value="error">错误日志</option>
          </select>
          <button
            onClick={loadLogs}
            disabled={loading}
            style={{
              padding: '5px 15px',
              backgroundColor: '#3b82f6',
              color: 'white',
              border: 'none',
              borderRadius: '4px',
              cursor: loading ? 'not-allowed' : 'pointer'
            }}
          >
            {loading ? '加载中...' : '刷新'}
          </button>
        </div>
      </div>

      {logs.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '40px', color: '#6b7280' }}>
          暂无日志文件
        </div>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', backgroundColor: 'white', boxShadow: '0 1px 3px rgba(0,0,0,0.1)' }}>
          <thead>
            <tr style={{ backgroundColor: '#f3f4f6', borderBottom: '2px solid #e5e7eb' }}>
              <th style={{ padding: '12px', textAlign: 'left' }}>文件名</th>
              <th style={{ padding: '12px', textAlign: 'left' }}>类型</th>
              <th style={{ padding: '12px', textAlign: 'right' }}>大小</th>
              <th style={{ padding: '12px', textAlign: 'left' }}>修改时间</th>
              <th style={{ padding: '12px', textAlign: 'center' }}>操作</th>
            </tr>
          </thead>
          <tbody>
            {logs.map((log, index) => (
              <tr key={index} style={{ borderBottom: '1px solid #e5e7eb' }}>
                <td style={{ padding: '12px', fontFamily: 'monospace', fontSize: '13px' }}>{log.name}</td>
                <td style={{ padding: '12px' }}>
                  <span style={{
                    padding: '2px 8px',
                    borderRadius: '12px',
                    fontSize: '12px',
                    backgroundColor: getTypeColor(log.type) + '20',
                    color: getTypeColor(log.type)
                  }}>
                    {getTypeLabel(log.type)}
                  </span>
                </td>
                <td style={{ padding: '12px', textAlign: 'right', fontFamily: 'monospace', fontSize: '13px' }}>
                  {formatSize(log.size)}
                </td>
                <td style={{ padding: '12px', fontSize: '13px' }}>{log.modified_at}</td>
                <td style={{ padding: '12px', textAlign: 'center' }}>
                  <div style={{ display: 'flex', gap: '8px', justifyContent: 'center', alignItems: 'center' }}>
                    <button
                      onClick={() => handleViewLog(log)}
                      style={{
                        padding: '4px 12px',
                        backgroundColor: '#3b82f6',
                        color: 'white',
                        border: 'none',
                        borderRadius: '4px',
                        cursor: 'pointer',
                        fontSize: '13px',
                        whiteSpace: 'nowrap'
                      }}
                    >
                      查看
                    </button>
                    {isAdmin && (
                      <button
                        onClick={() => handleDeleteLog(log)}
                        style={{
                          padding: '4px 12px',
                          backgroundColor: '#ef4444',
                          color: 'white',
                          border: 'none',
                          borderRadius: '4px',
                          cursor: 'pointer',
                          fontSize: '13px',
                          whiteSpace: 'nowrap'
                        }}
                      >
                        删除
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* 日志查看 Modal */}
      {showModal && selectedLog && (
        <div style={{
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          backgroundColor: 'rgba(0, 0, 0, 0.5)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 9999
        }}>
          <div style={{
            backgroundColor: 'white',
            borderRadius: '8px',
            width: '90%',
            maxWidth: '1200px',
            maxHeight: '90vh',
            display: 'flex',
            flexDirection: 'column',
            boxShadow: '0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04)'
          }}>
            {/* Modal Header */}
            <div style={{
              padding: '20px',
              borderBottom: '1px solid #e5e7eb',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center'
            }}>
              <div>
                <h3 style={{ margin: '0 0 8px 0', fontFamily: 'monospace' }}>{selectedLog.name}</h3>
                <div style={{ fontSize: '13px', color: '#6b7280' }}>
                  大小: {formatSize(selectedLog.size)} | 修改时间: {selectedLog.modified_at} | 总行数: {totalLines}
                </div>
              </div>
              <button
                onClick={() => setShowModal(false)}
                style={{
                  padding: '8px 16px',
                  backgroundColor: '#6b7280',
                  color: 'white',
                  border: 'none',
                  borderRadius: '4px',
                  cursor: 'pointer'
                }}
              >
                关闭
              </button>
            </div>

            {/* Search Bar */}
            <div style={{ padding: '12px 20px', borderBottom: '1px solid #e5e7eb', display: 'flex', gap: '10px', alignItems: 'center' }}>
              <input
                type="text"
                placeholder="搜索日志内容..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                style={{
                  flex: 1,
                  padding: '6px 12px',
                  border: '1px solid #d1d5db',
                  borderRadius: '4px',
                  fontSize: '13px'
                }}
              />
              <div style={{ fontSize: '13px', color: '#6b7280' }}>
                显示行: {currentOffset + 1} - {Math.min(currentOffset + 1000, totalLines)}
              </div>
              <button
                onClick={handlePrevPage}
                disabled={currentOffset === 0}
                style={{
                  padding: '6px 12px',
                  backgroundColor: currentOffset === 0 ? '#e5e7eb' : '#3b82f6',
                  color: currentOffset === 0 ? '#9ca3af' : 'white',
                  border: 'none',
                  borderRadius: '4px',
                  cursor: currentOffset === 0 ? 'not-allowed' : 'pointer',
                  fontSize: '13px'
                }}
              >
                上一页
              </button>
              <button
                onClick={handleNextPage}
                disabled={currentOffset + 1000 >= totalLines}
                style={{
                  padding: '6px 12px',
                  backgroundColor: currentOffset + 1000 >= totalLines ? '#e5e7eb' : '#3b82f6',
                  color: currentOffset + 1000 >= totalLines ? '#9ca3af' : 'white',
                  border: 'none',
                  borderRadius: '4px',
                  cursor: currentOffset + 1000 >= totalLines ? 'not-allowed' : 'pointer',
                  fontSize: '13px'
                }}
              >
                下一页
              </button>
            </div>

            {/* Log Content */}
            <div style={{ flex: 1, overflow: 'auto', padding: '20px', backgroundColor: '#1e1e1e' }}>
              <pre style={{
                margin: 0,
                fontFamily: 'Consolas, Monaco, "Courier New", monospace',
                fontSize: '12px',
                lineHeight: '1.5',
                color: '#d4d4d4',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-all'
              }}>
                {filteredContent || '(空日志)'}
              </pre>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default LogManagementPanel;
