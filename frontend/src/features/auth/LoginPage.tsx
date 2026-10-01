import { zodResolver } from '@hookform/resolvers/zod'
import { useMemo, useState } from 'react'
import { useForm } from 'react-hook-form'
import { Navigate, useLocation, useNavigate } from 'react-router'
import { z } from 'zod'
import { getApiErrorMessage } from '../../api/client'
import { Button } from '../../components/common'
import { useI18n } from '../../i18n'
import { useAuth } from './AuthProvider'

type LoginFields = { email: string; password: string }

type LoginLocationState = { from?: string; expired?: boolean }

export function LoginPage() {
  const { t, locale, setLocale } = useI18n()
  const { login, isAuthenticated } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const state = location.state as LoginLocationState | null
  const [requestError, setRequestError] = useState<string | null>(null)
  const schema = useMemo(() => z.object({
    email: z.email({ error: t('auth.emailRequired') }),
    password: z.string().min(1, t('auth.passwordRequired')),
  }), [t])
  const { register, handleSubmit, formState: { errors, isSubmitting } } = useForm<LoginFields>({
    resolver: zodResolver(schema),
    defaultValues: { email: '', password: '' },
  })

  if (isAuthenticated) return <Navigate to={state?.from || '/dashboard'} replace />

  const submit = async (values: LoginFields) => {
    setRequestError(null)
    try {
      await login(values.email, values.password)
      navigate(state?.from || '/dashboard', { replace: true })
    } catch (error) {
      const status = typeof error === 'object' && error !== null && 'response' in error
        ? (error.response as { status?: number } | undefined)?.status
        : undefined
      setRequestError(status === 401 ? t('auth.invalidCredentials') : getApiErrorMessage(error, locale))
    }
  }

  return <div className="login-page min-h-screen bg-white">
    <div className="login-aside" aria-hidden="true">
      <div className="brand-mark brand-mark-large">OF</div>
      <div className="login-aside-copy">
        <span className="eyebrow">ORDERFLOW / OPERATIONS</span>
        <h2>{t('auth.secureAccess')}</h2>
        <p>{t('app.subtitle')}</p>
      </div>
      <div className="login-aside-lines" />
    </div>
    <main className="login-main flex flex-col min-w-0">
      <div className="login-topbar flex items-center justify-end">
        <span className="login-mobile-brand"><span className="brand-mark">OF</span> OrderFlow</span>
        <label className="language-control">
          <span className="sr-only">{t('common.language')}</span>
          <select value={locale} onChange={(event) => setLocale(event.target.value as 'pt-BR' | 'en')}>
            <option value="pt-BR">PT-BR</option><option value="en">EN</option>
          </select>
        </label>
      </div>
      <div className="login-form-wrap">
        <div className="login-kicker">ORDERFLOW</div>
        <h1>{t('auth.signInTitle')}</h1>
        <p className="muted login-description">{t('auth.signInDescription')}</p>
        {state?.expired && <div className="notice notice-warning" role="status">{t('auth.sessionExpired')}</div>}
        {requestError && <div className="notice notice-error" role="alert">{requestError}</div>}
        <form onSubmit={handleSubmit(submit)} noValidate>
          <div className="field">
            <label htmlFor="email">{t('auth.email')}</label>
            <input id="email" type="email" autoComplete="username" aria-invalid={Boolean(errors.email)} aria-describedby={errors.email ? 'email-error' : undefined} {...register('email')} />
            {errors.email && <span id="email-error" className="field-error">{errors.email.message}</span>}
          </div>
          <div className="field">
            <label htmlFor="password">{t('auth.password')}</label>
            <input id="password" type="password" autoComplete="current-password" aria-invalid={Boolean(errors.password)} aria-describedby={errors.password ? 'password-error' : undefined} {...register('password')} />
            {errors.password && <span id="password-error" className="field-error">{errors.password.message}</span>}
          </div>
          <Button type="submit" loading={isSubmitting} className="login-submit">
            {isSubmitting ? t('auth.loginBusy') : t('auth.login')}
          </Button>
        </form>
      </div>
      <div className="login-footer">© {new Date().getFullYear()} OrderFlow</div>
    </main>
  </div>
}
