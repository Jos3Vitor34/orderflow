import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '../../api/client';
import { I18nProvider } from '../../i18n';
import type { Payment } from '../payments/types';
import { normalizeRefundAmount } from './amount';
import { RefundsSection } from './index';

vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ user: { role: 'operator' } }) }));

const payment: Payment = {
  id: 4, order_id: 3, provider: 'stripe', provider_reference: 'pi_4', amount: '50.00', status: 'approved',
  created_at: '2026-09-01T10:00:00Z', updated_at: '2026-09-01T10:00:00Z',
};

describe('refund request', () => {
  afterEach(() => { vi.restoreAllMocks(); sessionStorage.clear(); });

  it('accepts a Brazilian decimal and enforces the API monetary range', () => {
    expect(normalizeRefundAmount('10,50')).toBe('10.50');
    expect(normalizeRefundAmount('')).toBeUndefined();
    expect(normalizeRefundAmount('0,00')).toBeNull();
    expect(normalizeRefundAmount('10000000000,00')).toBeNull();
    expect(normalizeRefundAmount('1,999')).toBeNull();
  });

  it('confirms before sending and preserves the key on retry', async () => {
    vi.spyOn(api, 'get').mockResolvedValue({ data: { items: [], total: 0, page: 1, page_size: 20, pages: 0 } });
    const post = vi.spyOn(api, 'post')
      .mockRejectedValueOnce(new Error('temporary failure'))
      .mockResolvedValueOnce({ data: { id: 1, payment_id: 4 } });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const user = userEvent.setup();
    render(<QueryClientProvider client={client}><I18nProvider><MemoryRouter><RefundsSection payment={payment} /></MemoryRouter></I18nProvider></QueryClientProvider>);
    await screen.findByText('Nenhum reembolso para este pagamento.');
    await user.type(screen.getByLabelText('Valor'), '10,00');
    await user.selectOptions(screen.getByLabelText('Motivo'), 'duplicate');
    await user.click(screen.getByRole('button', { name: 'Solicitar reembolso' }));
    expect(post).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog', { name: 'Confirmar reembolso' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Confirmar solicitação' }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Confirmar solicitação' }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    const first = post.mock.calls[0]?.[2] as { headers: Record<string, string> };
    const second = post.mock.calls[1]?.[2] as { headers: Record<string, string> };
    expect(first.headers['Idempotency-Key']).toBeTruthy();
    expect(second.headers['Idempotency-Key']).toBe(first.headers['Idempotency-Key']);
    expect(post).toHaveBeenCalledWith('/payments/4/refunds', { amount: '10.00', reason: 'duplicate' }, expect.any(Object));
    expect(await screen.findByText('Reembolso solicitado.')).toHaveAttribute('role', 'status');
  });
});
