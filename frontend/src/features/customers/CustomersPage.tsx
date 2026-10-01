import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { Link, useNavigate, useParams } from 'react-router'
import { z } from 'zod'

import { getApiErrorMessage } from '../../api/client'
import { Button, Card, ConfirmDialog, EmptyState, ErrorState, PageHeader, Pagination, Spinner } from '../../components/common'
import { useAuth } from '../auth/AuthProvider'
import { formatDate, useI18n, type Locale } from '../../i18n'
import {
  createCustomer, customerKeys, deleteCustomer, getCustomer, listCustomers, updateCustomer,
  type Customer, type CustomerPayload,
} from './customerApi'
import './catalog.css'

const copy = {
  'pt-BR': {
    title: 'Clientes', description: 'Cadastros e contatos dos clientes.', add: 'Novo cliente',
    create: 'Criar cliente', edit: 'Editar cliente', details: 'Detalhes do cliente',
    name: 'Nome', email: 'E-mail', phone: 'Telefone', created: 'Criado em', updated: 'Atualizado em',
    actions: 'Ações', view: 'Ver detalhes', save: 'Salvar alterações', cancel: 'Cancelar',
    delete: 'Excluir cliente', deleteTitle: 'Excluir este cliente?',
    deleteDescription: 'Esta ação é permanente. Clientes com pedidos vinculados não podem ser excluídos.',
    empty: 'Nenhum cliente cadastrado', emptyDescription: 'Novos clientes aparecerão aqui.',
    loading: 'Carregando clientes', missing: 'Cliente não encontrado.', back: 'Voltar para clientes',
    required: 'Campo obrigatório.', invalidEmail: 'Informe um e-mail válido.',
    maxName: 'Use até 120 caracteres.', maxEmail: 'Use até 320 caracteres.', maxPhone: 'Use até 32 caracteres.',
    duplicate: 'Já existe um cliente com este e-mail.', deleteConflict: 'Este cliente tem registros vinculados e não pode ser excluído.',
    createdNotice: 'Cliente criado com sucesso.', updatedNotice: 'Cliente atualizado com sucesso.',
    optional: 'Opcional', total: 'Total de clientes',
  },
  en: {
    title: 'Customers', description: 'Customer records and contact details.', add: 'New customer',
    create: 'Create customer', edit: 'Edit customer', details: 'Customer details',
    name: 'Name', email: 'Email', phone: 'Phone', created: 'Created', updated: 'Updated',
    actions: 'Actions', view: 'View details', save: 'Save changes', cancel: 'Cancel',
    delete: 'Delete customer', deleteTitle: 'Delete this customer?',
    deleteDescription: 'This action is permanent. Customers linked to orders cannot be deleted.',
    empty: 'No customers yet', emptyDescription: 'New customers will appear here.',
    loading: 'Loading customers', missing: 'Customer not found.', back: 'Back to customers',
    required: 'This field is required.', invalidEmail: 'Enter a valid email address.',
    maxName: 'Use 120 characters or fewer.', maxEmail: 'Use 320 characters or fewer.', maxPhone: 'Use 32 characters or fewer.',
    duplicate: 'A customer with this email already exists.', deleteConflict: 'This customer has related records and cannot be deleted.',
    createdNotice: 'Customer created.', updatedNotice: 'Customer updated.',
    optional: 'Optional', total: 'Total customers',
  },
} as const

function schema(locale: Locale) {
  const c = copy[locale]
  return z.object({
    name: z.string().trim().min(1, c.required).max(120, c.maxName),
    email: z.string().trim().min(1, c.required).max(320, c.maxEmail).email(c.invalidEmail),
    phone: z.string().trim().max(32, c.maxPhone),
  })
}

type CustomerFormValues = z.infer<ReturnType<typeof schema>>

function customerError(error: unknown, locale: Locale, action: 'save' | 'delete') {
  const status = (error as { response?: { status?: number } })?.response?.status
  if (status === 409) return action === 'delete' ? copy[locale].deleteConflict : copy[locale].duplicate
  return getApiErrorMessage(error, locale)
}

