import React, { useEffect, useState } from 'react';
import apiClient from './api/client';
import clusterDeliveryUrl from './assets/login/cluster-delivery.svg';
import processingArchitectureUrl from './assets/login/processing-architecture.svg';
import systemOverviewUrl from './assets/login/system-overview.svg';

const loginSlides = [
  {
    title: '统一管理雷达数据生产流程',
    description: '覆盖源数据入库、精密轨道匹配、生产编排、成果目录和地图分析，形成可审计的工程闭环。',
    image: systemOverviewUrl,
  },
  {
    title: '面向 D-InSAR / SBAS 的处理链路',
    description: '扫描与解析先行，生产任务进入队列，运行结果和失败归因统一登记，便于复查和补跑。',
    image: processingArchitectureUrl,
  },
  {
    title: '支持集群生产与成果交付',
    description: '主服务器负责资产、权限和审计，生产节点承担受控处理服务，结果回传后进入交付申请与下载流程。',
    image: clusterDeliveryUrl,
  },
];

const LoginPage = ({ onLoginSuccess }) => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [licenseStatus, setLicenseStatus] = useState(null);
  const [activeSlide, setActiveSlide] = useState(0);

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

  useEffect(() => {
    const timer = window.setInterval(() => {
      setActiveSlide((current) => (current + 1) % loginSlides.length);
    }, 5200);
    return () => window.clearInterval(timer);
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
      <div className="login-shell">
        <section className="login-card" aria-label="系统登录">
          <div className="login-card-header">
            <div className="login-brand-tag">InSAR Production</div>
            <h2>雷达数据生产管理系统</h2>
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
            <button type="submit" className="primary-btn login-submit-btn" disabled={loading}>
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
        </section>

        <section className="login-visual-panel" aria-label="系统能力介绍">
          <div className="login-slide-copy">
            <span>系统能力</span>
            <h3>{loginSlides[activeSlide].title}</h3>
            <p>{loginSlides[activeSlide].description}</p>
          </div>
          <div className="login-slide-frame">
            {loginSlides.map((slide, index) => (
              <img
                key={slide.title}
                src={slide.image}
                alt={slide.title}
                className={`login-slide-image ${index === activeSlide ? 'active' : ''}`}
                aria-hidden={index !== activeSlide}
              />
            ))}
          </div>
          <div className="login-slide-dots" aria-label="切换系统介绍图">
            {loginSlides.map((slide, index) => (
              <button
                key={slide.title}
                type="button"
                className={index === activeSlide ? 'active' : ''}
                aria-label={`查看${slide.title}`}
                onClick={() => setActiveSlide(index)}
              />
            ))}
          </div>
        </section>
      </div>
    </div>
  );
};

export default LoginPage;
