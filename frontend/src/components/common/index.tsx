import { useEffect, useRef, type ComponentPropsWithRef, type ReactNode } from 'react'
import { useI18n } from '../../i18n'

type ButtonProps = ComponentPropsWithRef<'button'> & {
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost'
  loading?: boolean
}

export function Button({ variant = 'primary', loading = false, className = '', children, disabled, ref, ...props }: ButtonProps) {
  return <button ref={ref} className={`btn btn-${variant} inline-flex items-center justify-center gap-2 ${className}`.trim()} disabled={disabled || loading} {...props}>
    {loading && <span className="button-spinner" aria-hidden="true" />}
    {children}
  </button>
}

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <section className={`panel min-w-0 ${className}`.trim()}>{children}</section>
}

export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'success' | 'warning' | 'danger' | 'info' }) {
  return <span className={`badge badge-${tone}`}>{children}</span>
}

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: ReactNode }) {
  return <header className="page-header flex items-end justify-between gap-4">
    <div>
      <h1>{title}</h1>
      {description && <p className="muted">{description}</p>}
    </div>
    {actions && <div className="page-header-actions">{actions}</div>}
  </header>
}

export function Spinner({ label }: { label?: string }) {
  const { t } = useI18n()
  return <div className="spinner-line flex items-center justify-center gap-3" role="status"><span className="spinner" aria-hidden="true" />{label ?? t('common.loading')}</div>
}

export function EmptyState({ title, description }: { title: string; description?: string }) {
  return <div className="empty flex flex-col items-center justify-center text-center" role="status">
    <span className="empty-icon" aria-hidden="true">○</span>
    <strong>{title}</strong>
    {description && <p>{description}</p>}
  </div>
}

export function ErrorState({ title, message, onRetry }: { title?: string; message: string; onRetry?: () => void }) {
  const { t } = useI18n()
  return <div className="empty error-state flex flex-col items-center justify-center text-center" role="alert">
    <span className="empty-icon" aria-hidden="true">!</span>
    <strong>{title ?? t('common.error')}</strong>
    <p>{message}</p>
    {onRetry && <Button variant="secondary" onClick={onRetry}>{t('common.retry')}</Button>}
  </div>
}

export function Pagination({ page, pages, onPageChange }: { page: number; pages: number; onPageChange: (page: number) => void }) {
  const { t } = useI18n()
  const safePages = Math.max(1, pages)
  return <nav className="pagination flex items-center justify-between gap-3" aria-label={t('common.page', { page, pages: safePages })}>
    <Button variant="secondary" type="button" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>{t('common.previous')}</Button>
    <span>{t('common.page', { page, pages: safePages })}</span>
    <Button variant="secondary" type="button" disabled={page >= safePages} onClick={() => onPageChange(page + 1)}>{t('common.next')}</Button>
  </nav>
}

type ConfirmDialogProps = {
  open: boolean
  title: string
  description: string
  confirmLabel?: string
  busy?: boolean
  danger?: boolean
  onConfirm: () => void
  onClose: () => void
}

export function ConfirmDialog({ open, title, description, confirmLabel, busy = false, danger = false, onConfirm, onClose }: ConfirmDialogProps) {
  const { t } = useI18n()
  const cancelRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    cancelRef.current?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      previous?.focus()
    }
  }, [open, busy, onClose])

  if (!open) return null
  return <div className="modal-backdrop grid place-items-center" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose() }}>
    <div className="modal" role="dialog" aria-modal="true" aria-labelledby="confirm-title" aria-describedby="confirm-description">
      <h2 id="confirm-title">{title}</h2>
      <p id="confirm-description">{description}</p>
      <div className="actions modal-actions">
        <Button ref={cancelRef} variant="secondary" type="button" disabled={busy} onClick={onClose}>{t('common.cancel')}</Button>
        <Button variant={danger ? 'danger' : 'primary'} type="button" loading={busy} onClick={onConfirm}>{confirmLabel ?? t('common.confirm')}</Button>
      </div>
    </div>
  </div>
}
