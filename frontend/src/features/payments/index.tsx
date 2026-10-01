import { useState } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { useForm } from 'react-hook-form';
import { Link, useNavigate, useParams } from 'react-router';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { z } from 'zod';
import { api, getApiErrorMessage } from '../../api/client';
import { useAuth } from '../auth/AuthProvider';
import { useI18n } from '../../i18n';
import { Badge, Button, Card, EmptyState, ErrorState, PageHeader, Pagination, Spinner } from '../../components/common';
import { dateTime, money, orderStatusLabel, type Order } from '../orders/types';
import { RefundsSection } from '../refunds';
import { clearPendingKey, pendingKey } from './idempotency';
import { paymentStatusLabel, paymentTransitions, type Payment, type PaymentPage, type PaymentStatus, type StripeReceipt } from './types';

const PAGE_SIZE = 20;
const createSchema = z.object({
  order_id: z.number().int().positive(),
  provider: z.string().trim().min(1).max(50).refine((value) => value.toLowerCase() !== 'stripe'),
  provider_reference: z.string().max(255),
});
type CreateInput = z.infer<typeof createSchema>;

const copy = (locale: string) => locale.startsWith('pt') ? {
  title: 'Pagamentos', description: 'Acompanhe pagamentos e operações Stripe.', new: 'Novo pagamento',
  id: 'Pagamento', order: 'Pedido', provider: 'Provedor', reference: 'Referência', amount: 'Valor',
  status: 'Status', created: 'Criado em', details: 'Detalhes', empty: 'Nenhum pagamento encontrado.',
  manual: 'Manual', stripe: 'Stripe Test Mode', orderId: 'ID do pedido', providerHint: 'Use um provedor diferente de Stripe para pagamentos manuais.',
  referenceHint: 'Opcional. Uma referência existente para o mesmo pedido e provedor retorna o pagamento anterior.',
  create: 'Criar pagamento', stripeCreate: 'Criar PaymentIntent', cancel: 'Cancelar',
  required: 'Informe um ID válido.', invalidProvider: 'Informe um provedor válido, diferente de Stripe.',
  orderMissing: 'Pedido não encontrado.', orderCancelled: 'Pedido cancelado.',
  stripeLimit: 'A API não fornece client_secret. Conclua o pagamento pelo fluxo externo configurado; o estado será atualizado pelo webhook.',
  createSuccess: 'Pagamento criado.', stripeSuccess: 'PaymentIntent criado.',
  changeStatus: 'Alterar status', update: 'Atualizar', updateSuccess: 'Status atualizado.', noTransition: 'Este pagamento não tem próximas etapas.',
  back: 'Voltar aos pagamentos', detailTitle: 'Detalhe do pagamento',
} : {
  title: 'Payments', description: 'Track payments and Stripe operations.', new: 'New payment',
  id: 'Payment', order: 'Order', provider: 'Provider', reference: 'Reference', amount: 'Amount',
  status: 'Status', created: 'Created', details: 'Details', empty: 'No payments found.',
  manual: 'Manual', stripe: 'Stripe Test Mode', orderId: 'Order ID', providerHint: 'Use a provider other than Stripe for manual payments.',
  referenceHint: 'Optional. An existing reference for the same order and provider returns the earlier payment.',
  create: 'Create payment', stripeCreate: 'Create PaymentIntent', cancel: 'Cancel',
  required: 'Enter a valid ID.', invalidProvider: 'Enter a valid provider other than Stripe.',
  orderMissing: 'Order not found.', orderCancelled: 'Cancelled order.',
  stripeLimit: 'The API does not provide a client_secret. Complete payment through the configured external flow; webhooks will update its status.',
  createSuccess: 'Payment created.', stripeSuccess: 'PaymentIntent created.',
  changeStatus: 'Change status', update: 'Update', updateSuccess: 'Status updated.', noTransition: 'This payment has no next step.',
  back: 'Back to payments', detailTitle: 'Payment details',
};

function PaymentBadge({ status, locale }: { status: PaymentStatus; locale: string }) {
  const tone = status === 'approved' ? 'success' : status === 'failed' ? 'danger' : status === 'pending' ? 'warning' : 'info';
  return <Badge tone={tone}>{paymentStatusLabel(status, locale)}</Badge>;
}

function OrderLookup({ id, locale }: { id: number; locale: string }) {
  const c = copy(locale);
  const query = useQuery({
    queryKey: ['orders', id],
    queryFn: async () => (await api.get<Order>(`/orders/${id}`)).data,
    enabled: Number.isInteger(id) && id > 0,
    retry: false,
  });
  if (!Number.isInteger(id) || id <= 0) return null;
  if (query.isPending) return <span className="muted">…</span>;
  if (query.isError) return <span className="error">{c.orderMissing}</span>;
  return <span className={query.data.status === 'cancelled' ? 'error' : 'muted'}>#{query.data.id} · {money(query.data.total_amount, locale)} · {orderStatusLabel(query.data.status, locale)}{query.data.status === 'cancelled' && ` · ${c.orderCancelled}`}</span>;
}