function CustomerForm({ customer, locale, busy, error, onSave, onCancel }: {
  customer?: Customer
  locale: Locale
  busy: boolean
  error: string | null
  onSave: (payload: CustomerPayload) => void
  onCancel: () => void
}) {
  const c = copy[locale]
  const { register, handleSubmit, formState: { errors } } = useForm<CustomerFormValues>({
    resolver: zodResolver(schema(locale)),
    defaultValues: { name: customer?.name ?? '', email: customer?.email ?? '', phone: customer?.phone ?? '' },
  })

  const submit = (values: CustomerFormValues) => onSave({
    name: values.name,
    email: values.email,
    phone: values.phone || null,
  })

  return (
    <Card className="catalog-form-card">
      <h2>{customer ? c.edit : c.create}</h2>
      <form onSubmit={handleSubmit(submit)} noValidate>
        <div className="form-grid">
          <div className="field">
            <label htmlFor="customer-name">{c.name}</label>
            <input id="customer-name" autoComplete="name" aria-invalid={Boolean(errors.name)} aria-describedby={errors.name ? 'customer-name-error' : undefined} {...register('name')} />
            {errors.name && <small id="customer-name-error" className="error">{errors.name.message}</small>}
          </div>
          <div className="field">
            <label htmlFor="customer-email">{c.email}</label>
            <input id="customer-email" type="email" autoComplete="email" aria-invalid={Boolean(errors.email)} aria-describedby={errors.email ? 'customer-email-error' : undefined} {...register('email')} />
            {errors.email && <small id="customer-email-error" className="error">{errors.email.message}</small>}
          </div>
          <div className="field">
            <label htmlFor="customer-phone">{c.phone} <span className="muted">({c.optional})</span></label>
            <input id="customer-phone" type="tel" autoComplete="tel" aria-invalid={Boolean(errors.phone)} aria-describedby={errors.phone ? 'customer-phone-error' : undefined} {...register('phone')} />
            {errors.phone && <small id="customer-phone-error" className="error">{errors.phone.message}</small>}
          </div>
        </div>
        {error && <p className="catalog-form-error" role="alert">{error}</p>}
        <div className="actions">
          <Button type="submit" loading={busy}>{customer ? c.save : c.create}</Button>
          <Button type="button" variant="secondary" onClick={onCancel}>{c.cancel}</Button>
        </div>
      </form>
    </Card>
  )
}

