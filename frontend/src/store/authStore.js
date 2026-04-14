import { create } from 'zustand';

// 支持函数式更新的 setter 工厂
const s = (set, key) => (v) =>
    set((state) => ({ [key]: typeof v === 'function' ? v(state[key]) : v }));

export const useAuthStore = create((set) => ({
    currentUser: null,
    authChecked: false,
    licenseStatus: { ok: false, reason: '', expires_at: null },
    licenseLoading: true,
    licenseUploadStatus: { type: '', message: '' },
    licenseFileName: '',
    healthStatus: null,
    healthLoading: false,
    healthError: '',
    setCurrentUser: s(set, 'currentUser'),
    setAuthChecked: s(set, 'authChecked'),
    setLicenseStatus: s(set, 'licenseStatus'),
    setLicenseLoading: s(set, 'licenseLoading'),
    setLicenseUploadStatus: s(set, 'licenseUploadStatus'),
    setLicenseFileName: s(set, 'licenseFileName'),
    setHealthStatus: s(set, 'healthStatus'),
    setHealthLoading: s(set, 'healthLoading'),
    setHealthError: s(set, 'healthError'),
}));
