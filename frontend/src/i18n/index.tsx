import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

export type Locale = 'pt-BR' | 'en'

const messages: Record<Locale, Record<string, string>> = {
  'pt-BR': {
    'app.name': 'OrderFlow',
    'app.subtitle': 'Painel de operações',
    'nav.dashboard': 'Visão geral',
    'nav.customers': 'Clientes',
    'nav.products': 'Produtos',
    'nav.orders': 'Pedidos',
    'nav.payments': 'Pagamentos',
    'nav.refunds': 'Reembolsos',
    'nav.users': 'Usuários',
    'nav.administration': 'Administração',
    'nav.menu': 'Abrir menu',
    'nav.closeMenu': 'Fechar menu',
    'nav.workspace': 'Área de trabalho',
    'auth.login': 'Entrar',
    'auth.signInTitle': 'Bem-vindo ao OrderFlow',
    'auth.signInDescription': 'Entre com sua conta para acompanhar as operações.',
    'auth.email': 'E-mail',
    'auth.password': 'Senha',
    'auth.emailRequired': 'Informe um e-mail válido.',
    'auth.passwordRequired': 'Informe sua senha.',
    'auth.invalidCredentials': 'E-mail ou senha incorretos.',
    'auth.sessionExpired': 'Sua sessão expirou. Entre novamente.',
    'auth.logout': 'Sair',
    'auth.yourAccount': 'Sua conta',
    'auth.secureAccess': 'Acesso seguro à operação',
    'auth.loginBusy': 'Entrando…',
    'role.admin': 'Administrador',
    'role.operator': 'Operador',
    'role.viewer': 'Leitor',
    'common.loading': 'Carregando…',
    'common.retry': 'Tentar novamente',
    'common.cancel': 'Cancelar',
    'common.confirm': 'Confirmar',
    'common.back': 'Voltar',
    'common.page': 'Página {page} de {pages}',
    'common.previous': 'Anterior',
    'common.next': 'Próxima',
    'common.notFound': 'Página não encontrada',
    'common.notFoundDescription': 'O endereço informado não corresponde a uma página disponível.',
    'common.goDashboard': 'Ir para a visão geral',
    'common.forbidden': 'Acesso não autorizado',
    'common.forbiddenDescription': 'Sua conta não tem permissão para acessar esta área.',
    'common.error': 'Não foi possível carregar os dados.',
    'common.empty': 'Nenhum item encontrado.',
    'common.language': 'Idioma',
    'status.pending': 'Pendente',
    'status.processing': 'Em processamento',
    'status.confirmed': 'Confirmado',
    'status.shipped': 'Enviado',
    'status.delivered': 'Entregue',
    'status.cancelled': 'Cancelado',
    'status.paid': 'Pago',
    'status.failed': 'Falhou',
    'status.refunded': 'Reembolsado',
    'status.succeeded': 'Concluído',
  },
  en: {
    'app.name': 'OrderFlow',
    'app.subtitle': 'Operations dashboard',
    'nav.dashboard': 'Overview',
    'nav.customers': 'Customers',
    'nav.products': 'Products',
    'nav.orders': 'Orders',
    'nav.payments': 'Payments',
    'nav.refunds': 'Refunds',
    'nav.users': 'Users',
    'nav.administration': 'Administration',
    'nav.menu': 'Open menu',
    'nav.closeMenu': 'Close menu',
    'nav.workspace': 'Workspace',
    'auth.login': 'Sign in',
    'auth.signInTitle': 'Welcome to OrderFlow',
    'auth.signInDescription': 'Sign in to follow your operations.',
    'auth.email': 'Email',
    'auth.password': 'Password',
    'auth.emailRequired': 'Enter a valid email address.',
    'auth.passwordRequired': 'Enter your password.',
    'auth.invalidCredentials': 'Incorrect email or password.',
    'auth.sessionExpired': 'Your session has expired. Please sign in again.',
    'auth.logout': 'Sign out',
    'auth.yourAccount': 'Your account',
    'auth.secureAccess': 'Secure access to operations',
    'auth.loginBusy': 'Signing in…',
    'role.admin': 'Administrator',
    'role.operator': 'Operator',
    'role.viewer': 'Viewer',
    'common.loading': 'Loading…',
    'common.retry': 'Try again',
    'common.cancel': 'Cancel',
    'common.confirm': 'Confirm',
    'common.back': 'Back',
    'common.page': 'Page {page} of {pages}',
    'common.previous': 'Previous',
    'common.next': 'Next',
    'common.notFound': 'Page not found',
    'common.notFoundDescription': 'This address does not match an available page.',
    'common.goDashboard': 'Go to overview',
    'common.forbidden': 'Access denied',
    'common.forbiddenDescription': 'Your account does not have permission to access this area.',
    'common.error': 'Unable to load data.',
    'common.empty': 'No items found.',
    'common.language': 'Language',
    'status.pending': 'Pending',
    'status.processing': 'Processing',
    'status.confirmed': 'Confirmed',
    'status.shipped': 'Shipped',
    'status.delivered': 'Delivered',
    'status.cancelled': 'Cancelled',
    'status.paid': 'Paid',
    'status.failed': 'Failed',
    'status.refunded': 'Refunded',
    'status.succeeded': 'Succeeded',
  },
}

type I18nContextValue = {
  locale: Locale
  setLocale: (locale: Locale) => void
  t: (key: string, params?: Record<string, string | number>) => string
}

const I18nContext = createContext<I18nContextValue | null>(null)

function readLocale(): Locale {
  try {
    return window.localStorage.getItem('orderflow.locale') === 'en' ? 'en' : 'pt-BR'
  } catch {
    return 'pt-BR'
  }
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocale] = useState<Locale>(readLocale)

  useEffect(() => {
    document.documentElement.lang = locale
    try {
      window.localStorage.setItem('orderflow.locale', locale)
    } catch {
      // Language selection still works when browser storage is disabled.
    }
  }, [locale])

  const value = useMemo<I18nContextValue>(() => ({
    locale,
    setLocale,
    t: (key, params) => {
      const template = messages[locale][key] ?? messages['pt-BR'][key] ?? key
      return template.replace(/\{(\w+)\}/g, (_, name: string) => String(params?.[name] ?? ''))
    },
  }), [locale])

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}

export function useI18n(): I18nContextValue {
  const context = useContext(I18nContext)
  if (!context) throw new Error('useI18n must be used within I18nProvider')
  return context
}

export function formatCurrency(value: number | string, locale: Locale, currency = 'BRL'): string {
  const amount = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(amount)) return '—'
  return new Intl.NumberFormat(locale, { style: 'currency', currency }).format(amount)
}

export function formatDate(value: string | Date, locale: Locale): string {
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

export function formatNumber(value: number, locale: Locale): string {
  return new Intl.NumberFormat(locale).format(value)
}
