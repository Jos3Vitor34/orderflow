import { useState } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { useFieldArray, useForm } from 'react-hook-form';
import { Link, useNavigate, useParams } from 'react-router';
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { z } from 'zod';
import { api, getApiErrorMessage } from '../../api/client';
import { useAuth } from '../auth/AuthProvider';
import { useI18n } from '../../i18n';
import { Badge, Button, Card, EmptyState, ErrorState, PageHeader, Pagination, Spinner } from '../../components/common';
import { dateTime, money, orderStatusLabel, orderTransitions, type CustomerSummary, type Order, type OrderStatus, type Page, type ProductSummary } from './types';

const PAGE_SIZE = 20;
const itemSchema = z.object({ product_id: z.number().int().positive(), quantity: z.number().int().positive() });
const orderSchema = z.object({ customer_id: z.number().int().positive(), items: z.array(itemSchema).min(1) }).refine(
  (data) => new Set(data.items.map((item) => item.product_id)).size === data.items.length,
  { path: ['items'], message: 'Each product may appear only once.' },
);
type OrderInput = z.infer<typeof orderSchema>;

const copy = (locale: string) => locale.startsWith('pt') ? {
  title: 'Pedidos', description: 'Acompanhe pedidos e altere seus estados.', new: 'Novo pedido',
  id: 'Pedido', customer: 'Cliente', status: 'Status', total: 'Total', created: 'Criado em', details: 'Detalhes',
  empty: 'Nenhum pedido encontrado.', retry: 'Tentar novamente', cancel: 'Cancelar', save: 'Criar pedido',
  product: 'Produto', quantity: 'Quantidade', addItem: 'Adicionar item', remove: 'Remover',
  customerId: 'ID do cliente', productId: 'ID do produto', required: 'Informe um ID válido e uma quantidade positiva.',
  duplicate: 'Cada produto pode aparecer uma única vez no pedido.', customerMissing: 'Cliente não encontrado.',
  productMissing: 'Produto não encontrado.', inactive: 'Produto inativo.', stock: 'Estoque',
  createSuccess: 'Pedido criado.', detailTitle: 'Detalhe do pedido', items: 'Itens',
  changeStatus: 'Alterar status', update: 'Atualizar', updateSuccess: 'Status atualizado.',
  noTransition: 'Este pedido não tem próximas etapas.', back: 'Voltar aos pedidos',
  payments: 'Pagamentos', viewPayments: 'Abrir lista de pagamentos', paymentsHint: 'A lista reúne todos os pagamentos em páginas. Confira a coluna Pedido e avance até encontrar o pedido',
  relatedWarning: 'Não foi possível carregar todos os dados relacionados.',
} : {
  title: 'Orders', description: 'Track orders and update their status.', new: 'New order',
  id: 'Order', customer: 'Customer', status: 'Status', total: 'Total', created: 'Created', details: 'Details',
  empty: 'No orders found.', retry: 'Try again', cancel: 'Cancel', save: 'Create order',
  product: 'Product', quantity: 'Quantity', addItem: 'Add item', remove: 'Remove',
  customerId: 'Customer ID', productId: 'Product ID', required: 'Enter a valid ID and positive quantity.',
  duplicate: 'Each product may appear only once in an order.', customerMissing: 'Customer not found.',
  productMissing: 'Product not found.', inactive: 'Inactive product.', stock: 'Stock',
  createSuccess: 'Order created.', detailTitle: 'Order details', items: 'Items',
  changeStatus: 'Change status', update: 'Update', updateSuccess: 'Status updated.',
  noTransition: 'This order has no next step.', back: 'Back to orders',
  payments: 'Payments', viewPayments: 'Open payment list', paymentsHint: 'The list shows all payments in pages. Check the Order column and move through pages to find order',
  relatedWarning: 'Some related data could not be loaded.',
};

