
import axios from 'axios';

// Since the frontend is served from the same origin as the API, we can use relative paths
// or just '/' as base.
export const apiClient = axios.create({
    baseURL: '/',
    headers: {
        'Content-Type': 'application/json',
    },
});

apiClient.interceptors.response.use(
    (response) => response,
    (error) => {
        console.error('API Error:', error.response?.data || error.message);
        return Promise.reject(error);
    }
);
