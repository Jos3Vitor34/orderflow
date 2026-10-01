import { api } from '../../api/client'

export interface Product {
  id: number
  sku: string
  name: string
  description: string | null
  price: string
  stock: number
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface ProductList {
  items: Product[]
  total: number
  page: number
  page_size: number
  pages: number
}

export interface ProductPayload {
  sku: string
  name: string
  description: string | null
  price: string
  stock: number
  is_active: boolean
}

export const productKeys = {
  all: ['products'] as const,
  list: (page: number) => ['products', 'list', page] as const,
  detail: (id: number) => ['products', 'detail', id] as const,
}

export async function listProducts(page: number): Promise<ProductList> {
  return (await api.get<ProductList>('/products', { params: { page, page_size: 20 } })).data
}

export async function getProduct(id: number): Promise<Product> {
  return (await api.get<Product>(`/products/${id}`)).data
}

export async function createProduct(data: ProductPayload): Promise<Product> {
  return (await api.post<Product>('/products', data)).data
}

export async function updateProduct(id: number, data: ProductPayload): Promise<Product> {
  return (await api.patch<Product>(`/products/${id}`, data)).data
}

export async function deleteProduct(id: number): Promise<void> {
  await api.delete(`/products/${id}`)
}
