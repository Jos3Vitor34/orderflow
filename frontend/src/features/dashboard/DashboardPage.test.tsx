import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { api } from '../../api/client'
import { I18nProvider } from '../../i18n'
import { DashboardPage } from './DashboardPage'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

it('renders aggregate metrics from the overview API', async () => {
  vi.spyOn(api, 'get').mockResolvedValue({ data: {
    orders: { total: 4, by_status: { pending: 1, processing: 1, confirmed: 1, shipped: 0, delivered: 1, cancelled: 0 }, total_amount: '380.00', average_ticket: '95.00', created_in_period: 4 },
    payments: { total: 3, by_status: { pending: 1, approved: 2, failed: 0, partially_refunded: 0, refunded: 0 }, total_processed_amount: '280.00', successful_amount: '200.00', successful_count: 2, success_rate: '100.00' },
    operational: { pending_orders: 1, processing_orders: 1, confirmed_orders: 1, cancelled_orders: 0 },
    generated_at: '2026-09-30T12:00:00Z',
  } })
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={queryClient}><I18nProvider><DashboardPage /></I18nProvider></QueryClientProvider>)
  expect(await screen.findByText('Valor dos pedidos')).toBeInTheDocument()
  expect(screen.getByText(/R\$\s*380,00/)).toBeInTheDocument()
  expect(screen.getByText('100%')).toBeInTheDocument()
  expect(api.get).toHaveBeenCalledWith('/statistics/overview')
})
