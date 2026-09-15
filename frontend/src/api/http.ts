import axios from 'axios'
import type { AxiosRequestConfig } from 'axios'

const instance = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '/api/v1',
  timeout: 60000,
})

instance.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

instance.interceptors.response.use(
  (res) => res.data,
  (err) => {
    if (err.response?.status === 401) {
      localStorage.removeItem('token')
      localStorage.removeItem('user')
      window.location.href = '/login'
    }
    return Promise.reject(err)
  },
)

// 响应拦截器已经把 AxiosResponse 剥成 res.data，但 axios 的类型签名仍认为返回
// AxiosResponse<T>。这里把方法签名重写成「直接 resolve 业务数据」，这样各处调用
// 只需写一个泛型：http.get<IKb[]>('/knowledge-bases') → Promise<IKb[]>
type Http = {
  <T = any>(config: AxiosRequestConfig): Promise<T>
  get<T = any>(url: string, config?: AxiosRequestConfig): Promise<T>
  post<T = any>(url: string, data?: any, config?: AxiosRequestConfig): Promise<T>
  put<T = any>(url: string, data?: any, config?: AxiosRequestConfig): Promise<T>
  patch<T = any>(url: string, data?: any, config?: AxiosRequestConfig): Promise<T>
  delete<T = any>(url: string, config?: AxiosRequestConfig): Promise<T>
  request<T = any>(config: AxiosRequestConfig): Promise<T>
}

const http = instance as unknown as Http

export default http