function CreatePayment({ onClose }: { onClose: () => void }) {
  const { locale } = useI18n();
  const c = copy(locale);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<'manual' | 'stripe'>('manual');
  const [feedback, setFeedback] = useState('');
  const form = useForm<CreateInput>({ resolver: zodResolver(createSchema), defaultValues: { order_id: 0, provider: 'manual', provider_reference: '' } });
  const mutation = useMutation({
    mutationFn: async (data: { mode: 'manual' | 'stripe'; input: CreateInput }): Promise<Payment> => {
      if (data.mode === 'stripe') {
        const payload = { order_id: data.input.order_id };
        const key = pendingKey('stripe-payment', payload);
        const receipt = (await api.post<StripeReceipt>('/payments/stripe', payload, { headers: { 'Idempotency-Key': key } })).data;
        clearPendingKey('stripe-payment');
        return receipt.payment;
      }
      const payload = { order_id: data.input.order_id, provider: data.input.provider.trim(), provider_reference: data.input.provider_reference.trim() || null };
      return (await api.post<Payment>('/payments', payload)).data;
    },
    onSuccess: async (payment, variables) => {
      setFeedback(variables.mode === 'stripe' ? c.stripeSuccess : c.createSuccess);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['payments'] }),
        queryClient.invalidateQueries({ queryKey: ['statistics', 'overview'] }),
      ]);
      navigate(`/payments/${payment.id}`);
    },
  });
  const orderId = form.watch('order_id');
  return <Card><h2>{c.new}</h2>
    <div className="actions" role="group" aria-label={c.new}>
      <Button type="button" variant={mode === 'manual' ? 'primary' : 'secondary'} onClick={() => setMode('manual')}>{c.manual}</Button>
      <Button type="button" variant={mode === 'stripe' ? 'primary' : 'secondary'} onClick={() => { form.setValue('provider', 'manual'); form.clearErrors('provider'); setMode('stripe'); }}>{c.stripe}</Button>
    </div>
    <form onSubmit={form.handleSubmit((input) => mutation.mutate({ mode, input }))} noValidate>
      <div className="form-grid"><div className="field"><label htmlFor="payment-order-id">{c.orderId}</label>
        <input id="payment-order-id" type="number" min="1" step="1" {...form.register('order_id', { valueAsNumber: true })} aria-invalid={Boolean(form.formState.errors.order_id)} />
        {form.formState.errors.order_id && <span className="error">{c.required}</span>}
        <OrderLookup id={orderId} locale={locale} />
      </div>
      {mode === 'manual' && <>
        <div className="field"><label htmlFor="payment-provider">{c.provider}</label><input id="payment-provider" {...form.register('provider')} aria-invalid={Boolean(form.formState.errors.provider)} />
          {form.formState.errors.provider && <span className="error">{c.invalidProvider}</span>}
          <span className="muted">{c.providerHint}</span>
        </div>
        <div className="field"><label htmlFor="payment-reference">{c.reference}</label><input id="payment-reference" {...form.register('provider_reference')} aria-invalid={Boolean(form.formState.errors.provider_reference)} /><span className="muted">{c.referenceHint}</span></div>
      </>}
      </div>
      {mode === 'stripe' && <p className="notice">{c.stripeLimit}</p>}
      <div className="actions"><Button type="submit" loading={mutation.isPending}>{mode === 'stripe' ? c.stripeCreate : c.create}</Button><Button type="button" variant="ghost" onClick={onClose}>{c.cancel}</Button></div>
      {mutation.isError && <p className="error" role="alert">{getApiErrorMessage(mutation.error, locale)}</p>}
      {feedback && <p className="notice" role="status">{feedback}</p>}
    </form>
  </Card>;
}

