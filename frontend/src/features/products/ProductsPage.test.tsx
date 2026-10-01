import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '../../api/client'
import { I18nProvider } from '../../i18n'
import { ProductsPage } from './ProductsPage'

const role = vi.hoisted(() => ({ current: 'operator' }))
vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ user: { role: role.current } }) }))

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={queryClient}><I18nProvider><MemoryRouter><ProductsPage /></MemoryRouter></I18nProvider></QueryClientProvider>)
}

describe('ProductsPage', () => {
  beforeEach(() => {
    role.current = 'operator'
    vi.spyOn(api, 'get').mockResolvedValue({ data: { items: [], total: 0, page: 1, page_size: 20, pages: 0 } })
  })

  afterEach(() => { cleanup(); vi.restoreAllMocks() })

  it('blocks invalid price precision and stock before sending', async () => {
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: {} })
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('Nenhum produto cadastrado')
    await user.click(screen.getByRole('button', { name: 'Novo produto' }))
    await user.type(screen.getByLabelText('SKU'), 'SKU-1')
    await user.type(screen.getByLabelText('Nome'), 'Caderno')
    await user.type(screen.getByLabelText('Preço'), '12,501')
    await user.clear(screen.getByLabelText('Estoque'))
    await user.type(screen.getByLabelText('Estoque'), '2147483648')
    await user.click(screen.getByRole('button', { name: 'Criar produto' }))
    expect(await screen.findByText(/com até duas casas decimais/)).toBeInTheDocument()
    expect(screen.getByText(/número inteiro entre 0 e 2.147.483.647/)).toBeInTheDocument()
    expect(post).not.toHaveBeenCalled()
  })

  it('creates a product with decimal normalization and active state', async () => {
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: {} })
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('Nenhum produto cadastrado')
    await user.click(screen.getByRole('button', { name: 'Novo produto' }))
    await user.type(screen.getByLabelText('SKU'), 'SKU-1')
    await user.type(screen.getByLabelText('Nome'), 'Caderno')
    await user.type(screen.getByLabelText('Preço'), '15,90')
    await user.clear(screen.getByLabelText('Estoque'))
    await user.type(screen.getByLabelText('Estoque'), '12')
    await user.click(screen.getByRole('button', { name: 'Criar produto' }))
    await waitFor(() => expect(post).toHaveBeenCalledWith('/products', {
      sku: 'SKU-1', name: 'Caderno', description: null, price: '15.90', stock: 12, is_active: true,
    }))
  })
})
