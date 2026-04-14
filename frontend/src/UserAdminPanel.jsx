import React, { useCallback, useEffect, useMemo, useState } from 'react';


const DEFAULT_FORM = {
  username: '',
  password: '',
  role: 'viewer',
  is_active: true,
};

const getRoleLabel = (role) => (role === 'admin' ? '管理员' : '只读');

const isErrorMessage = (text) => /(失败|错误|error)/i.test(text || '');

const formatDateTime = (value, fallback = '-') => {
  if (!value) return fallback;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return fallback;
  return date.toLocaleString();
};


const UserAdminPanel = ({ apiClient, currentUser }) => {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [form, setForm] = useState(DEFAULT_FORM);
  const [draftMap, setDraftMap] = useState({});
  const [keyword, setKeyword] = useState('');
  const [roleFilter, setRoleFilter] = useState('all');

  const sortedUsers = useMemo(
    () => [...users].sort((a, b) => (a.username || '').localeCompare(b.username || '')),
    [users],
  );

  const summary = useMemo(() => {
    const total = users.length;
    const admin = users.filter((item) => item.role === 'admin').length;
    const active = users.filter((item) => Boolean(item.is_active)).length;
    return { total, admin, active };
  }, [users]);

  const filteredUsers = useMemo(() => {
    const normalizedKeyword = keyword.trim().toLowerCase();
    return sortedUsers.filter((item) => {
      if (roleFilter !== 'all' && item.role !== roleFilter) {
        return false;
      }
      if (!normalizedKeyword) {
        return true;
      }
      return (item.username || '').toLowerCase().includes(normalizedKeyword);
    });
  }, [sortedUsers, keyword, roleFilter]);

  const buildDrafts = (items) => {
    const next = {};
    (items || []).forEach((item) => {
      next[item.id] = {
        role: item.role,
        is_active: Boolean(item.is_active),
        password: '',
      };
    });
    return next;
  };

  const loadUsers = useCallback(async () => {
    setLoading(true);
    setMessage('');
    try {
      const response = await apiClient.get('/auth/users');
      const list = response.data || [];
      setUsers(list);
      setDraftMap(buildDrafts(list));
    } catch (error) {
      setMessage(error.response?.data?.detail || '加载用户列表失败。');
    } finally {
      setLoading(false);
    }
  }, [apiClient]);

  useEffect(() => {
    loadUsers();
  }, [loadUsers]);

  const handleCreateUser = async (event) => {
    event.preventDefault();
    setMessage('');
    setLoading(true);
    try {
      await apiClient.post('/auth/users', form);
      setForm({ ...DEFAULT_FORM });
      await loadUsers();
      setMessage('用户创建成功。');
    } catch (error) {
      setMessage(error.response?.data?.detail || '创建用户失败。');
    } finally {
      setLoading(false);
    }
  };

  const updateDraft = (id, field, value) => {
    setDraftMap((prev) => ({
      ...prev,
      [id]: {
        ...(prev[id] || {}),
        [field]: value,
      },
    }));
  };

  const handleUpdateUser = async (user) => {
    const draft = draftMap[user.id] || {};
    const payload = {
      role: draft.role,
      is_active: draft.is_active,
    };
    if (draft.password) {
      payload.password = draft.password;
    }

    setLoading(true);
    setMessage('');
    try {
      await apiClient.patch(`/auth/users/${user.id}`, payload);
      await loadUsers();
      setMessage(`用户 ${user.username} 更新成功。`);
    } catch (error) {
      setMessage(error.response?.data?.detail || `更新用户 ${user.username} 失败。`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="user-admin-layout">
      <div className="panel-card user-admin-hero">
        <div className="user-admin-head">
          <div>
            <div className="panel-card-title">用户与权限管理</div>
            <p className="panel-card-desc">管理员可创建账号、分配角色和启用/禁用用户。</p>
          </div>
          <button type="button" className="secondary-btn" onClick={loadUsers} disabled={loading}>
            {loading ? '刷新中...' : '刷新'}
          </button>
        </div>
        <div className="user-admin-summary">
          <div className="user-admin-stat">
            <span>总用户</span>
            <strong>{summary.total}</strong>
          </div>
          <div className="user-admin-stat">
            <span>管理员</span>
            <strong>{summary.admin}</strong>
          </div>
          <div className="user-admin-stat">
            <span>已启用</span>
            <strong>{summary.active}</strong>
          </div>
          <div className="user-admin-stat">
            <span>当前用户</span>
            <strong>{currentUser?.username || '-'}</strong>
          </div>
        </div>
      </div>

      <div className="panel-card">
        <div className="panel-card-title">新增用户</div>
        <form onSubmit={handleCreateUser} className="user-admin-create-form">
          <label className="user-admin-field">
            <span>用户名</span>
            <input
              type="text"
              value={form.username}
              onChange={(event) => setForm((prev) => ({ ...prev, username: event.target.value }))}
              placeholder="字母/数字/._-"
              required
            />
          </label>
          <label className="user-admin-field">
            <span>初始密码</span>
            <input
              type="password"
              value={form.password}
              onChange={(event) => setForm((prev) => ({ ...prev, password: event.target.value }))}
              placeholder="至少 8 位"
              required
            />
          </label>
          <div className="user-admin-form-row">
            <label className="user-admin-field compact">
              <span>角色</span>
              <select
                value={form.role}
                onChange={(event) => setForm((prev) => ({ ...prev, role: event.target.value }))}
              >
                <option value="viewer">只读（viewer）</option>
                <option value="admin">管理员（admin）</option>
              </select>
            </label>
            <label className="user-admin-checkbox">
              <input
                type="checkbox"
                checked={form.is_active}
                onChange={(event) => setForm((prev) => ({ ...prev, is_active: event.target.checked }))}
              />
              启用
            </label>
            <button type="submit" className="primary-btn" disabled={loading}>
              创建用户
            </button>
          </div>
        </form>
      </div>

      <div className="panel-card user-admin-list-card">
        <div className="user-admin-list-toolbar">
          <input
            type="text"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            placeholder="按用户名筛选"
          />
          <select value={roleFilter} onChange={(event) => setRoleFilter(event.target.value)}>
            <option value="all">全部角色</option>
            <option value="admin">管理员</option>
            <option value="viewer">只读</option>
          </select>
        </div>

        <div className="user-admin-list">
          {filteredUsers.map((user) => {
            const draft = draftMap[user.id] || { role: user.role, is_active: user.is_active, password: '' };
            const isSelf = currentUser?.id === user.id;
            return (
              <div key={user.id} className="user-entry-card">
                <div className="user-entry-header">
                  <div className="user-entry-name-wrap">
                    <strong>{user.username}</strong>
                    <span className={`role-pill ${user.role === 'admin' ? 'admin' : 'viewer'}`}>
                      {getRoleLabel(user.role)}
                    </span>
                    {!user.is_active && <span className="state-pill">已禁用</span>}
                    {isSelf && <span className="state-pill self">当前账号</span>}
                  </div>
                  <div className="user-entry-meta">
                    <span>创建：{formatDateTime(user.created_at, '-')}</span>
                    <span>最近登录：{formatDateTime(user.last_login_at, '从未')}</span>
                  </div>
                </div>

                <div className="user-entry-controls">
                  <label className="user-admin-field compact">
                    <span>角色</span>
                    <select
                      value={draft.role}
                      onChange={(event) => updateDraft(user.id, 'role', event.target.value)}
                      disabled={isSelf}
                    >
                      <option value="viewer">只读（viewer）</option>
                      <option value="admin">管理员（admin）</option>
                    </select>
                  </label>
                  <label className="user-admin-checkbox">
                    <input
                      type="checkbox"
                      checked={Boolean(draft.is_active)}
                      onChange={(event) => updateDraft(user.id, 'is_active', event.target.checked)}
                      disabled={isSelf}
                    />
                    启用
                  </label>
                  <label className="user-admin-field compact password-field">
                    <span>重置密码</span>
                    <input
                      type="password"
                      value={draft.password || ''}
                      onChange={(event) => updateDraft(user.id, 'password', event.target.value)}
                      placeholder="留空不修改"
                    />
                  </label>
                  <button type="button" className="primary-btn" onClick={() => handleUpdateUser(user)} disabled={loading}>
                    保存变更
                  </button>
                </div>
                {isSelf && <div className="user-entry-note">当前登录账号不可降权或禁用。</div>}
              </div>
            );
          })}
          {filteredUsers.length === 0 && !loading && (
            <div className="empty-state">当前筛选条件下暂无用户。</div>
          )}
        </div>
      </div>

      {message && (
        <div className={`panel-inline-message ${isErrorMessage(message) ? 'error' : 'success'}`}>
          {message}
        </div>
      )}
    </div>
  );
};


export default UserAdminPanel;