function OrderBadge({ status, locale }: { status: OrderStatus; locale: string }) {
  const tone = status === 'delivered' || status === 'confirmed' ? 'success' : status === 'cancelled' ? 'danger' : status === 'pending' ? 'warning' : 'info';
  return <Badge tone={tone}>{orderStatusLabel(status, locale)}</Badge>;
}

function CustomerLookup({ id, locale }: { id: number; locale: string }) {
  const c = copy(locale);
  const query = useQuery({
    queryKey: ['customers', id],
    queryFn: async () => (await api.get<CustomerSummary>(`/customers/${id}`)).data,
    enabled: Number.isInteger(id) && id > 0,
    retry: false,
  });
  if (!Number.isInteger(id) || id <= 0) return null;
  if (query.isPending) return <span className="muted">…</span>;
  if (query.isError) return <span className="error">{c.customerMissing}</span>;
  return <span className="muted">{query.data.name} · {query.data.email}</span>;
}

function ProductLookup({ id, locale }: { id: number; locale: string }) {
  const c = copy(locale);
  const query = useQuery({
    queryKey: ['products', id],
    queryFn: async () => (await api.get<ProductSummary>(`/products/${id}`)).data,
    enabled: Number.isInteger(id) && id > 0,
    retry: false,
  });
  if (!Number.isInteger(id) || id <= 0) return null;
  if (query.isPending) return <span className="muted">…</span>;
  if (query.isError) return <span className="error">{c.productMissing}</span>;
  return <span className={query.data.is_active ? 'muted' : 'error'}>{query.data.name} · {money(query.data.price, locale)} · {c.stock}: {query.data.stock}{!query.data.is_active && ` · ${c.inactive}`}</span>;
}

function CreateOrder({ onClose }: { onClose: () => void }) {
  const { locale } = useI18n();
  const c = copy(locale);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [success, setSuccess] = useState('');
  const form = useForm<OrderInput>({ resolver: zodResolver(orderSchema), defaultValues: { customer_id: 0, items: [{ product_id: 0, quantity: 1 }] } });
  const { fields, append, remove } = useFieldArray({ control: form.control, name: 'items' });
  const mutation = useMutation({
    mutationFn: async (value: OrderInput) => (await api.post<Order>('/orders', value)).data,
    onSuccess: async (order) => {
      setSuccess(c.createSuccess);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['orders'] }),
        queryClient.invalidateQueries({ queryKey: ['statistics', 'overview'] }),
      ]);
      navigate(`/orders/${order.id}`);
    },
  });
  const customerId = form.watch('customer_id');
  const itemValues = form.watch('items');
  return <Card>
    <h2>{c.new}</h2>
    <form onSubmit={form.handleSubmit((value) => mutation.mutate(value))} noValidate>
      <div className="form-grid">
        <div className="field"><label htmlFor="order-customer-id">{c.customerId}</label>
          <input id="order-customer-id" type="number" min="1" step="1" {...form.register('customer_id', { valueAsNumber: true })} aria-invalid={Boolean(form.formState.errors.customer_id)} />
          {form.formState.errors.customer_id && <span className="error">{c.required}</span>}
          <CustomerLookup id={customerId} locale={locale} />
        </div>
      </div>
      <h3>{c.items}</h3>
      {fields.map((field, index) => <div className="form-grid" key={field.id}>
        <div className="field"><label htmlFor={`order-product-${field.id}`}>{c.productId}</label>
          <input id={`order-product-${field.id}`} type="number" min="1" step="1" {...form.register(`items.${index}.product_id`, { valueAsNumber: true })} aria-invalid={Boolean(form.formState.errors.items?.[index]?.product_id)} />
          {form.formState.errors.items?.[index]?.product_id && <span className="error">{c.required}</span>}
          <ProductLookup id={itemValues?.[index]?.product_id ?? 0} locale={locale} />
        </div>
        <div className="field"><label htmlFor={`order-quantity-${field.id}`}>{c.quantity}</label>
          <input id={`order-quantity-${field.id}`} type="number" min="1" step="1" {...form.register(`items.${index}.quantity`, { valueAsNumber: true })} aria-invalid={Boolean(form.formState.errors.items?.[index]?.quantity)} />
          {form.formState.errors.items?.[index]?.quantity && <span className="error">{c.required}</span>}
        </div>
        <div className="actions"><Button type="button" variant="ghost" onClick={() => remove(index)} disabled={fields.length === 1}>{c.remove}</Button></div>
      </div>)}
      {form.formState.errors.items?.root && <p className="error" role="alert">{c.duplicate}</p>}
      {form.formState.errors.items?.message && <p className="error" role="alert">{c.duplicate}</p>}
      <div className="actions">
        <Button type="button" variant="secondary" onClick={() => append({ product_id: 0, quantity: 1 })}>{c.addItem}</Button>
        <Button type="submit" loading={mutation.isPending}>{c.save}</Button>
        <Button type="button" variant="ghost" onClick={onClose}>{c.cancel}</Button>
      </div>
      {mutation.isError && <p className="error" role="alert">{getApiErrorMessage(mutation.error, locale)}</p>}
      {success && <p className="notice" role="status">{success}</p>}
    </form>
  </Card>;
}

