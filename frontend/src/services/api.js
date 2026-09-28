import axios from 'axios';

const apiBaseUrl = process.env.REACT_APP_API_URL ?? (
  process.env.NODE_ENV === 'production' ? '' : 'http://localhost:8000'
);

const API = axios.create({
  baseURL: apiBaseUrl,
  timeout: 600000
});

export default API;