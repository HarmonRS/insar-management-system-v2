import React, { useEffect, useState } from 'react';
import apiClient from './api/client';


const LoginPage = ({ onLoginSuccess }) => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [licenseStatus, setLicenseStatus] = useState(null);

  useEffect(() => {
    const checkLicense = async () => {
      try {
        const response = await apiClient.get('/license/status');
        setLicenseStatus(response.data || null);
      } catch {
        setLicenseStatus({ ok: false, reason: '无法获取授权状态' });
      }
    };
    checkLicense();
  }, []);

  const handleSubmit = async (event) => {
    event.preventDefault();
    setLoading(true);
    setMessage('');
    try {
      const response = await apiClient.post('/auth/login', {
        username: username.trim(),
        password,
      });
      if (onLoginSuccess) {
        await onLoginSuccess(response.data?.user || null);
      }
    } catch (error) {
      const status = error.response?.status;
      if (status === 401) {
        setMessage('用户名或密码不正确。');
      } else if (status === 429) {
        setMessage(error.response?.data?.detail || '登录尝试过于频繁，请稍后再试。');
      } else {
        setMessage(error.response?.data?.detail || '登录请求失败，请稍后重试。');
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-page-wrapper">
      <div className="login-card">
        <div className="login-card-header">
          <div className="login-brand-tag">InSAR</div>
          <h2>管理系统登录</h2>
          <p className="login-subtitle">内网受控访问，请使用管理员分配的账号登录。</p>
        </div>

        {licenseStatus && !licenseStatus.ok && (
          <div className="login-alert warn">
            当前授权状态异常：{licenseStatus.reason || '未授权'}
          </div>
        )}

        <form onSubmit={handleSubmit} className="login-form-grid">
          <label className="login-field">
            <span>用户名</span>
            <input
              type="text"
              placeholder="请输入用户名"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              required
            />
          </label>
          <label className="login-field">
            <span>密码</span>
            <input
              type="password"
              placeholder="请输入密码"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </label>
          <button type="submit" className="primary-btn" disabled={loading}>
            {loading ? '登录中...' : '登录'}
          </button>
        </form>

        {message && (
          <div className="login-alert error">
            {message}
          </div>
        )}

        <div className="login-tips">
          <strong>提示：</strong>
          <span>首次部署请确认已在 `.env` 配置管理员账号与密码。</span>
        </div>
      </div>
    </div>
  );
};


export default LoginPage;
