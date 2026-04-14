import React, { useCallback, useEffect, useMemo, useState } from 'react';


const LIMIT_OPTIONS = [100, 200, 500, 1000];

const ACTION_LABELS = {
  write_access_granted: '写操作放行',
  write_blocked_readonly: '只读拦截',
  write_auth_required: '未登录拦截',
  login_success: '登录成功',
  login_failed: '登录失败',
  logout: '退出登录',
  user_created: '创建用户',
  user_updated: '更新用户',
  license_uploaded: '上传授权文件',
  license_upload_failed: '上传授权失败',
  license_refreshed: '刷新授权状态',
  task_queued: '任务入队',
  batch_created: '创建任务批次',
  batch_marked_complete: '批次标记完成',
  batch_item_updated: '更新批次条目',
  dinsar_label_updated: '更新D-InSAR标签',
};


const getActionLabel = (action) => ACTION_LABELS[action] || action || '未知动作';

const getOutcome = (action) => {
  const normalized = (action || '').toLowerCase();
  if (normalized.includes('failed') || normalized.includes('blocked') || normalized.includes('required')) {
    return 'blocked';
  }
  return 'ok';
};

const formatTime = (value) => {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '-';
  return date.toLocaleString();
};

const formatDetail = (detail) => {
  if (detail === null || detail === undefined || detail === '') {
    return '-';
  }
  if (typeof detail === 'string') {
    return detail;
  }
  try {
    return JSON.stringify(detail);
  } catch {
    return '[detail 解析失败]';
  }
};


const AuditLogPanel = ({ apiClient }) => {
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [limit, setLimit] = useState(200);
  const [keyword, setKeyword] = useState('');
  const [actionFilter, setActionFilter] = useState('all');
  const [outcomeFilter, setOutcomeFilter] = useState('all');
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [lastUpdated, setLastUpdated] = useState(null);

  const loadLogs = useCallback(async ({ silent = false } = {}) => {
    if (!silent) {
      setLoading(true);
      setMessage('');
    }
    try {
      const response = await apiClient.get('/auth/audit-logs', { params: { limit } });
      const data = Array.isArray(response.data) ? response.data : [];
      setLogs(data);
      setLastUpdated(new Date());
    } catch (error) {
      setMessage(error.response?.data?.detail || '加载审计日志失败。');
    } finally {
      if (!silent) {
        setLoading(false);
      }
    }
  }, [apiClient, limit]);

  useEffect(() => {
    loadLogs();
  }, [loadLogs]);

  useEffect(() => {
    if (!autoRefresh) return undefined;
    const timer = setInterval(() => {
      loadLogs({ silent: true });
    }, 15000);
    return () => clearInterval(timer);
  }, [autoRefresh, loadLogs]);

  const actionOptions = useMemo(() => {
    return Array.from(new Set(logs.map((item) => item.action).filter(Boolean))).sort();
  }, [logs]);

  const filteredLogs = useMemo(() => {
    const normalizedKeyword = keyword.trim().toLowerCase();

    return logs.filter((item) => {
      if (actionFilter !== 'all' && item.action !== actionFilter) {
        return false;
      }

      const outcome = getOutcome(item.action);
      if (outcomeFilter !== 'all' && outcome !== outcomeFilter) {
        return false;
      }

      if (!normalizedKeyword) {
        return true;
      }

      const detailText = formatDetail(item.detail).toLowerCase();
      const searchText = [
        item.username || '',
        item.action || '',
        item.resource || '',
        item.ip_address || '',
        detailText,
      ].join(' ').toLowerCase();

      return searchText.includes(normalizedKeyword);
    });
  }, [logs, actionFilter, outcomeFilter, keyword]);

  const summary = useMemo(() => {
    const total = filteredLogs.length;
    const blocked = filteredLogs.filter((item) => getOutcome(item.action) === 'blocked').length;
    const passed = total - blocked;
    return { total, blocked, passed };
  }, [filteredLogs]);

  return (
    <div className="audit-panel">
      <div className="panel-card audit-panel-header">
        <div className="audit-header-title">
          <div className="panel-card-title">审计日志</div>
          <p className="panel-card-desc">记录关键操作、权限拦截和鉴权行为，便于排查问题。</p>
        </div>
        <div className="audit-header-actions">
          <label className="audit-switch">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(event) => setAutoRefresh(event.target.checked)}
            />
            自动刷新（15s）
          </label>
          <button
            type="button"
            className="secondary-btn"
            onClick={() => loadLogs()}
            disabled={loading}
          >
            {loading ? '刷新中...' : '立即刷新'}
          </button>
        </div>
      </div>

      <div className="panel-card audit-summary-card">
        <div className="audit-summary-grid">
          <div className="audit-summary-item">
            <span>当前显示</span>
            <strong>{summary.total}</strong>
          </div>
          <div className="audit-summary-item ok">
            <span>通过</span>
            <strong>{summary.passed}</strong>
          </div>
          <div className="audit-summary-item blocked">
            <span>拦截/失败</span>
            <strong>{summary.blocked}</strong>
          </div>
          <div className="audit-summary-item">
            <span>更新时间</span>
            <strong>{lastUpdated ? lastUpdated.toLocaleTimeString() : '-'}</strong>
          </div>
        </div>
      </div>

      <div className="panel-card audit-filter-card">
        <div className="audit-filter-grid">
          <label className="audit-filter-field">
            <span>关键字</span>
            <input
              type="text"
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              placeholder="用户名 / 动作 / 资源 / 详情"
            />
          </label>
          <label className="audit-filter-field">
            <span>动作类型</span>
            <select value={actionFilter} onChange={(event) => setActionFilter(event.target.value)}>
              <option value="all">全部动作</option>
              {actionOptions.map((action) => (
                <option key={action} value={action}>
                  {getActionLabel(action)}
                </option>
              ))}
            </select>
          </label>
          <label className="audit-filter-field">
            <span>结果</span>
            <select value={outcomeFilter} onChange={(event) => setOutcomeFilter(event.target.value)}>
              <option value="all">全部结果</option>
              <option value="ok">通过</option>
              <option value="blocked">拦截/失败</option>
            </select>
          </label>
          <label className="audit-filter-field">
            <span>返回条数</span>
            <select value={limit} onChange={(event) => setLimit(Number(event.target.value) || 200)}>
              {LIMIT_OPTIONS.map((value) => (
                <option key={value} value={value}>
                  最近 {value} 条
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>

      <div className="panel-card audit-list-card">
        <div className="audit-list">
          {filteredLogs.map((item) => {
            const outcome = getOutcome(item.action);
            return (
              <div key={item.id} className="audit-log-item">
                <div className="audit-log-top">
                  <div className="audit-log-main">
                    <span className={`audit-outcome ${outcome}`}>{outcome === 'ok' ? '通过' : '拦截/失败'}</span>
                    <strong>{getActionLabel(item.action)}</strong>
                    <span className="audit-operator">{item.username || '匿名/未登录'}</span>
                  </div>
                  <span className="audit-time">{formatTime(item.created_at)}</span>
                </div>
                <div className="audit-log-meta">
                  <span>资源：{item.resource || '-'}</span>
                  <span>IP：{item.ip_address || '-'}</span>
                </div>
                <div className="audit-log-detail">详情：{formatDetail(item.detail)}</div>
              </div>
            );
          })}
          {!loading && filteredLogs.length === 0 && (
            <div className="empty-state">当前筛选条件下暂无日志。</div>
          )}
        </div>
      </div>

      {message && (
        <div className="panel-inline-message error">
          {message}
        </div>
      )}
    </div>
  );
};


export default AuditLogPanel;