export function OrdersPage() {
  const { locale } = useI18n();
  const { user } = useAuth();
  const c = copy(locale);
  const [page, setPage] = useState(1);
  const [showCreate, setShowCreate] = useState(false);
  const query = useQuery({
    queryKey: ['orders', 'list', page],
    queryFn: async () => (await api.get<Page<Order>>('/orders', { params: { page, page_size: PAGE_SIZE } })).data,
  });
  return <main className="page">
    <PageHeader title={c.title} description={c.description} actions={user?.role !== 'viewer' && <Button onClick={() => setShowCreate((current) => !current)}>{c.new}</Button>} />
    {showCreate && user?.role !== 'viewer' && <CreateOrder onClose={() => setShowCreate(false)} />}
    <Card>
      {query.isPending ? <Spinner label={c.title} /> : query.isError ? <ErrorState message={getApiErrorMessage(query.error, locale)} onRetry={() => void query.refetch()} /> : query.data.items.length === 0 ? <EmptyState title={c.empty} /> : <>
        <div style={{ overflowX: 'auto' }}><table className="data-table">
          <thead><tr><th scope="col">{c.id}</th><th scope="col">{c.customer}</th><th scope="col">{c.status}</th><th scope="col">{c.total}</th><th scope="col">{c.created}</th><th scope="col"><span className="sr-only">{c.details}</span></th></tr></thead>
          <tbody>{query.data.items.map((order) => <tr key={order.id}>
            <td>#{order.id}</td><td>#{order.customer_id}</td><td><OrderBadge status={order.status} locale={locale} /></td><td>{money(order.total_amount, locale)}</td><td>{dateTime(order.created_at, locale)}</td><td><Link to={`/orders/${order.id}`}>{c.details}</Link></td>
          </tr>)}</tbody>
        </table></div>
        <Pagination page={page} pages={query.data.pages} onPageChange={setPage} />
      </>}
    </Card>
  </main>;
}

