import axios from 'axios'

const api = axios.create({ baseURL: '/' })

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 401) {
      localStorage.removeItem('token')
      if (location.pathname !== '/login') location.href = '/login'
    }
    return Promise.reject(err)
  },
)

export default api

export function wsUrl(path) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const token = encodeURIComponent(localStorage.getItem('token') || '')
  return `${proto}://${location.host}${path}?token=${token}`
}

// 本地解析 JWT payload（仅用于前端显隐控制，真正的权限校验在后端）
export function jwtPayload() {
  const token = localStorage.getItem('token')
  if (!token) return null
  try {
    const part = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')
    return JSON.parse(decodeURIComponent(
      atob(part).split('').map((c) => `%${c.charCodeAt(0).toString(16).padStart(2, '0')}`).join(''),
    ))
  } catch {
    return null
  }
}

export function isAdmin() {
  return !!jwtPayload()?.is_admin
}
