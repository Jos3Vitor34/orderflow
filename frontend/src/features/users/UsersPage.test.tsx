import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../api/client'
import { I18nProvider } from '../../i18n'
import { UserDetailPage, UsersPage } from './index'

const auth = vi.hoisted(() => ({ role: 'viewer' }))
vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ user: { role: auth.role } }) }))

const managedUser = {
  id: 2,
  full_name: 'Ana Admin',
  email: 'ana@example.com',
  role: 'admin',
  is_active: true,
  created_at: '2026-09-01T10:00:00Z',
  updated_at: '2026-09-01T10:00:00Z',
}

function renderPage(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><I18nProvider><MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/admin/users" element={<UsersPage />} />
    <Route path="/admin/users/:userId" element={<UserDetailPage />} />
  </Routes></MemoryRouter></I18nProvider></QueryClientProvider>)
}

describe('user administration', () => {
  beforeEach(() => { auth.role = 'viewer' })
  afterEach(() => { cleanup(); vi.restoreAllMocks() })

  it('blocks a viewer without calling the admin endpoint', () => {
    const get = vi.spyOn(api, 'get')
    renderPage('/admin/users')
    expect(screen.getByText('Apenas administradores podem acessar usuários.')).toBeInTheDocument()
    expect(get).not.toHaveBeenCalled()
  })

  it('shows the backend last-admin conflict when changing a role', async () => {
    auth.role = 'admin'
    vi.spyOn(api, 'get').mockResolvedValue({ data: managedUser })
    const patch = vi.spyOn(api, 'patch').mockRejectedValue({
      isAxiosError: true,
      response: { status: 409, data: { detail: 'At least one active administrator must remain' } },
    })
    const user = userEvent.setup()
    renderPage('/admin/users/2')
    expect(await screen.findByText('Ana Admin')).toBeInTheDocument()
    await user.selectOptions(screen.getByRole('combobox', { name: 'Papel' }), 'viewer')
    await user.click(screen.getByRole('button', { name: 'Salvar papel' }))
    await waitFor(() => expect(patch).toHaveBeenCalledWith('/users/2/role', { role: 'viewer' }))
    expect(await screen.findByText('É necessário manter ao menos um administrador ativo.')).toHaveAttribute('role', 'alert')
  })
})
