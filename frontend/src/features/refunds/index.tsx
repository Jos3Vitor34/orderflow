import { useState } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { useForm } from 'react-hook-form';
import { Link, useParams } from 'react-router';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { z } from 'zod';
import { api, getApiErrorMessage } from '../../api/client';
import { useAuth } from '../auth/AuthProvider';
import { useI18n } from '../../i18n';
import { Badge, Button, Card, ConfirmDialog, EmptyState, ErrorState, PageHeader, Pagination, Spinner } from '../../components/common';
import { dateTime, money } from '../orders/types';
import { clearPendingKey, pendingKey } from '../payments/idempotency';
import type { Payment } from '../payments/types';
import { normalizeRefundAmount } from './amount';
import { refundStatusLabel, type Refund, type RefundPage, type RefundReason, type RefundStatus } from './types';

const PAGE_SIZE = 20;
const refundSchema = z.object({
  amount: z.string().refine((value) => normalizeRefundAmount(value) !== null),
  reason: z.enum(['', 'duplicate', 'fraudulent', 'requested_by_customer']),
});
type RefundInput = z.infer<typeof refundSchema>;
interface RefundPayload { amount?: string; reason?: RefundReason }

const copy = (locale: string) => locale.startsWith('pt') ? {
  title: 'Reembolsos', description: 'Histórico de reembolsos deste pagamento.', request: 'Solicitar reembolso',
  id: 'Reembolso', amount: 'Valor', status: 'Status', reason: 'Motivo', created: 'Criado em',
  details: 'Detalhes', empty: 'Nenhum reembolso para este pagamento.',
  fullAmount: 'Deixe em branco para solicitar o saldo total disponível.',
  noReason: 'Sem motivo informado', duplicate: 'Duplicidade', fraudulent: 'Fraude', customer: 'Solicitado pelo cliente',
  invalidAmount: 'Informe entre R$ 0,01 e R$ 9.999.999.999,99, com até duas casas decimais.',
  confirmTitle: 'Confirmar reembolso', confirmBody: 'A solicitação será enviada à Stripe. Confirme antes de continuar.',
  confirm: 'Confirmar solicitação', success: 'Reembolso solicitado.',
  detailTitle: 'Detalhe do reembolso', payment: 'Pagamento', providerReference: 'Referência Stripe',
  failure: 'Falha', back: 'Voltar ao pagamento',
} : {
  title: 'Refunds', description: 'Refund history for this payment.', request: 'Request refund',
  id: 'Refund', amount: 'Amount', status: 'Status', reason: 'Reason', created: 'Created',
  details: 'Details', empty: 'No refunds for this payment.',
  fullAmount: 'Leave blank to request the full available balance.',
  noReason: 'No reason given', duplicate: 'Duplicate', fraudulent: 'Fraudulent', customer: 'Requested by customer',
  invalidAmount: 'Enter between BRL 0.01 and 9,999,999,999.99, with up to two decimal places.',
  confirmTitle: 'Confirm refund', confirmBody: 'The request will be sent to Stripe. Confirm to continue.',
  confirm: 'Confirm request', success: 'Refund requested.',
  detailTitle: 'Refund details', payment: 'Payment', providerReference: 'Stripe reference',
  failure: 'Failure', back: 'Back to payment',
};

function reasonLabel(reason: string | null, locale: string): string {
  const c = copy(locale);
  if (reason === 'duplicate') return c.duplicate;
  if (reason === 'fraudulent') return c.fraudulent;
  if (reason === 'requested_by_customer') return c.customer;
  return reason || c.noReason;
}

function RefundBadge({ status, locale }: { status: RefundStatus; locale: string }) {
  const tone = status === 'succeeded' ? 'success' : status === 'failed' || status === 'canceled' ? 'danger' : 'warning';
  return <Badge tone={tone}>{refundStatusLabel(status, locale)}</Badge>;
}

