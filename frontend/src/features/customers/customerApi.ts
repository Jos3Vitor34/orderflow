import { api } from '../../api/client'

export interface Customer {
  id: number
  name: string
  email: string
  phone: string | null
  created_at: string
  updated_at: string
}

export interface CustomerList {
  items: Customer[]
  total: number
  page: number
  page_size: number
  pages: number
}

export interface CustomerPayload {
  name: string
  email: string
  phone: string | null
}

export const customerKeys = {
  all: ['customers'] as const,
  list: (page: number) => ['customers', 'list', page] as const,
  detail: (id: number) => ['customers', 'detail', id] as const,
}

export async function listCustomers(page: number): Promise<CustomerList> {
  return (await api.get<CustomerList>('/customers', { params: { page, page_size: 20 } })).data
}

export async function getCustomer(id: number): Promise<Customer> {
  return (await api.get<Customer>(`/customers/${id}`)).data
}

export async function createCustomer(data: CustomerPayload): Promise<Customer> {
  return (await api.post<Customer>('/customers', data)).data
}

export async function updateCustomer(id: number, data: CustomerPayload): Promise<Customer> {
  return (await api.patch<Customer>(`/customers/${id}`, data)).data
}

export async function deleteCustomer(id: number): Promise<void> {
  await api.delete(`/customers/${id}`)
}
