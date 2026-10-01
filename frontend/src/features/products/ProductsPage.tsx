import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { Link, useNavigate, useParams } from 'react-router'
import { z } from 'zod'

import { getApiErrorMessage } from '../../api/client'
import { Badge, Button, Card, ConfirmDialog, EmptyState, ErrorState, PageHeader, Pagination, Spinner } from '../../components/common'
import { formatCurrency, formatDate, useI18n, type Locale } from '../../i18n'
import { useAuth } from '../auth/AuthProvider'
import { createProduct, deleteProduct, getProduct, listProducts, productKeys, updateProduct, type Product, type ProductPayload } from './productApi'
import '../customers/catalog.css'

const copy = {
  'pt-BR': {
    title: 'Produtos', description: 'Catálogo, preços e disponibilidade.', add: 'Novo produto',
    create: 'Criar produto', edit: 'Editar produto', details: 'Detalhes do produto',
    sku: 'SKU', name: 'Nome', descriptionField: 'Descrição', price: 'Preço', stock: 'Estoque',
    active: 'Ativo', inactive: 'Inativo', availability: 'Disponibilidade', created: 'Criado em', updated: 'Atualizado em',
    actions: 'Ações', view: 'Ver detalhes', save: 'Salvar alterações', cancel: 'Cancelar',
    delete: 'Excluir produto', deleteTitle: 'Excluir este produto?',
    deleteDescription: 'Esta ação é permanente. Produtos vinculados a pedidos não podem ser excluídos.',
    empty: 'Nenhum produto cadastrado', emptyDescription: 'Novos produtos aparecerão aqui.',
    loading: 'Carregando produtos', missing: 'Produto não encontrado.', back: 'Voltar para produtos',
    required: 'Campo obrigatório.', maxSku: 'Use até 64 caracteres.', maxName: 'Use até 160 caracteres.',
    invalidPrice: 'Informe um valor entre 0 e 9.999.999.999,99, com até duas casas decimais.',
    invalidStock: 'Informe um número inteiro entre 0 e 2.147.483.647.',
    duplicate: 'Já existe um produto com este SKU.', deleteConflict: 'Este produto tem pedidos vinculados e não pode ser excluído.',
    createdNotice: 'Produto criado com sucesso.', updatedNotice: 'Produto atualizado com sucesso.',
    optional: 'Opcional', total: 'Total de produtos',
  },
  en: {
    title: 'Products', description: 'Catalog, prices, and availability.', add: 'New product',
    create: 'Create product', edit: 'Edit product', details: 'Product details',
    sku: 'SKU', name: 'Name', descriptionField: 'Description', price: 'Price', stock: 'Stock',
    active: 'Active', inactive: 'Inactive', availability: 'Availability', created: 'Created', updated: 'Updated',
    actions: 'Actions', view: 'View details', save: 'Save changes', cancel: 'Cancel',
    delete: 'Delete product', deleteTitle: 'Delete this product?',
    deleteDescription: 'This action is permanent. Products linked to orders cannot be deleted.',
    empty: 'No products yet', emptyDescription: 'New products will appear here.',
    loading: 'Loading products', missing: 'Product not found.', back: 'Back to products',
    required: 'This field is required.', maxSku: 'Use 64 characters or fewer.', maxName: 'Use 160 characters or fewer.',
    invalidPrice: 'Enter a value from 0 to 9,999,999,999.99 with up to two decimal places.',
    invalidStock: 'Enter an integer from 0 to 2,147,483,647.',
    duplicate: 'A product with this SKU already exists.', deleteConflict: 'This product has linked orders and cannot be deleted.',
    createdNotice: 'Product created.', updatedNotice: 'Product updated.',
    optional: 'Optional', total: 'Total products',
  },
} as const

function productSchema(locale: Locale) {
  const c = copy[locale]
  return z.object({
    sku: z.string().trim().min(1, c.required).max(64, c.maxSku),
    name: z.string().trim().min(1, c.required).max(160, c.maxName),
    description: z.string(),
    price: z.string().trim().regex(/^\d{1,10}(?:[.,]\d{1,2})?$/, c.invalidPrice),
    stock: z.string().trim().regex(/^\d+$/, c.invalidStock).refine((value) => Number(value) <= 2_147_483_647, c.invalidStock),
    is_active: z.boolean(),
  })
}

type ProductFormValues = z.infer<ReturnType<typeof productSchema>>

function productError(error: unknown, locale: Locale, action: 'save' | 'delete') {
  const status = (error as { response?: { status?: number } })?.response?.status
  if (status === 409) return action === 'delete' ? copy[locale].deleteConflict : copy[locale].duplicate
  return getApiErrorMessage(error, locale)
}