export function CustomersPage() {
  const { locale } = useI18n()
  const c = copy[locale]
  const { user } = useAuth()
  const canWrite = user?.role === 'operator' || user?.role === 'admin'
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [showCreate, setShowCreate] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const customers = useQuery({ queryKey: customerKeys.list(page), queryFn: () => listCustomers(page) })
  const create = useMutation({
    mutationFn: createCustomer,
    onSuccess: () => {
      setShowCreate(false)
      setNotice(c.createdNotice)
      setPage(1)
      void queryClient.invalidateQueries({ queryKey: customerKeys.all })
    },
  })

  return (
    <div className="page">
      <PageHeader title={c.title} description={c.description} actions={canWrite && <Button onClick={() => { setShowCreate(true); setNotice(null); create.reset() }}>{c.add}</Button>} />
      {notice && <p className="catalog-notice" role="status">{notice}</p>}
      {showCreate && canWrite && <CustomerForm locale={locale} busy={create.isPending} error={create.isError ? customerError(create.error, locale, 'save') : null} onSave={(data) => create.mutate(data)} onCancel={() => setShowCreate(false)} />}
      <Card>
        {customers.isPending && <Spinner label={c.loading} />}
        {customers.isError && <ErrorState message={getApiErrorMessage(customers.error, locale)} onRetry={() => void customers.refetch()} />}
        {customers.data && (
          <>
            <div className="catalog-table-heading"><span className="muted">{c.total}: {customers.data.total.toLocaleString(locale)}</span></div>
            {customers.data.items.length === 0 ? <EmptyState title={c.empty} description={c.emptyDescription} /> : (
              <div className="table-wrap">
                <table className="data-table">
                  <thead><tr><th scope="col">{c.name}</th><th scope="col">{c.email}</th><th scope="col">{c.phone}</th><th scope="col">{c.created}</th><th scope="col">{c.actions}</th></tr></thead>
                  <tbody>{customers.data.items.map((customer) => (
                    <tr key={customer.id}>
                      <td><Link className="catalog-primary-link" to={`/customers/${customer.id}`}>{customer.name}</Link></td>
                      <td>{customer.email}</td>
                      <td>{customer.phone || '—'}</td>
                      <td>{formatDate(customer.created_at, locale)}</td>
                      <td><Link to={`/customers/${customer.id}`}>{c.view}</Link></td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
            <Pagination page={page} pages={Math.max(1, customers.data.pages)} onPageChange={setPage} />
          </>
        )}
      </Card>
    </div>
  )
}

export function CustomerDetailPage() {
  const { locale } = useI18n()
  const c = copy[locale]
  const { user } = useAuth()
  const canWrite = user?.role === 'operator' || user?.role === 'admin'
  const canDelete = user?.role === 'admin'
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const idParam = useParams().customerId
  const id = Number(idParam)
  const validId = Number.isSafeInteger(id) && id > 0
  const [editing, setEditing] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const customer = useQuery({ queryKey: customerKeys.detail(id), queryFn: () => getCustomer(id), enabled: validId })
  const update = useMutation({
    mutationFn: (data: CustomerPayload) => updateCustomer(id, data),
    onSuccess: () => {
      setEditing(false)
      setNotice(c.updatedNotice)
      void queryClient.invalidateQueries({ queryKey: customerKeys.all })
    },
  })
  const remove = useMutation({
    mutationFn: () => deleteCustomer(id),
    onError: () => setConfirmDelete(false),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: customerKeys.all })
      navigate('/customers')
    },
  })

  return (
    <div className="page">
      <Link className="catalog-back-link" to="/customers">← {c.back}</Link>
      <PageHeader title={customer.data?.name ?? c.details} description={customer.data ? `${c.email}: ${customer.data.email}` : undefined} actions={customer.data && <div className="actions">{canWrite && <Button variant="secondary" onClick={() => { setEditing(true); setNotice(null); update.reset() }}>{c.edit}</Button>}{canDelete && <Button variant="danger" onClick={() => { setConfirmDelete(true); remove.reset() }}>{c.delete}</Button>}</div>} />
      {!validId && <ErrorState message={c.missing} />}
      {validId && customer.isPending && <Spinner label={c.loading} />}
      {validId && customer.isError && <ErrorState message={getApiErrorMessage(customer.error, locale)} onRetry={() => void customer.refetch()} />}
      {notice && <p className="catalog-notice" role="status">{notice}</p>}
      {customer.data && (
        <>
          {editing && canWrite && <CustomerForm key={customer.data.id} customer={customer.data} locale={locale} busy={update.isPending} error={update.isError ? customerError(update.error, locale, 'save') : null} onSave={(data) => update.mutate(data)} onCancel={() => setEditing(false)} />}
          <Card>
            <dl className="catalog-details">
              <div><dt>{c.name}</dt><dd>{customer.data.name}</dd></div>
              <div><dt>{c.email}</dt><dd><a href={`mailto:${customer.data.email}`}>{customer.data.email}</a></dd></div>
              <div><dt>{c.phone}</dt><dd>{customer.data.phone || '—'}</dd></div>
              <div><dt>{c.created}</dt><dd>{formatDate(customer.data.created_at, locale)}</dd></div>
              <div><dt>{c.updated}</dt><dd>{formatDate(customer.data.updated_at, locale)}</dd></div>
            </dl>
          </Card>
          {remove.isError && <p role="alert" className="catalog-form-error">{customerError(remove.error, locale, 'delete')}</p>}
          <ConfirmDialog open={confirmDelete} title={c.deleteTitle} description={c.deleteDescription} confirmLabel={c.delete} busy={remove.isPending} danger onConfirm={() => remove.mutate()} onClose={() => setConfirmDelete(false)} />
        </>
      )}
    </div>
  )
}
