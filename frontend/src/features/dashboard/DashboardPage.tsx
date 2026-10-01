import { useQuery } from '@tanstack/react-query'

import { api, getApiErrorMessage } from '../../api/client'
import { Badge, Card, ErrorState, PageHeader, Spinner } from '../../components/common'
import { formatCurrency, formatDate, useI18n, type Locale } from '../../i18n'
import './dashboard.css'

type OrderStatus = 'pending' | 'processing' | 'confirmed' | 'shipped' | 'delivered' | 'cancelled'
type PaymentStatus = 'pending' | 'approved' | 'failed' | 'partially_refunded' | 'refunded'

export interface StatisticsOverview {
  orders: {
    total: number
    by_status: Record<OrderStatus, number>
    total_amount: string
    average_ticket: string
    created_in_period: number
  }
  payments: {
    total: number
    by_status: Record<PaymentStatus, number>
    total_processed_amount: string
    successful_amount: string
    successful_count: number
    success_rate: string
  }
  operational: {
    pending_orders: number
    processing_orders: number
    confirmed_orders: number
    cancelled_orders: number
  }
  generated_at: string
}

const copy = {
  'pt-BR': {
    title: 'Visão geral',
    description: 'Um retrato atualizado dos pedidos e pagamentos.',
    orders: 'Pedidos',
    orderValue: 'Valor dos pedidos',
    averageTicket: 'Ticket médio',
    processedValue: 'Valor processado',
    successfulValue: 'Pagamentos bem-sucedidos',
    successRate: 'Taxa de sucesso',
    orderStatus: 'Pedidos por status',
    paymentStatus: 'Pagamentos por status',
    operational: 'Fila operacional',
    generated: 'Atualizado em',
    pending: 'Pendente',
    processing: 'Em processamento',
    confirmed: 'Confirmado',
    shipped: 'Enviado',
    delivered: 'Entregue',
    cancelled: 'Cancelado',
    approved: 'Aprovado',
    failed: 'Falhou',
    partially_refunded: 'Parcialmente reembolsado',
    refunded: 'Reembolsado',
  },
  en: {
    title: 'Overview',
    description: 'A current view of orders and payments.',
    orders: 'Orders',
    orderValue: 'Order value',
    averageTicket: 'Average order value',
    processedValue: 'Processed value',
    successfulValue: 'Successful payments',
    successRate: 'Success rate',
    orderStatus: 'Orders by status',
    paymentStatus: 'Payments by status',
    operational: 'Operations queue',
    generated: 'Updated at',
    pending: 'Pending',
    processing: 'Processing',
    confirmed: 'Confirmed',
    shipped: 'Shipped',
    delivered: 'Delivered',
    cancelled: 'Cancelled',
    approved: 'Approved',
    failed: 'Failed',
    partially_refunded: 'Partially refunded',
    refunded: 'Refunded',
  },
} as const

const orderStatuses: OrderStatus[] = ['pending', 'processing', 'confirmed', 'shipped', 'delivered', 'cancelled']
const paymentStatuses: PaymentStatus[] = ['pending', 'approved', 'failed', 'partially_refunded', 'refunded']

function statusTone(status: OrderStatus | PaymentStatus) {
  if (status === 'approved' || status === 'delivered' || status === 'confirmed') return 'success' as const
  if (status === 'failed' || status === 'cancelled') return 'danger' as const
  if (status === 'pending' || status === 'processing') return 'warning' as const
  return 'info' as const
}

function Metric({ label, value, note }: { label: string; value: string | number; note?: string }) {
  return (
    <Card className="dashboard-metric">
      <span className="dashboard-metric-label">{label}</span>
      <strong className="dashboard-metric-value">{value}</strong>
      {note && <span className="muted">{note}</span>}
    </Card>
  )
}

function StatusBreakdown({ title, counts, statuses, locale }: {
  title: string
  counts: Partial<Record<OrderStatus | PaymentStatus, number>>
  statuses: Array<OrderStatus | PaymentStatus>
  locale: Locale
}) {
  const total = statuses.reduce((sum, status) => sum + (counts[status] ?? 0), 0)
  return (
    <Card className="dashboard-breakdown">
      <h2>{title}</h2>
      <ul className="dashboard-status-list">
        {statuses.map((status) => (
          <li key={status}>
            <span><Badge tone={statusTone(status)}>{copy[locale][status]}</Badge></span>
            <strong>{(counts[status] ?? 0).toLocaleString(locale)}</strong>
          </li>
        ))}
      </ul>
      <div className="dashboard-bar" aria-hidden="true">
        {total > 0 && statuses.map((status) => (
          <span key={status} className={`dashboard-bar-${status}`} style={{ width: `${((counts[status] ?? 0) / total) * 100}%` }} />
        ))}
      </div>
    </Card>
  )
}

export function DashboardPage() {
  const { locale } = useI18n()
  const c = copy[locale]
  const overview = useQuery({
    queryKey: ['statistics', 'overview'],
    queryFn: async () => (await api.get<StatisticsOverview>('/statistics/overview')).data,
  })

  return (
    <div className="page">
      <PageHeader title={c.title} description={c.description} />
      {overview.isPending && <Spinner label={locale === 'pt-BR' ? 'Carregando indicadores' : 'Loading metrics'} />}
      {overview.isError && <ErrorState message={getApiErrorMessage(overview.error, locale)} onRetry={() => void overview.refetch()} />}
      {overview.data && (
        <>
          <div className="dashboard-metrics">
            <Metric label={c.orders} value={overview.data.orders.total.toLocaleString(locale)} />
            <Metric label={c.orderValue} value={formatCurrency(overview.data.orders.total_amount, locale)} />
            <Metric label={c.averageTicket} value={formatCurrency(overview.data.orders.average_ticket, locale)} />
            <Metric label={c.processedValue} value={formatCurrency(overview.data.payments.total_processed_amount, locale)} />
            <Metric label={c.successfulValue} value={formatCurrency(overview.data.payments.successful_amount, locale)} note={`${overview.data.payments.successful_count.toLocaleString(locale)} ${locale === 'pt-BR' ? 'pagamentos' : 'payments'}`} />
            <Metric label={c.successRate} value={`${Number(overview.data.payments.success_rate).toLocaleString(locale, { maximumFractionDigits: 2 })}%`} />
          </div>
          <div className="dashboard-breakdowns">
            <StatusBreakdown title={c.orderStatus} counts={overview.data.orders.by_status} statuses={orderStatuses} locale={locale} />
            <StatusBreakdown title={c.paymentStatus} counts={overview.data.payments.by_status} statuses={paymentStatuses} locale={locale} />
          </div>
          <Card className="dashboard-operations">
            <h2>{c.operational}</h2>
            <div className="dashboard-operation-grid">
              <span>{c.pending}<strong>{overview.data.operational.pending_orders.toLocaleString(locale)}</strong></span>
              <span>{c.processing}<strong>{overview.data.operational.processing_orders.toLocaleString(locale)}</strong></span>
              <span>{c.confirmed}<strong>{overview.data.operational.confirmed_orders.toLocaleString(locale)}</strong></span>
              <span>{c.cancelled}<strong>{overview.data.operational.cancelled_orders.toLocaleString(locale)}</strong></span>
            </div>
          </Card>
          <p className="dashboard-timestamp muted">{c.generated} {formatDate(overview.data.generated_at, locale)}</p>
        </>
      )}
    </div>
  )
}
