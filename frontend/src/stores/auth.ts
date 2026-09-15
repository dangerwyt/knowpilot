import { defineStore } from 'pinia'
import http from '../api/http'

interface User {
  id: string
  email: string
  name?: string
  role: string
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem('token') || '',
    user: JSON.parse(localStorage.getItem('user') || 'null') as User | null,
  }),
  getters: {
    isLoggedIn: (state) => !!state.token,
  },
  actions: {
    setAuth(payload: { token: string; user: User }) {
      this.token = payload.token
      this.user = payload.user
      localStorage.setItem('token', payload.token)
      localStorage.setItem('user', JSON.stringify(payload.user))
    },
    async login(email: string, password: string) {
      const res = await http.post<{ token: string; user: User }>('/auth/login', { email, password })
      this.setAuth(res)
    },
    async register(email: string, password: string, name?: string) {
      const res = await http.post<{ token: string; user: User }>('/auth/register', { email, password, name })
      this.setAuth(res)
    },
    logout() {
      this.token = ''
      this.user = null
      localStorage.removeItem('token')
      localStorage.removeItem('user')
    },
  },
})