function RefundForm({ payment }: { payment: Payment }) {
  const { locale } = useI18n();
  const c = copy(locale);
  const queryClient = useQueryClient();
  const [pendingPayload, setPendingPayload] = useState<RefundPayload | null>(null);
  const [feedback, setFeedback] = useState('');
  const form = useForm<RefundInput>({ resolver: zodResolver(refundSchema), defaultValues: { amount: '', reason: '' } });
  const mutation = useMutation({
    mutationFn: async (payload: RefundPayload) => {
      const scope = `refund-${payment.id}`;
      const key = pendingKey(scope, payload);
      const refund = (await api.post<Refund>(`/payments/${payment.id}/refunds`, payload, { headers: { 'Idempotency-Key': key } })).data;
      clearPendingKey(scope);
      return refund;
    },
    onSuccess: async () => {
      setPendingPayload(null); setFeedback(c.success); form.reset();
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['refunds', 'payment', payment.id] }),
        queryClient.invalidateQueries({ queryKey: ['payments'] }),
        queryClient.invalidateQueries({ queryKey: ['statistics', 'overview'] }),
      ]);
    },
    onError: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['refunds', 'payment', payment.id] }),
        queryClient.invalidateQueries({ queryKey: ['payments', payment.id] }),
      ]);
    },
  });
  return <Card><h3>{c.request}</h3>
    <form onSubmit={form.handleSubmit((input) => {
      const payload: RefundPayload = {};
      const amount = normalizeRefundAmount(input.amount);
      if (amount) payload.amount = amount;
      if (input.reason) payload.reason = input.reason;
      setPendingPayload(payload);
    })} noValidate>
      <div className="form-grid">
        <div className="field"><label htmlFor="refund-amount">{c.amount}</label><input id="refund-amount" inputMode="decimal" placeholder={locale.startsWith('pt') ? '0,00' : '0.00'} {...form.register('amount')} aria-invalid={Boolean(form.formState.errors.amount)} />
          <span className="muted">{c.fullAmount}</span>
          {form.formState.errors.amount && <span className="error">{c.invalidAmount}</span>}
        </div>
        <label className="field">{c.reason}<select {...form.register('reason')}>
          <option value="">{c.noReason}</option><option value="duplicate">{c.duplicate}</option><option value="fraudulent">{c.fraudulent}</option><option value="requested_by_customer">{c.customer}</option>
        </select></label>
      </div>
      <div className="actions"><Button type="submit" disabled={mutation.isPending}>{c.request}</Button></div>
    </form>
    {mutation.isError && <p className="error" role="alert">{getApiErrorMessage(mutation.error, locale)}</p>}
    {feedback && <p className="notice" role="status">{feedback}</p>}
    <ConfirmDialog open={pendingPayload !== null} title={c.confirmTitle} description={`${c.confirmBody} ${c.amount}: ${pendingPayload?.amount ? money(pendingPayload.amount, locale) : c.fullAmount} · ${c.reason}: ${reasonLabel(pendingPayload?.reason ?? null, locale)}`} confirmLabel={c.confirm} busy={mutation.isPending} danger onConfirm={() => { if (pendingPayload) mutation.mutate(pendingPayload); }} onClose={() => { if (!mutation.isPending) setPendingPayload(null); }} />
  </Card>;
}

export function RefundsSection({ payment }: { payment: Payment }) {
  const { locale } = useI18n();
  const { user } = useAuth();
  const c = copy(locale);
  const [page, setPage] = useState(1);
  const query = useQuery({
    queryKey: ['refunds', 'payment', payment.id, page],
    queryFn: async () => (await api.get<RefundPage>(`/payments/${payment.id}/refunds`, { params: { page, page_size: PAGE_SIZE } })).data,
  });
  const canRefund = user?.role !== 'viewer' && payment.provider === 'stripe' && (payment.status === 'approved' || payment.status === 'partially_refunded');
  return <section aria-labelledby="refund-heading">
    <Card><h2 id="refund-heading">{c.title}</h2><p className="muted">{c.description}</p>
      {query.isPending ? <Spinner label={c.title} /> : query.isError ? <ErrorState message={getApiErrorMessage(query.error, locale)} onRetry={() => void query.refetch()} /> : query.data.items.length === 0 ? <EmptyState title={c.empty} /> : <>
        <div style={{ overflowX: 'auto' }}><table className="data-table">
          <thead><tr><th scope="col">{c.id}</th><th scope="col">{c.amount}</th><th scope="col">{c.status}</th><th scope="col">{c.reason}</th><th scope="col">{c.created}</th><th scope="col"><span className="sr-only">{c.details}</span></th></tr></thead>
          <tbody>{query.data.items.map((refund) => <tr key={refund.id}><td>#{refund.id}</td><td>{money(refund.amount, locale)}</td><td><RefundBadge status={refund.status} locale={locale} /></td><td>{reasonLabel(refund.reason, locale)}</td><td>{dateTime(refund.created_at, locale)}</td><td><Link to={`/refunds/${refund.id}`}>{c.details}</Link></td></tr>)}</tbody>
        </table></div><Pagination page={page} pages={query.data.pages} onPageChange={setPage} />
      </>}
    </Card>
    {canRefund && <RefundForm payment={payment} />}
  </section>;
}

export function RefundDetailPage() {
  const { refundId } = useParams();
  const id = Number(refundId);
  const { locale } = useI18n();
  const c = copy(locale);
  const query = useQuery({
    queryKey: ['refunds', id],
    queryFn: async () => (await api.get<Refund>(`/refunds/${id}`)).data,
    enabled: Number.isInteger(id) && id > 0,
  });
  if (!Number.isInteger(id) || id <= 0) return <main className="page"><ErrorState message={c.detailTitle} /></main>;
  return <main className="page">
    <PageHeader title={`${c.detailTitle} #${id}`} actions={query.data && <Link to={`/payments/${query.data.payment_id}`}>{c.back}</Link>} />
    {query.isPending ? <Spinner label={c.detailTitle} /> : query.isError ? <ErrorState message={getApiErrorMessage(query.error, locale)} onRetry={() => void query.refetch()} /> : <Card><dl className="form-grid">
      <div><dt>{c.status}</dt><dd><RefundBadge status={query.data.status} locale={locale} /></dd></div>
      <div><dt>{c.amount}</dt><dd>{money(query.data.amount, locale)}</dd></div>
      <div><dt>{c.payment}</dt><dd><Link to={`/payments/${query.data.payment_id}`}>#{query.data.payment_id}</Link></dd></div>
      <div><dt>{c.reason}</dt><dd>{reasonLabel(query.data.reason, locale)}</dd></div>
      <div><dt>{c.providerReference}</dt><dd>{query.data.provider_refund_id ?? '—'}</dd></div>
      <div><dt>{c.created}</dt><dd>{dateTime(query.data.created_at, locale)}</dd></div>
      {query.data.failure_reason && <div><dt>{c.failure}</dt><dd className="error">{query.data.failure_reason}</dd></div>}
    </dl></Card>}
  </main>;
}
