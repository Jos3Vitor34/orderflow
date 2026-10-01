import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AxiosHeaders, type AxiosResponse } from 'axios'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router'
import { api, authExpiredEvent, clearAccessToken, setAccessToken } from '../../api/client'
import { I18nProvider } from '../../i18n'
import { AuthProvider, type UserPublic } from './AuthProvider'
import { LoginPage } from './LoginPage'
import { ProtectedRoute, RoleProtectedRoute } from './ProtectedRoute'

const viewer: UserPublic = {
  id: 7,
  full_name: 'Ana Viewer',
  email: 'ana@example.com',
  role: 'viewer',
  is_active: true,
  created_at: '2026-09-01T10:00:00Z',
  updated_at: '2026-09-01T10:00:00Z',
}

function response<T>(data: T): AxiosResponse<T> {
  return { data, status: 200, statusText: 'OK', headers: {}, config: { headers: new AxiosHeaders() } }
}

function renderAuth(initialPath = '/login', includeAdmin = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>
    <I18nProvider><AuthProvider><MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<ProtectedRoute />}>
          <Route path="/dashboard" element={<div>Protected dashboard</div>} />
          {includeAdmin && <Route element={<RoleProtectedRoute roles={['admin']} />}>
            <Route path="/admin/users" element={<div>Admin users</div>} />
          </Route>}
        </Route>
      </Routes>
    </MemoryRouter></AuthProvider></I18nProvider>
  </QueryClientProvider>)
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  window.sessionStorage.clear()
  window.localStorage.clear()
})

describe('authentication flow', () => {
  it('validates required login fields before calling the API', async () => {
    const post = vi.spyOn(api, 'post')
    renderAuth()
    await userEvent.click(screen.getByRole('button', { name: 'Entrar' }))
    expect(await screen.findByText('Informe um e-mail válido.')).toBeInTheDocument()
    expect(screen.getByText('Informe sua senha.')).toBeInTheDocument()
    expect(post).not.toHaveBeenCalled()
  })

  it('sends OAuth2 form fields, loads the user, and keeps the token in session storage', async () => {
    const post = vi.spyOn(api, 'post').mockResolvedValue(response({ access_token: 'test-token', token_type: 'bearer' }))
    vi.spyOn(api, 'get').mockResolvedValue(response(viewer))
    renderAuth()
    await userEvent.type(screen.getByLabelText('E-mail'), viewer.email)
    await userEvent.type(screen.getByLabelText('Senha'), 'password123')
    await userEvent.click(screen.getByRole('button', { name: 'Entrar' }))
    expect(await screen.findByText('Protected dashboard')).toBeInTheDocument()
    expect(post).toHaveBeenCalledWith('/auth/login', expect.any(URLSearchParams), expect.objectContaining({ headers: expect.objectContaining({ 'Content-Type': 'application/x-www-form-urlencoded' }) }))
    const body = post.mock.calls[0]?.[1]
    expect(body).toBeInstanceOf(URLSearchParams)
    expect((body as URLSearchParams).get('username')).toBe(viewer.email)
    expect(window.sessionStorage.getItem('orderflow.access_token')).toBe('test-token')
  })

  it('blocks a viewer from the admin route', async () => {
    setAccessToken('existing-token')
    vi.spyOn(api, 'get').mockResolvedValue(response(viewer))
    renderAuth('/admin/users', true)
    expect(await screen.findByText('Acesso não autorizado')).toBeInTheDocument()
    expect(screen.queryByText('Admin users')).not.toBeInTheDocument()
  })

  it('returns to login when the session expires', async () => {
    setAccessToken('existing-token')
    vi.spyOn(api, 'get').mockResolvedValue(response(viewer))
    renderAuth('/dashboard')
    expect(await screen.findByText('Protected dashboard')).toBeInTheDocument()
    clearAccessToken()
    window.dispatchEvent(new Event(authExpiredEvent))
    await waitFor(() => expect(screen.getByText('Sua sessão expirou. Entre novamente.')).toBeInTheDocument())
    expect(window.sessionStorage.getItem('orderflow.access_token')).toBeNull()
  })
})
