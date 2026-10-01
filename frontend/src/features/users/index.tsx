import { useState } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { useForm } from 'react-hook-form';
import { Link, useNavigate, useParams } from 'react-router';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import axios from 'axios';
import { z } from 'zod';
import { api, getApiErrorMessage } from '../../api/client';
import { useAuth, type UserRole } from '../auth/AuthProvider';
import { useI18n } from '../../i18n';
import { Badge, Button, Card, ConfirmDialog, EmptyState, ErrorState, PageHeader, Pagination, Spinner } from '../../components/common';
import { dateTime, type Page } from '../orders/types';

interface ManagedUser {
  id: number;
  full_name: string;
  email: string;
  role: UserRole;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

const PAGE_SIZE = 20;
const createSchema = z.object({
  full_name: z.string().trim().min(1).max(120),
  email: z.string().email().max(320),
  password: z.string().min(8).max(128),
  role: z.enum(['viewer', 'operator', 'admin']),
});
type CreateInput = z.infer<typeof createSchema>;

const copy = (locale: string) => locale.startsWith('pt') ? {
  title: 'Usuários', description: 'Gerencie acesso, papéis e ativação.', new: 'Novo usuário',
  id: 'Usuário', name: 'Nome completo', email: 'E-mail', role: 'Papel', active: 'Ativo',
  inactive: 'Inativo', created: 'Criado em', details: 'Detalhes', empty: 'Nenhum usuário encontrado.',
  viewer: 'Leitor', operator: 'Operador', admin: 'Administrador',
  password: 'Senha inicial', create: 'Criar usuário', cancel: 'Cancelar',
  invalidName: 'Informe um nome de até 120 caracteres.', invalidEmail: 'Informe um e-mail válido.',
  invalidPassword: 'A senha deve ter entre 8 e 128 caracteres.',
  detailTitle: 'Detalhe do usuário', back: 'Voltar aos usuários',
  updateRole: 'Alterar papel', saveRole: 'Salvar papel', roleSaved: 'Papel atualizado.',
  updateActive: 'Acesso', deactivate: 'Desativar usuário', activate: 'Ativar usuário', activeSaved: 'Acesso atualizado.',
  confirmDeactivate: 'Confirmar desativação', confirmDeactivateBody: 'Este usuário perderá o acesso imediatamente.',
  lastAdmin: 'É necessário manter ao menos um administrador ativo.',
  accessDenied: 'Apenas administradores podem acessar usuários.',
} : {
  title: 'Users', description: 'Manage access, roles, and activation.', new: 'New user',
  id: 'User', name: 'Full name', email: 'Email', role: 'Role', active: 'Active',
  inactive: 'Inactive', created: 'Created', details: 'Details', empty: 'No users found.',
  viewer: 'Viewer', operator: 'Operator', admin: 'Administrator',
  password: 'Initial password', create: 'Create user', cancel: 'Cancel',
  invalidName: 'Enter a name up to 120 characters.', invalidEmail: 'Enter a valid email.',
  invalidPassword: 'Password must be 8 to 128 characters.',
  detailTitle: 'User details', back: 'Back to users',
  updateRole: 'Change role', saveRole: 'Save role', roleSaved: 'Role updated.',
  updateActive: 'Access', deactivate: 'Deactivate user', activate: 'Activate user', activeSaved: 'Access updated.',
  confirmDeactivate: 'Confirm deactivation', confirmDeactivateBody: 'This user will immediately lose access.',
  lastAdmin: 'At least one active administrator must remain.',
  accessDenied: 'Only administrators can access users.',
};

function userMutationError(error: unknown, locale: string): string {
  if (
    axios.isAxiosError<{ detail?: string }>(error)
    && error.response?.status === 409
    && error.response.data?.detail === 'At least one active administrator must remain'
  ) return copy(locale).lastAdmin;
  return getApiErrorMessage(error, locale === 'en' ? 'en' : 'pt-BR');
}

function RoleBadge({ role, locale }: { role: UserRole; locale: string }) {
  const c = copy(locale);
  return <Badge tone={role === 'admin' ? 'info' : role === 'operator' ? 'success' : 'neutral'}>{c[role]}</Badge>;
}

function CreateUser({ onClose }: { onClose: () => void }) {
  const { locale } = useI18n();
  const c = copy(locale);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const form = useForm<CreateInput>({ resolver: zodResolver(createSchema), defaultValues: { full_name: '', email: '', password: '', role: 'viewer' } });
  const mutation = useMutation({
    mutationFn: async (value: CreateInput) => (await api.post<ManagedUser>('/users', value)).data,
    onSuccess: async (created) => {
      form.reset();
      await queryClient.invalidateQueries({ queryKey: ['users'] });
      navigate(`/admin/users/${created.id}`);
    },
  });
  return <Card><h2>{c.new}</h2><form onSubmit={form.handleSubmit((value) => mutation.mutate(value))} noValidate>
    <div className="form-grid">
      <label className="field">{c.name}<input autoComplete="name" {...form.register('full_name')} aria-invalid={Boolean(form.formState.errors.full_name)} />{form.formState.errors.full_name && <span className="error">{c.invalidName}</span>}</label>
      <label className="field">{c.email}<input type="email" autoComplete="email" {...form.register('email')} aria-invalid={Boolean(form.formState.errors.email)} />{form.formState.errors.email && <span className="error">{c.invalidEmail}</span>}</label>
      <label className="field">{c.password}<input type="password" autoComplete="new-password" {...form.register('password')} aria-invalid={Boolean(form.formState.errors.password)} />{form.formState.errors.password && <span className="error">{c.invalidPassword}</span>}</label>
      <label className="field">{c.role}<select {...form.register('role')}><option value="viewer">{c.viewer}</option><option value="operator">{c.operator}</option><option value="admin">{c.admin}</option></select></label>
    </div>
    <div className="actions"><Button type="submit" loading={mutation.isPending}>{c.create}</Button><Button type="button" variant="ghost" onClick={onClose}>{c.cancel}</Button></div>
    {mutation.isError && <p className="error" role="alert">{getApiErrorMessage(mutation.error, locale)}</p>}
  </form></Card>;
}

export function UsersPage() {
  const { locale } = useI18n();
  const { user } = useAuth();
  const c = copy(locale);
  const isAdmin = user?.role === 'admin';
  const [page, setPage] = useState(1);
  const [showCreate, setShowCreate] = useState(false);
  const query = useQuery({
    queryKey: ['users', 'list', page],
    queryFn: async () => (await api.get<Page<ManagedUser>>('/users', { params: { page, page_size: PAGE_SIZE } })).data,
    enabled: isAdmin,
  });
  if (!isAdmin) return <main className="page"><ErrorState message={c.accessDenied} /></main>;
  return <main className="page">
    <PageHeader title={c.title} description={c.description} actions={<Button onClick={() => setShowCreate((current) => !current)}>{c.new}</Button>} />
    {showCreate && <CreateUser onClose={() => setShowCreate(false)} />}
    <Card>{query.isPending ? <Spinner label={c.title} /> : query.isError ? <ErrorState message={getApiErrorMessage(query.error, locale)} onRetry={() => void query.refetch()} /> : query.data.items.length === 0 ? <EmptyState title={c.empty} /> : <>
      <div style={{ overflowX: 'auto' }}><table className="data-table"><thead><tr><th scope="col">{c.id}</th><th scope="col">{c.name}</th><th scope="col">{c.email}</th><th scope="col">{c.role}</th><th scope="col">{c.active}</th><th scope="col">{c.created}</th><th scope="col"><span className="sr-only">{c.details}</span></th></tr></thead>
        <tbody>{query.data.items.map((entry) => <tr key={entry.id}><td>#{entry.id}</td><td>{entry.full_name}</td><td>{entry.email}</td><td><RoleBadge role={entry.role} locale={locale} /></td><td><Badge tone={entry.is_active ? 'success' : 'neutral'}>{entry.is_active ? c.active : c.inactive}</Badge></td><td>{dateTime(entry.created_at, locale)}</td><td><Link to={`/admin/users/${entry.id}`}>{c.details}</Link></td></tr>)}</tbody>
      </table></div><Pagination page={page} pages={query.data.pages} onPageChange={setPage} />
    </>}</Card>
  </main>;
}

export function UserDetailPage() {
  const { userId } = useParams();
  const id = Number(userId);
  const { locale } = useI18n();
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const c = copy(locale);
  const queryClient = useQueryClient();
  const [newRole, setNewRole] = useState<UserRole | ''>('');
  const [feedback, setFeedback] = useState('');
  const [confirmDeactivate, setConfirmDeactivate] = useState(false);
  const query = useQuery({
    queryKey: ['users', id],
    queryFn: async () => (await api.get<ManagedUser>(`/users/${id}`)).data,
    enabled: isAdmin && Number.isInteger(id) && id > 0,
  });
  const entry = query.data;
  const roleMutation = useMutation({
    mutationFn: async (role: UserRole) => (await api.patch<ManagedUser>(`/users/${id}/role`, { role })).data,
    onSuccess: async () => { setFeedback(c.roleSaved); setNewRole(''); await queryClient.invalidateQueries({ queryKey: ['users'] }); },
  });
  const activeMutation = useMutation({
    mutationFn: async (is_active: boolean) => (await api.patch<ManagedUser>(`/users/${id}/active`, { is_active })).data,
    onSuccess: async () => { setConfirmDeactivate(false); setFeedback(c.activeSaved); await queryClient.invalidateQueries({ queryKey: ['users'] }); },
  });
  if (!isAdmin) return <main className="page"><ErrorState message={c.accessDenied} /></main>;
  if (!Number.isInteger(id) || id <= 0) return <main className="page"><ErrorState message={c.detailTitle} /></main>;
  return <main className="page">
    <PageHeader title={`${c.detailTitle} #${id}`} actions={<Link to="/admin/users">{c.back}</Link>} />
    {query.isPending ? <Spinner label={c.detailTitle} /> : query.isError ? <ErrorState message={getApiErrorMessage(query.error, locale)} onRetry={() => void query.refetch()} /> : entry && <>
      <Card><dl className="form-grid">
        <div><dt>{c.name}</dt><dd>{entry.full_name}</dd></div>
        <div><dt>{c.email}</dt><dd>{entry.email}</dd></div>
        <div><dt>{c.role}</dt><dd><RoleBadge role={entry.role} locale={locale} /></dd></div>
        <div><dt>{c.active}</dt><dd><Badge tone={entry.is_active ? 'success' : 'neutral'}>{entry.is_active ? c.active : c.inactive}</Badge></dd></div>
        <div><dt>{c.created}</dt><dd>{dateTime(entry.created_at, locale)}</dd></div>
      </dl></Card>
      <Card><h2>{c.updateRole}</h2><div className="actions"><label className="field">{c.role}<select value={newRole} onChange={(event) => setNewRole(event.target.value as UserRole)}><option value="">—</option><option value="viewer">{c.viewer}</option><option value="operator">{c.operator}</option><option value="admin">{c.admin}</option></select></label><Button disabled={!newRole || newRole === entry.role} loading={roleMutation.isPending} onClick={() => { if (newRole) roleMutation.mutate(newRole); }}>{c.saveRole}</Button></div>
        {roleMutation.isError && <p className="error" role="alert">{userMutationError(roleMutation.error, locale)}</p>}
      </Card>
      <Card><h2>{c.updateActive}</h2><Button variant={entry.is_active ? 'danger' : 'primary'} loading={activeMutation.isPending} onClick={() => { if (entry.is_active) setConfirmDeactivate(true); else activeMutation.mutate(true); }}>{entry.is_active ? c.deactivate : c.activate}</Button>
        {activeMutation.isError && <p className="error" role="alert">{userMutationError(activeMutation.error, locale)}</p>}
        {feedback && <p className="notice" role="status">{feedback}</p>}
        <ConfirmDialog open={confirmDeactivate} title={c.confirmDeactivate} description={`${entry.full_name}: ${c.confirmDeactivateBody}`} confirmLabel={c.deactivate} busy={activeMutation.isPending} danger onConfirm={() => activeMutation.mutate(false)} onClose={() => { if (!activeMutation.isPending) setConfirmDeactivate(false); }} />
      </Card>
    </>}
  </main>;
}
