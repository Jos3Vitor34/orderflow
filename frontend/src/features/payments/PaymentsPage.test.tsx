import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '../../api/client';
import { I18nProvider } from '../../i18n';
import { PaymentsPage } from './index';

vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ user: { role: 'operator' } }) }));

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  return render(<QueryClientProvider client={client}><I18nProvider><MemoryRouter initialEntries={['/payments']}><Routes>
    <Route path="/payments" element={<PaymentsPage />} />
    <Route path="/payments/:paymentId" element={<div>Payment created</div>} />
  </Routes></MemoryRouter></I18nProvider></QueryClientProvider>);
}

describe('Stripe payment creation', () => {
  afterEach(() => { vi.restoreAllMocks(); sessionStorage.clear(); });

  it('reuses its idempotency key when retrying the same order', async () => {
    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/payments') return { data: { items: [], total: 0, page: 1, page_size: 20, pages: 0 } } as never;
      if (url === '/orders/5') return { data: { id: 5, status: 'pending', total_amount: '50.00' } } as never;
      throw new Error(`Unexpected URL: ${url}`);
    });
    const post = vi.spyOn(api, 'post')
      .mockRejectedValueOnce(new Error('temporary failure'))
      .mockResolvedValueOnce({ data: { payment: { id: 9 }, stripe: { payment_intent_id: 'pi_9', status: 'requires_payment_method', amount: 5000, currency: 'brl' } } });
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Nenhum pagamento encontrado.');
    await user.click(screen.getByRole('button', { name: 'Novo pagamento' }));
    await user.click(screen.getByRole('button', { name: 'Stripe Test Mode' }));
    await user.type(screen.getByLabelText('ID do pedido'), '5');
    await user.click(screen.getByRole('button', { name: 'Criar PaymentIntent' }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Criar PaymentIntent' }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    const first = post.mock.calls[0]?.[2] as { headers: Record<string, string> };
    const second = post.mock.calls[1]?.[2] as { headers: Record<string, string> };
    expect(first.headers['Idempotency-Key']).toBeTruthy();
    expect(second.headers['Idempotency-Key']).toBe(first.headers['Idempotency-Key']);
    expect(post).toHaveBeenCalledWith('/payments/stripe', { order_id: 5 }, expect.any(Object));
    expect(await screen.findByText('Payment created')).toBeInTheDocument();
  });
});
