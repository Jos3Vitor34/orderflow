import { Navigate, Outlet, useLocation } from 'react-router'
import { ErrorState, Spinner } from '../../components/common'
import { useI18n } from '../../i18n'
import { useAuth, type UserRole } from './AuthProvider'

export function ProtectedRoute() {
  const { status, sessionExpired, retryAuth } = useAuth()
  const location = useLocation()
  const { t } = useI18n()

  if (status === 'loading') return <div className="route-state"><Spinner label={t('common.loading')} /></div>
  if (status === 'error') return <div className="route-state"><ErrorState message={t('common.error')} onRetry={retryAuth} /></div>
  if (status === 'anonymous') {
    return <Navigate to="/login" state={{ from: `${location.pathname}${location.search}`, expired: sessionExpired }} replace />
  }
  return <Outlet />
}

export function RoleProtectedRoute({ roles }: { roles: readonly UserRole[] }) {
  const { user } = useAuth()
  const { t } = useI18n()
  if (!user || !roles.includes(user.role)) {
    return <div className="route-state"><ErrorState title={t('common.forbidden')} message={t('common.forbiddenDescription')} /></div>
  }
  return <Outlet />
}
