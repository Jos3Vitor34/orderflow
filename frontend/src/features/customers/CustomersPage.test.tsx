import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '../../api/client'
import { I18nProvider } from '../../i18n'
import { CustomerDetailPage, CustomersPage } from './CustomersPage'

const role = vi.hoisted(() => ({ current: 'viewer' }))
vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ user: { role: role.current } }) }))

const firstCustomer = {
  id: 1, name: 'Ana Silva', email: 'ana@example.com', phone: null,
  created_at: '2026-09-01T10:00:00Z', updated_at: '2026-09-01T10:00:00Z',
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nProvider><MemoryRouter><CustomersPage /></MemoryRouter></I18nProvider>
    </QueryClientProvider>,
  )
}

function renderDetailPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nProvider><MemoryRouter initialEntries={['/customers/1']}><Routes><Route path="/customers/:customerId" element={<CustomerDetailPage />} /></Routes></MemoryRouter></I18nProvider>
    </QueryClientProvider>,
  )
}

describe('CustomersPage', () => {
  beforeEach(() => {
    role.current = 'viewer'
    vi.spyOn(api, 'get').mockImplementation(async (_url, config) => {
      const page = Number((config?.params as { page?: number } | undefined)?.page ?? 1)
      return { data: { items: page === 1 ? [firstCustomer] : [{ ...firstCustomer, id: 2, name: 'Bruno Lima' }], total: 2, page, page_size: 1, pages: 2 } } as never
    })
  })

  afterEach(() => { cleanup(); vi.restoreAllMocks() })

  it('paginates the API list and hides creation from viewers', async () => {
    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByRole('link', { name: 'Ana Silva' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Novo cliente' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /próxima/i }))
    expect(await screen.findByRole('link', { name: 'Bruno Lima' })).toBeInTheDocument()
    expect(api.get).toHaveBeenCalledWith('/customers', { params: { page: 2, page_size: 20 } })
  })

  it('validates and creates a customer with the API payload', async () => {
    role.current = 'operator'
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: firstCustomer })
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('link', { name: 'Ana Silva' })
    await user.click(screen.getByRole('button', { name: 'Novo cliente' }))
    await user.type(screen.getByLabelText('Nome'), 'Nova Cliente')
    await user.type(screen.getByLabelText('E-mail'), 'invalido')
    await user.click(screen.getByRole('button', { name: 'Criar cliente' }))
    expect(await screen.findByText('Informe um e-mail válido.')).toBeInTheDocument()
    expect(post).not.toHaveBeenCalled()
    await user.clear(screen.getByLabelText('E-mail'))
    await user.type(screen.getByLabelText('E-mail'), 'nova@example.com')
    await user.click(screen.getByRole('button', { name: 'Criar cliente' }))
    await waitFor(() => expect(post).toHaveBeenCalledWith('/customers', {
      name: 'Nova Cliente', email: 'nova@example.com', phone: null,
    }))
    expect(await screen.findByRole('status')).toHaveTextContent('Cliente criado com sucesso.')
  })

  it('shows a related-record conflict after a failed admin deletion', async () => {
    role.current = 'admin'
    vi.mocked(api.get).mockResolvedValue({ data: firstCustomer } as never)
    const remove = vi.spyOn(api, 'delete').mockRejectedValue({ response: { status: 409 } })
    const user = userEvent.setup()
    renderDetailPage()
    expect(await screen.findByRole('heading', { name: 'Ana Silva' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Excluir cliente' }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Excluir cliente' }))
    await waitFor(() => expect(remove).toHaveBeenCalledWith('/customers/1'))
    expect(await screen.findByRole('alert')).toHaveTextContent('Este cliente tem registros vinculados')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