export function OrderDetailPage() {
  const { orderId } = useParams();
  const id = Number(orderId);
  const { locale } = useI18n();
  const { user } = useAuth();
  const c = copy(locale);
  const queryClient = useQueryClient();
  const [nextStatus, setNextStatus] = useState<OrderStatus | ''>('');
  const [feedback, setFeedback] = useState('');
  const query = useQuery({
    queryKey: ['orders', id],
    queryFn: async () => (await api.get<Order>(`/orders/${id}`)).data,
    enabled: Number.isInteger(id) && id > 0,
  });
  const order = query.data;
  const customerQuery = useQuery({
    queryKey: ['customers', order?.customer_id],
    queryFn: async () => (await api.get<CustomerSummary>(`/customers/${order?.customer_id}`)).data,
    enabled: Boolean(order), retry: false,
  });
  const productQueries = useQueries({ queries: (order?.items ?? []).map((item) => ({
    queryKey: ['products', item.product_id],
    queryFn: async () => (await api.get<ProductSummary>(`/products/${item.product_id}`)).data,
    retry: false,
  })) });
  const mutation = useMutation({
    mutationFn: async (status: OrderStatus) => (await api.patch<Order>(`/orders/${id}`, { status })).data,
    onSuccess: async () => {
      setFeedback(c.updateSuccess); setNextStatus('');
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['orders'] }),
        queryClient.invalidateQueries({ queryKey: ['products'] }),
        queryClient.invalidateQueries({ queryKey: ['statistics', 'overview'] }),
      ]);
    },
    onError: async () => { await queryClient.invalidateQueries({ queryKey: ['orders', id] }); },
  });
  if (!Number.isInteger(id) || id <= 0) return <main className="page"><ErrorState message={c.detailTitle} /></main>;
  return <main className="page">
    <PageHeader title={`${c.detailTitle} #${id}`} actions={<Link to="/orders">{c.back}</Link>} />
    {query.isPending ? <Spinner label={c.detailTitle} /> : query.isError ? <ErrorState message={getApiErrorMessage(query.error, locale)} onRetry={() => void query.refetch()} /> : order && <>
      <Card>
        <dl className="form-grid">
          <div><dt>{c.status}</dt><dd><OrderBadge status={order.status} locale={locale} /></dd></div>
          <div><dt>{c.total}</dt><dd>{money(order.total_amount, locale)}</dd></div>
          <div><dt>{c.customer}</dt><dd><Link to={`/customers/${order.customer_id}`}>{customerQuery.data?.name ?? `#${order.customer_id}`}</Link>{customerQuery.data?.email && <span className="muted"> · {customerQuery.data.email}</span>}</dd></div>
          <div><dt>{c.created}</dt><dd>{dateTime(order.created_at, locale)}</dd></div>
        </dl>
        {customerQuery.isError && <p className="error" role="status">{c.relatedWarning}</p>}
      </Card>
      <Card><h2>{c.items}</h2><div style={{ overflowX: 'auto' }}><table className="data-table">
        <thead><tr><th scope="col">{c.product}</th><th scope="col">{c.quantity}</th><th scope="col">{c.total}</th></tr></thead>
        <tbody>{order.items.map((item, index) => <tr key={item.id}><td><Link to={`/products/${item.product_id}`}>{productQueries[index]?.data?.name ?? `#${item.product_id}`}</Link>{productQueries[index]?.isError && <span className="error"> · {c.productMissing}</span>}</td><td>{item.quantity}</td><td>{money(Number(item.unit_price) * item.quantity, locale)}</td></tr>)}</tbody>
      </table></div></Card>
      <Card><h2>{c.payments}</h2><p className="muted">{c.paymentsHint} #{order.id}.</p><Link to="/payments">{c.viewPayments}</Link></Card>
      {user?.role !== 'viewer' && <Card><h2>{c.changeStatus}</h2>
        {orderTransitions[order.status].length === 0 ? <p className="muted">{c.noTransition}</p> : <div className="actions">
          <label className="field">{c.status}<select value={nextStatus} onChange={(event) => setNextStatus(event.target.value as OrderStatus)}><option value="">—</option>{orderTransitions[order.status].map((status) => <option key={status} value={status}>{orderStatusLabel(status, locale)}</option>)}</select></label>
          <Button onClick={() => { if (nextStatus) mutation.mutate(nextStatus); }} disabled={!nextStatus} loading={mutation.isPending}>{c.update}</Button>
        </div>}
        {mutation.isError && <p className="error" role="alert">{getApiErrorMessage(mutation.error, locale)}</p>}
        {feedback && <p className="notice" role="status">{feedback}</p>}
      </Card>}
    </>}
  </main>;
}