export function PaymentsPage() {
  const { locale } = useI18n();
  const { user } = useAuth();
  const c = copy(locale);
  const [page, setPage] = useState(1);
  const [showCreate, setShowCreate] = useState(false);
  const query = useQuery({
    queryKey: ['payments', 'list', page],
    queryFn: async () => (await api.get<PaymentPage>('/payments', { params: { page, page_size: PAGE_SIZE } })).data,
  });
  return <main className="page">
    <PageHeader title={c.title} description={c.description} actions={user?.role !== 'viewer' && <Button onClick={() => setShowCreate((current) => !current)}>{c.new}</Button>} />
    {showCreate && user?.role !== 'viewer' && <CreatePayment onClose={() => setShowCreate(false)} />}
    <Card>{query.isPending ? <Spinner label={c.title} /> : query.isError ? <ErrorState message={getApiErrorMessage(query.error, locale)} onRetry={() => void query.refetch()} /> : query.data.items.length === 0 ? <EmptyState title={c.empty} /> : <>
      <div style={{ overflowX: 'auto' }}><table className="data-table">
        <thead><tr><th scope="col">{c.id}</th><th scope="col">{c.order}</th><th scope="col">{c.provider}</th><th scope="col">{c.status}</th><th scope="col">{c.amount}</th><th scope="col">{c.created}</th><th scope="col"><span className="sr-only">{c.details}</span></th></tr></thead>
        <tbody>{query.data.items.map((payment) => <tr key={payment.id}><td>#{payment.id}</td><td><Link to={`/orders/${payment.order_id}`}>#{payment.order_id}</Link></td><td>{payment.provider}</td><td><PaymentBadge status={payment.status} locale={locale} /></td><td>{money(payment.amount, locale)}</td><td>{dateTime(payment.created_at, locale)}</td><td><Link to={`/payments/${payment.id}`}>{c.details}</Link></td></tr>)}</tbody>
      </table></div><Pagination page={page} pages={query.data.pages} onPageChange={setPage} />
    </>}</Card>
  </main>;
}

export function PaymentDetailPage() {
  const { paymentId } = useParams();
  const id = Number(paymentId);
  const { locale } = useI18n();
  const { user } = useAuth();
  const c = copy(locale);
  const queryClient = useQueryClient();
  const [nextStatus, setNextStatus] = useState<PaymentStatus | ''>('');
  const [feedback, setFeedback] = useState('');
  const query = useQuery({
    queryKey: ['payments', id],
    queryFn: async () => (await api.get<Payment>(`/payments/${id}`)).data,
    enabled: Number.isInteger(id) && id > 0,
  });
  const payment = query.data;
  const orderQuery = useQuery({
    queryKey: ['orders', payment?.order_id],
    queryFn: async () => (await api.get<Order>(`/orders/${payment?.order_id}`)).data,
    enabled: Boolean(payment), retry: false,
  });
  const mutation = useMutation({
    mutationFn: async (status: PaymentStatus) => (await api.patch<Payment>(`/payments/${id}`, { status })).data,
    onSuccess: async () => {
      setFeedback(c.updateSuccess); setNextStatus('');
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['payments'] }),
        queryClient.invalidateQueries({ queryKey: ['statistics', 'overview'] }),
      ]);
    },
    onError: async () => { await queryClient.invalidateQueries({ queryKey: ['payments', id] }); },
  });
  if (!Number.isInteger(id) || id <= 0) return <main className="page"><ErrorState message={c.detailTitle} /></main>;
  return <main className="page">
    <PageHeader title={`${c.detailTitle} #${id}`} actions={<Link to="/payments">{c.back}</Link>} />
    {query.isPending ? <Spinner label={c.detailTitle} /> : query.isError ? <ErrorState message={getApiErrorMessage(query.error, locale)} onRetry={() => void query.refetch()} /> : payment && <>
      <Card><dl className="form-grid">
        <div><dt>{c.status}</dt><dd><PaymentBadge status={payment.status} locale={locale} /></dd></div>
        <div><dt>{c.amount}</dt><dd>{money(payment.amount, locale)}</dd></div>
        <div><dt>{c.order}</dt><dd><Link to={`/orders/${payment.order_id}`}>#{payment.order_id}</Link>{orderQuery.data && <span className="muted"> · {orderStatusLabel(orderQuery.data.status, locale)}</span>}</dd></div>
        <div><dt>{c.provider}</dt><dd>{payment.provider}</dd></div>
        <div><dt>{c.reference}</dt><dd>{payment.provider_reference || '—'}</dd></div>
        <div><dt>{c.created}</dt><dd>{dateTime(payment.created_at, locale)}</dd></div>
      </dl></Card>
      {payment.provider === 'stripe' && <p className="notice">{c.stripeLimit}</p>}
      {user?.role !== 'viewer' && <Card><h2>{c.changeStatus}</h2>
        {paymentTransitions[payment.status].length === 0 ? <p className="muted">{c.noTransition}</p> : <div className="actions"><label className="field">{c.status}<select value={nextStatus} onChange={(event) => setNextStatus(event.target.value as PaymentStatus)}><option value="">—</option>{paymentTransitions[payment.status].map((status) => <option key={status} value={status}>{paymentStatusLabel(status, locale)}</option>)}</select></label><Button onClick={() => { if (nextStatus) mutation.mutate(nextStatus); }} disabled={!nextStatus} loading={mutation.isPending}>{c.update}</Button></div>}
        {mutation.isError && <p className="error" role="alert">{getApiErrorMessage(mutation.error, locale)}</p>}
        {feedback && <p className="notice" role="status">{feedback}</p>}
      </Card>}
      <RefundsSection payment={payment} />
    </>}
  </main>;
}
