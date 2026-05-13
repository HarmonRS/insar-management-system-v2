import axios from 'axios';

const apiClient = axios.create({
    baseURL: '/api',
    withCredentials: true,
    paramsSerializer: {
        indexes: null,
    },
});

export default apiClient;
