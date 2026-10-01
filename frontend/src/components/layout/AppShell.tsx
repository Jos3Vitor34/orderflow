import { Suspense, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router'
import { Button, Spinner } from '../common'
import { useAuth, useCan } from '../../features/auth/AuthProvider'
import { useI18n } from '../../i18n'

type NavItem = { to: string; label: string; icon: 'dashboard' | 'customers' | 'products' | 'orders' | 'payments' | 'users' }

export function AppShell() {
  const { user, logout } = useAuth()
  const canAdmin = useCan('admin')
  const { t, locale, setLocale } = useI18n()
  const navigate = useNavigate()
  const location = useLocation()
  const [menuOpen, setMenuOpen] = useState(false)

  const primaryNav: NavItem[] = [
    { to: '/dashboard', label: t('nav.dashboard'), icon: 'dashboard' },
    { to: '/customers', label: t('nav.customers'), icon: 'customers' },
    { to: '/products', label: t('nav.products'), icon: 'products' },
    { to: '/orders', label: t('nav.orders'), icon: 'orders' },
    { to: '/payments', label: t('nav.payments'), icon: 'payments' },
  ]
  const pageLabel = location.pathname.startsWith('/admin') ? t('nav.users')
    : location.pathname.startsWith('/customers') ? t('nav.customers')
      : location.pathname.startsWith('/products') ? t('nav.products')
        : location.pathname.startsWith('/orders') ? t('nav.orders')
          : location.pathname.startsWith('/payments') ? t('nav.payments')
            : location.pathname.startsWith('/refunds') ? t('nav.refunds') : t('nav.dashboard')

  const handleLogout = () => {
    logout()
    navigate('/login', { replace: true })
  }

  return <div className="app-shell min-h-screen flex">
    {menuOpen && <button className="sidebar-scrim" aria-label={t('nav.closeMenu')} onClick={() => setMenuOpen(false)} />}
    <aside className={`sidebar ${menuOpen ? 'sidebar-open' : ''}`} aria-label={t('nav.workspace')}>
      <div className="sidebar-brand">
        <span className="brand-mark" aria-hidden="true">OF</span>
        <div><strong>OrderFlow</strong><small>{t('app.subtitle')}</small></div>
        <button className="sidebar-close" aria-label={t('nav.closeMenu')} onClick={() => setMenuOpen(false)}>×</button>
      </div>
      <nav className="sidebar-nav" aria-label={t('nav.workspace')}>
        <div className="sidebar-group-label">{t('nav.workspace')}</div>
        {primaryNav.map((item) => <NavLink key={item.to} to={item.to} className={({ isActive }) => `sidebar-link ${isActive ? 'sidebar-link-active' : ''}`} onClick={() => setMenuOpen(false)}>
          <NavIcon name={item.icon} /><span>{item.label}</span>
        </NavLink>)}
        {canAdmin && <>
          <div className="sidebar-group-label sidebar-group-label-spaced">{t('nav.administration')}</div>
          <NavLink to="/admin/users" className={({ isActive }) => `sidebar-link ${isActive ? 'sidebar-link-active' : ''}`} onClick={() => setMenuOpen(false)}>
            <NavIcon name="users" /><span>{t('nav.users')}</span>
          </NavLink>
        </>}
      </nav>
      <div className="sidebar-footer">
        <span className="sidebar-avatar" aria-hidden="true">{initials(user?.full_name ?? '')}</span>
        <div className="sidebar-user"><strong>{user?.full_name}</strong><small>{user?.email}</small></div>
      </div>
    </aside>
    <div className="app-main min-w-0 flex-1 flex flex-col">
      <header className="topbar flex items-center justify-between">
        <div className="topbar-left flex items-center min-w-0">
          <button className="mobile-menu-button" aria-label={t('nav.menu')} onClick={() => setMenuOpen(true)}>
            <span /><span /><span />
          </button>
          <div className="topbar-crumb"><span>{t('nav.workspace')}</span><span className="crumb-separator">/</span><strong>{pageLabel}</strong></div>
        </div>
        <div className="topbar-actions flex items-center min-w-0">
          <label className="language-control">
            <span className="sr-only">{t('common.language')}</span>
            <select value={locale} onChange={(event) => setLocale(event.target.value as 'pt-BR' | 'en')}>
              <option value="pt-BR">PT-BR</option><option value="en">EN</option>
            </select>
          </label>
          <div className="topbar-user" title={user?.email}>
            <span className="topbar-user-name">{user?.full_name}</span>
            <span className="topbar-role">{t(`role.${user?.role ?? 'viewer'}`)}</span>
          </div>
          <Button variant="ghost" onClick={handleLogout} className="logout-button">{t('auth.logout')}</Button>
        </div>
      </header>
      <main id="main-content" className="content flex-1 w-full mx-auto">
        <Suspense fallback={<div className="route-content-loading"><Spinner label={t('common.loading')} /></div>}><Outlet /></Suspense>
      </main>
    </div>
  </div>
}

function initials(name: string): string {
  return name.trim().split(/\s+/).slice(0, 2).map((part) => part[0]?.toUpperCase() ?? '').join('') || 'OF'
}

function NavIcon({ name }: { name: NavItem['icon'] }) {
  const paths: Record<NavItem['icon'], string> = {
    dashboard: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
    customers: 'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75',
    products: 'M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16V8ZM3.3 7 12 12l8.7-5M12 22V12',
    orders: 'M8 3h8l3 3v15H5V3h3ZM8 10h8M8 14h8M8 18h5',
    payments: 'M3 6h18v12H3zM3 10h18M7 15h3',
    users: 'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM17 8h6M20 5v6',
  }
  return <svg className="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>
}
