import { lazy } from 'react'
import { Link, Navigate, Route, Routes } from 'react-router'
import { AppShell } from './components/layout/AppShell'
import { ErrorState } from './components/common'
import { LoginPage } from './features/auth/LoginPage'
import { ProtectedRoute, RoleProtectedRoute } from './features/auth/ProtectedRoute'
import { useI18n } from './i18n'

const DashboardPage = lazy(async () => ({ default: (await import('./features/dashboard/DashboardPage')).DashboardPage }))
const CustomersPage = lazy(async () => ({ default: (await import('./features/customers/CustomersPage')).CustomersPage }))
const CustomerDetailPage = lazy(async () => ({ default: (await import('./features/customers/CustomersPage')).CustomerDetailPage }))
const ProductsPage = lazy(async () => ({ default: (await import('./features/products/ProductsPage')).ProductsPage }))
const ProductDetailPage = lazy(async () => ({ default: (await import('./features/products/ProductsPage')).ProductDetailPage }))
const OrdersPage = lazy(async () => ({ default: (await import('./features/orders')).OrdersPage }))
const OrderDetailPage = lazy(async () => ({ default: (await import('./features/orders')).OrderDetailPage }))
const PaymentsPage = lazy(async () => ({ default: (await import('./features/payments')).PaymentsPage }))
const PaymentDetailPage = lazy(async () => ({ default: (await import('./features/payments')).PaymentDetailPage }))
const RefundDetailPage = lazy(async () => ({ default: (await import('./features/refunds')).RefundDetailPage }))
const UsersPage = lazy(async () => ({ default: (await import('./features/users')).UsersPage }))
const UserDetailPage = lazy(async () => ({ default: (await import('./features/users')).UserDetailPage }))

export function App() {
  const { t } = useI18n()
  return <Routes>
    <Route path="/login" element={<LoginPage />} />
    <Route element={<ProtectedRoute />}>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/customers" element={<CustomersPage />} />
        <Route path="/customers/:customerId" element={<CustomerDetailPage />} />
        <Route path="/products" element={<ProductsPage />} />
        <Route path="/products/:productId" element={<ProductDetailPage />} />
        <Route path="/orders" element={<OrdersPage />} />
        <Route path="/orders/:orderId" element={<OrderDetailPage />} />
        <Route path="/payments" element={<PaymentsPage />} />
        <Route path="/payments/:paymentId" element={<PaymentDetailPage />} />
        <Route path="/refunds/:refundId" element={<RefundDetailPage />} />
        <Route element={<RoleProtectedRoute roles={['admin']} />}>
          <Route path="/admin/users" element={<UsersPage />} />
          <Route path="/admin/users/:userId" element={<UserDetailPage />} />
        </Route>
        <Route path="*" element={<div className="page"><ErrorState title={t('common.notFound')} message={t('common.notFoundDescription')} /><Link className="back-link" to="/dashboard">{t('common.goDashboard')}</Link></div>} />
      </Route>
    </Route>
  </Routes>
}
