import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../../api/client';
import { I18nProvider } from '../../i18n';
import { OrderDetailPage, OrdersPage } from './index';
import type { Order } from './types';

const role = vi.hoisted(() => ({ current: 'viewer' }));
vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ user: { role: role.current } }) }));

const order: Order = {
  id: 1, customer_id: 3, status: 'pending', total_amount: '50.00',
  items: [{ id: 10, product_id: 7, quantity: 2, unit_price: '25.00' }],
  created_at: '2026-09-01T10:00:00Z', updated_at: '2026-09-01T10:00:00Z',
};

function renderPage(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><I18nProvider><MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/orders" element={<OrdersPage />} />
    <Route path="/orders/:orderId" element={<OrderDetailPage />} />
  </Routes></MemoryRouter></I18nProvider></QueryClientProvider>);
}

describe('order workflow', () => {
  beforeEach(() => {
    role.current = 'viewer';
    vi.spyOn(api, 'get').mockImplementation(async (url, config) => {
      if (url === '/orders') {
        const page = Number((config?.params as { page?: number } | undefined)?.page ?? 1);
        return { data: { items: [{ ...order, id: page }], page, page_size: 20, pages: 2, total: 2 } } as never;
      }
      if (url === '/orders/1') return { data: order } as never;
      if (url === '/customers/3') return { data: { id: 3, name: 'Ana', email: 'ana@example.com' } } as never;
      if (url === '/products/7') return { data: { id: 7, name: 'Caderno', sku: 'CAD', price: '25.00', stock: 8, is_active: true } } as never;
      throw new Error(`Unexpected URL: ${url}`);
    });
  });

  afterEach(() => vi.restoreAllMocks());

  it('paginates and hides creation from viewers', async () => {
    const user = userEvent.setup();
    renderPage('/orders');
    expect(await screen.findByRole('link', { name: 'Detalhes' })).toHaveAttribute('href', '/orders/1');
    expect(screen.queryByRole('button', { name: 'Novo pedido' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /próxima/i }));
    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/orders', { params: { page: 2, page_size: 20 } }));
    expect(await screen.findByRole('link', { name: 'Detalhes' })).toHaveAttribute('href', '/orders/2');
  });

  it('rejects duplicate products before posting', async () => {
    role.current = 'operator';
    const post = vi.spyOn(api, 'post');
    const user = userEvent.setup();
    renderPage('/orders');
    await screen.findByRole('link', { name: 'Detalhes' });
    await user.click(screen.getByRole('button', { name: 'Novo pedido' }));
    await user.type(screen.getByLabelText('ID do cliente'), '3');
    await user.type(screen.getByLabelText('ID do produto'), '7');
    await user.click(screen.getByRole('button', { name: 'Adicionar item' }));
    await user.type(screen.getAllByLabelText('ID do produto')[1]!, '7');
    await user.click(screen.getByRole('button', { name: 'Criar pedido' }));
    expect(await screen.findByText('Cada produto pode aparecer uma única vez no pedido.')).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });

  it('offers only valid next statuses and updates the order', async () => {
    role.current = 'operator';
    const patch = vi.spyOn(api, 'patch').mockResolvedValue({ data: { ...order, status: 'processing' } });
    const user = userEvent.setup();
    renderPage('/orders/1');
    const status = await screen.findByRole('combobox', { name: 'Status' });
    expect(status).toHaveTextContent('Em processamento');
    expect(status).toHaveTextContent('Cancelado');
    expect(status).not.toHaveTextContent('Confirmado');
    await user.selectOptions(status, 'processing');
    await user.click(screen.getByRole('button', { name: 'Atualizar' }));
    await waitFor(() => expect(patch).toHaveBeenCalledWith('/orders/1', { status: 'processing' }));
  });
});
