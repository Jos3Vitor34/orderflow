import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, authExpiredEvent, clearAccessToken, getAccessToken, setAccessToken } from '../../api/client'

export type UserRole = 'viewer' | 'operator' | 'admin'

export type UserPublic = {
  id: number
  full_name: string
  email: string
  role: UserRole
  is_active: boolean
  created_at: string
  updated_at: string
}

type AuthStatus = 'anonymous' | 'loading' | 'authenticated' | 'error'

type AuthContextValue = {
  user: UserPublic | null
  status: AuthStatus
  isLoading: boolean
  isAuthenticated: boolean
  sessionExpired: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => void
  retryAuth: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const [token, setToken] = useState<string | null>(getAccessToken)
  const [sessionExpired, setSessionExpired] = useState(false)

  const meQuery = useQuery({
    queryKey: ['auth', 'me'],
    queryFn: async () => (await api.get<UserPublic>('/auth/me')).data,
    enabled: Boolean(token),
    retry: false,
    staleTime: 60_000,
  })

  useEffect(() => {
    const handleExpired = () => {
      setToken(null)
      setSessionExpired(true)
      queryClient.clear()
    }
    window.addEventListener(authExpiredEvent, handleExpired)
    return () => window.removeEventListener(authExpiredEvent, handleExpired)
  }, [queryClient])

  const login = useCallback(async (email: string, password: string) => {
    const body = new URLSearchParams({ username: email, password })
    const response = await api.post<{ access_token: string; token_type: 'bearer' }>(
      '/auth/login',
      body,
      { headers: { 'Content-Type': 'application/x-www-form-urlencoded' } },
    )
    queryClient.clear()
    setAccessToken(response.data.access_token)
    try {
      const me = (await api.get<UserPublic>('/auth/me')).data
      queryClient.setQueryData(['auth', 'me'], me)
      setSessionExpired(false)
      setToken(response.data.access_token)
    } catch (error) {
      clearAccessToken()
      setToken(null)
      throw error
    }
  }, [queryClient])

  const logout = useCallback(() => {
    clearAccessToken()
    queryClient.clear()
    setToken(null)
    setSessionExpired(false)
  }, [queryClient])

  const status: AuthStatus = !token ? 'anonymous' : meQuery.isPending ? 'loading' : meQuery.isError ? 'error' : 'authenticated'
  const user = status === 'authenticated' ? (meQuery.data ?? null) : null

  const value = useMemo<AuthContextValue>(() => ({
    user,
    status,
    isLoading: status === 'loading',
    isAuthenticated: status === 'authenticated',
    sessionExpired,
    login,
    logout,
    retryAuth: () => { void meQuery.refetch() },
  }), [user, status, sessionExpired, login, logout, meQuery])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used within AuthProvider')
  return context
}

export function useCan(required: UserRole | readonly UserRole[]): boolean {
  const { user } = useAuth()
  if (!user) return false
  if (typeof required !== 'string') return required.includes(user.role)
  const roleOrder: Record<UserRole, number> = { viewer: 0, operator: 1, admin: 2 }
  return roleOrder[user.role] >= roleOrder[required]
}