function ProductForm({ product, locale, busy, error, onSave, onCancel }: {
  product?: Product
  locale: Locale
  busy: boolean
  error: string | null
  onSave: (payload: ProductPayload) => void
  onCancel: () => void
}) {
  const c = copy[locale]
  const { register, handleSubmit, formState: { errors } } = useForm<ProductFormValues>({
    resolver: zodResolver(productSchema(locale)),
    defaultValues: {
      sku: product?.sku ?? '', name: product?.name ?? '', description: product?.description ?? '',
      price: product?.price ?? '', stock: String(product?.stock ?? 0), is_active: product?.is_active ?? true,
    },
  })

  const submit = (values: ProductFormValues) => onSave({
    sku: values.sku,
    name: values.name,
    description: values.description.trim() || null,
    price: values.price.replace(',', '.'),
    stock: Number(values.stock),
    is_active: values.is_active,
  })

  return (
    <Card className="catalog-form-card">
      <h2>{product ? c.edit : c.create}</h2>
      <form onSubmit={handleSubmit(submit)} noValidate>
        <div className="form-grid">
          <div className="field">
            <label htmlFor="product-sku">{c.sku}</label>
            <input id="product-sku" aria-invalid={Boolean(errors.sku)} aria-describedby={errors.sku ? 'product-sku-error' : undefined} {...register('sku')} />
            {errors.sku && <small id="product-sku-error" className="error">{errors.sku.message}</small>}
          </div>
          <div className="field">
            <label htmlFor="product-name">{c.name}</label>
            <input id="product-name" aria-invalid={Boolean(errors.name)} aria-describedby={errors.name ? 'product-name-error' : undefined} {...register('name')} />
            {errors.name && <small id="product-name-error" className="error">{errors.name.message}</small>}
          </div>
          <div className="field">
            <label htmlFor="product-price">{c.price}</label>
            <input id="product-price" inputMode="decimal" aria-invalid={Boolean(errors.price)} aria-describedby={errors.price ? 'product-price-error' : undefined} {...register('price')} />
            {errors.price && <small id="product-price-error" className="error">{errors.price.message}</small>}
          </div>
          <div className="field">
            <label htmlFor="product-stock">{c.stock}</label>
            <input id="product-stock" inputMode="numeric" aria-invalid={Boolean(errors.stock)} aria-describedby={errors.stock ? 'product-stock-error' : undefined} {...register('stock')} />
            {errors.stock && <small id="product-stock-error" className="error">{errors.stock.message}</small>}
          </div>
          <div className="field catalog-form-wide">
            <label htmlFor="product-description">{c.descriptionField} <span className="muted">({c.optional})</span></label>
            <textarea id="product-description" {...register('description')} />
          </div>
          <label className="catalog-checkbox"><input type="checkbox" {...register('is_active')} />{c.active}</label>
        </div>
        {error && <p role="alert" className="catalog-form-error">{error}</p>}
        <div className="actions"><Button type="submit" loading={busy}>{product ? c.save : c.create}</Button><Button type="button" variant="secondary" onClick={onCancel}>{c.cancel}</Button></div>
      </form>
    </Card>
  )
}

