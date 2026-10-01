import axios, { AxiosError, type AxiosRequestConfig } from 'axios'
import type { Locale } from '../i18n'

const tokenKey = 'orderflow.access_token'
export const authExpiredEvent = 'orderflow:auth-expired'

const configuredBaseUrl = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/+$/, '')
const baseURL = configuredBaseUrl.endsWith('/api/v1') ? configuredBaseUrl : `${configuredBaseUrl}/api/v1`

export function getAccessToken(): string | null {
  try {
    return window.sessionStorage.getItem(tokenKey)
  } catch {
    return null
  }
}

export function setAccessToken(token: string): void {
  window.sessionStorage.setItem(tokenKey, token)
}

export function clearAccessToken(): void {
  try {
    window.sessionStorage.removeItem(tokenKey)
  } catch {
    // The in-memory auth state is cleared by AuthProvider as well.
  }
}

export const api = axios.create({ baseURL, timeout: 15000 })

api.interceptors.request.use((config) => {
  const token = getAccessToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (response) => response,
  (error: unknown) => {
    if (axios.isAxiosError(error) && error.response?.status === 401 && !error.config?.url?.endsWith('/auth/login')) {
      clearAccessToken()
      window.dispatchEvent(new Event(authExpiredEvent))
    }
    return Promise.reject(error)
  },
)

export type ApiError = AxiosError<{ detail?: unknown }>

export function getApiErrorMessage(error: unknown, locale: Locale = 'pt-BR'): string {
  const english = locale === 'en'
  if (!axios.isAxiosError(error)) return english ? 'An unexpected error occurred.' : 'Ocorreu um erro inesperado.'
  if (!error.response) return english ? 'Unable to reach the API. Check your connection.' : 'Não foi possível acessar a API. Verifique sua conexão.'

  const status = error.response.status
  if (status === 401) return english ? 'Your session is invalid or has expired.' : 'Sua sessão é inválida ou expirou.'
  if (status === 403) return english ? 'You do not have permission for this action.' : 'Você não tem permissão para esta ação.'
  if (status === 404) return english ? 'The requested item was not found.' : 'O item solicitado não foi encontrado.'
  if (status === 409) return english ? 'This action conflicts with the current state. Refresh and try again.' : 'Esta ação não combina com o estado atual. Atualize e tente novamente.'
  if (status === 422) return english ? 'Check the entered information and try again.' : 'Confira os dados informados e tente novamente.'
  if (status >= 500) return english ? 'The server could not complete the request. Try again later.' : 'O servidor não concluiu a solicitação. Tente novamente mais tarde.'

  const detail: unknown = (error.response.data as { detail?: unknown } | undefined)?.detail
  if (typeof detail === 'string' && detail.length < 240) return detail
  return english ? 'Unable to complete the request.' : 'Não foi possível concluir a solicitação.'
}

export function withIdempotencyKey(key: string): AxiosRequestConfig {
  return { headers: { 'Idempotency-Key': key } }
}
