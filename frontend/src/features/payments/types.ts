import type { Page } from '../orders/types';

export type PaymentStatus = 'pending' | 'approved' | 'failed' | 'partially_refunded' | 'refunded';

export interface Payment {
  id: number;
  order_id: number;
  provider: string;
  provider_reference: string | null;
  amount: string;
  status: PaymentStatus;
  created_at: string;
  updated_at: string;
}

export type PaymentPage = Page<Payment>;

export interface StripeReceipt {
  payment: Payment;
  stripe: {
    payment_intent_id: string;
    status: string;
    amount: number;
    currency: string;
  };
}

export const paymentTransitions: Record<PaymentStatus, PaymentStatus[]> = {
  pending: ['approved', 'failed'],
  approved: ['partially_refunded', 'refunded'],
  failed: [],
  partially_refunded: ['refunded'],
  refunded: [],
};

const labels: Record<PaymentStatus, [string, string]> = {
  pending: ['Pendente', 'Pending'],
  approved: ['Aprovado', 'Approved'],
  failed: ['Falhou', 'Failed'],
  partially_refunded: ['Reembolsado parcialmente', 'Partially refunded'],
  refunded: ['Reembolsado', 'Refunded'],
};

export function paymentStatusLabel(status: PaymentStatus, locale: string): string {
  return labels[status]?.[locale.startsWith('pt') ? 0 : 1] ?? status;
}