export function ProductsPage() {
  const { locale } = useI18n()
  const c = copy[locale]
  const { user } = useAuth()
  const canWrite = user?.role === 'operator' || user?.role === 'admin'
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [showCreate, setShowCreate] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const products = useQuery({ queryKey: productKeys.list(page), queryFn: () => listProducts(page) })
  const create = useMutation({
    mutationFn: createProduct,
    onSuccess: () => {
      setShowCreate(false)
      setNotice(c.createdNotice)
      setPage(1)
      void queryClient.invalidateQueries({ queryKey: productKeys.all })
    },
  })

  return (
    <div className="page">
      <PageHeader title={c.title} description={c.description} actions={canWrite && <Button onClick={() => { setShowCreate(true); setNotice(null); create.reset() }}>{c.add}</Button>} />
      {notice && <p className="catalog-notice" role="status">{notice}</p>}
      {showCreate && canWrite && <ProductForm locale={locale} busy={create.isPending} error={create.isError ? productError(create.error, locale, 'save') : null} onSave={(data) => create.mutate(data)} onCancel={() => setShowCreate(false)} />}
      <Card>
        {products.isPending && <Spinner label={c.loading} />}
        {products.isError && <ErrorState message={getApiErrorMessage(products.error, locale)} onRetry={() => void products.refetch()} />}
        {products.data && (
          <>
            <div className="catalog-table-heading"><span className="muted">{c.total}: {products.data.total.toLocaleString(locale)}</span></div>
            {products.data.items.length === 0 ? <EmptyState title={c.empty} description={c.emptyDescription} /> : (
              <div className="table-wrap">
                <table className="data-table">
                  <thead><tr><th scope="col">{c.sku}</th><th scope="col">{c.name}</th><th scope="col">{c.price}</th><th scope="col">{c.stock}</th><th scope="col">{c.availability}</th><th scope="col">{c.actions}</th></tr></thead>
                  <tbody>{products.data.items.map((product) => (
                    <tr key={product.id}>
                      <td><span className="catalog-sku">{product.sku}</span></td>
                      <td><Link className="catalog-primary-link" to={`/products/${product.id}`}>{product.name}</Link></td>
                      <td>{formatCurrency(product.price, locale)}</td>
                      <td>{product.stock.toLocaleString(locale)}</td>
                      <td><Badge tone={product.is_active ? 'success' : 'neutral'}>{product.is_active ? c.active : c.inactive}</Badge></td>
                      <td><Link to={`/products/${product.id}`}>{c.view}</Link></td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
            <Pagination page={page} pages={Math.max(1, products.data.pages)} onPageChange={setPage} />
          </>
        )}
      </Card>
    </div>
  )
}

export function ProductDetailPage() {
  const { locale } = useI18n()
  const c = copy[locale]
  const { user } = useAuth()
  const canWrite = user?.role === 'operator' || user?.role === 'admin'
  const canDelete = user?.role === 'admin'
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const idParam = useParams().productId
  const id = Number(idParam)
  const validId = Number.isSafeInteger(id) && id > 0
  const [editing, setEditing] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const product = useQuery({ queryKey: productKeys.detail(id), queryFn: () => getProduct(id), enabled: validId })
  const update = useMutation({
    mutationFn: (data: ProductPayload) => updateProduct(id, data),
    onSuccess: () => {
      setEditing(false)
      setNotice(c.updatedNotice)
      void queryClient.invalidateQueries({ queryKey: productKeys.all })
    },
  })
  const remove = useMutation({
    mutationFn: () => deleteProduct(id),
    onError: () => setConfirmDelete(false),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: productKeys.all })
      navigate('/products')
    },
  })

  return (
    <div className="page">
      <Link className="catalog-back-link" to="/products">← {c.back}</Link>
      <PageHeader title={product.data?.name ?? c.details} description={product.data ? `${c.sku}: ${product.data.sku}` : undefined} actions={product.data && <div className="actions">{canWrite && <Button variant="secondary" onClick={() => { setEditing(true); setNotice(null); update.reset() }}>{c.edit}</Button>}{canDelete && <Button variant="danger" onClick={() => { setConfirmDelete(true); remove.reset() }}>{c.delete}</Button>}</div>} />
      {!validId && <ErrorState message={c.missing} />}
      {validId && product.isPending && <Spinner label={c.loading} />}
      {validId && product.isError && <ErrorState message={getApiErrorMessage(product.error, locale)} onRetry={() => void product.refetch()} />}
      {notice && <p className="catalog-notice" role="status">{notice}</p>}
      {product.data && (
        <>
          {editing && canWrite && <ProductForm key={product.data.id} product={product.data} locale={locale} busy={update.isPending} error={update.isError ? productError(update.error, locale, 'save') : null} onSave={(data) => update.mutate(data)} onCancel={() => setEditing(false)} />}
          <Card>
            <dl className="catalog-details">
              <div><dt>{c.sku}</dt><dd className="catalog-sku">{product.data.sku}</dd></div>
              <div><dt>{c.availability}</dt><dd><Badge tone={product.data.is_active ? 'success' : 'neutral'}>{product.data.is_active ? c.active : c.inactive}</Badge></dd></div>
              <div><dt>{c.price}</dt><dd>{formatCurrency(product.data.price, locale)}</dd></div>
              <div><dt>{c.stock}</dt><dd>{product.data.stock.toLocaleString(locale)}</dd></div>
              <div className="catalog-detail-wide"><dt>{c.descriptionField}</dt><dd>{product.data.description || '—'}</dd></div>
              <div><dt>{c.created}</dt><dd>{formatDate(product.data.created_at, locale)}</dd></div>
              <div><dt>{c.updated}</dt><dd>{formatDate(product.data.updated_at, locale)}</dd></div>
            </dl>
          </Card>
          {remove.isError && <p role="alert" className="catalog-form-error">{productError(remove.error, locale, 'delete')}</p>}
          <ConfirmDialog open={confirmDelete} title={c.deleteTitle} description={c.deleteDescription} confirmLabel={c.delete} busy={remove.isPending} danger onConfirm={() => remove.mutate()} onClose={() => setConfirmDelete(false)} />
        </>
      )}
    </div>
  )
}
