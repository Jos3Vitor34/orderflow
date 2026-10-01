import type { Page } from '../orders/types';

export type RefundStatus = 'pending' | 'requires_action' | 'succeeded' | 'failed' | 'canceled';
export type RefundReason = 'duplicate' | 'fraudulent' | 'requested_by_customer';

export interface Refund {
  id: number;
  payment_id: number;
  provider: string;
  provider_refund_id: string | null;
  amount: string;
  currency: string;
  status: RefundStatus;
  reason: string | null;
  failure_reason: string | null;
  created_at: string;
  updated_at: string;
}

export type RefundPage = Page<Refund>;

const labels: Record<RefundStatus, [string, string]> = {
  pending: ['Pendente', 'Pending'],
  requires_action: ['Ação necessária', 'Action required'],
  succeeded: ['Concluído', 'Succeeded'],
  failed: ['Falhou', 'Failed'],
  canceled: ['Cancelado', 'Canceled'],
};

export function refundStatusLabel(status: RefundStatus, locale: string): string {
  return labels[status]?.[locale.startsWith('pt') ? 0 : 1] ?? status;
}
