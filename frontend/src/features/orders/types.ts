export type OrderStatus = 'pending' | 'processing' | 'confirmed' | 'shipped' | 'delivered' | 'cancelled';

export interface OrderItem {
  id: number;
  product_id: number;
  quantity: number;
  unit_price: string;
}

export interface Order {
  id: number;
  customer_id: number;
  status: OrderStatus;
  total_amount: string;
  items: OrderItem[];
  created_at: string;
  updated_at: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface CustomerSummary {
  id: number;
  name: string;
  email: string;
}

export interface ProductSummary {
  id: number;
  name: string;
  sku: string;
  price: string;
  stock: number;
  is_active: boolean;
}

export const orderTransitions: Record<OrderStatus, OrderStatus[]> = {
  pending: ['processing', 'cancelled'],
  processing: ['confirmed'],
  confirmed: ['shipped'],
  shipped: ['delivered'],
  delivered: [],
  cancelled: [],
};

const statusLabels: Record<OrderStatus, [string, string]> = {
  pending: ['Pendente', 'Pending'],
  processing: ['Em processamento', 'Processing'],
  confirmed: ['Confirmado', 'Confirmed'],
  shipped: ['Enviado', 'Shipped'],
  delivered: ['Entregue', 'Delivered'],
  cancelled: ['Cancelado', 'Cancelled'],
};

export function orderStatusLabel(status: OrderStatus, locale: string): string {
  return statusLabels[status]?.[locale.startsWith('pt') ? 0 : 1] ?? status;
}

export function money(value: string | number, locale: string): string {
  return new Intl.NumberFormat(locale, { style: 'currency', currency: 'BRL' }).format(Number(value));
}

export function dateTime(value: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));
}
