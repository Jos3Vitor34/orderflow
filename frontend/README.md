# OrderFlow frontend

Painel em React e TypeScript para a API FastAPI do repositório. O backend permanece
na raiz; este diretório contém somente o aplicativo executado no navegador.

## Requisitos e execução

- Node.js 24 e npm (a versão principal também está em `.nvmrc`)
- Backend OrderFlow em `http://localhost:8000`
- PostgreSQL, Redis, migrações e um usuário administrador inicializados conforme o
  [README principal](../README.md)

Na raiz do repositório, inicie a API e suas dependências com
`docker compose up -d --build --wait`. Crie o primeiro administrador com o comando
de bootstrap documentado no README principal. Em outro terminal:

```powershell
Set-Location frontend
Copy-Item .env.example .env
npm ci
npm run dev
```

Abra `http://localhost:5173`. O frontend roda com Vite no host e usa a API do
Compose em `http://localhost:8000`. Para executar o backend diretamente no host,
use os passos de desenvolvimento do README principal.

## Configuração

`VITE_API_BASE_URL` indica a origem da API. O cliente acrescenta `/api/v1` aos
endpoints e aceita também uma URL que já termine em `/api/v1`. O valor de exemplo
é `http://localhost:8000`. Reinicie o Vite após alterar `.env`.

O backend permite por padrão `http://localhost:5173` e
`http://127.0.0.1:5173`. Para outra origem, configure `CORS_ORIGINS` no `.env`
da raiz com URLs exatas separadas por vírgulas. Não coloque segredos em variáveis
`VITE_*`: seus valores entram no bundle público. `frontend/.env` fica fora do Git;
`frontend/.env.example` é versionado.

## Rotas e permissões

| Rota | Conteúdo | Acesso |
| --- | --- | --- |
| `/login` | Login com e-mail e senha | Público |
| `/dashboard` | Estatísticas reais de pedidos e pagamentos | Viewer, operator, admin |
| `/customers`, `/customers/:customerId` | Clientes | Consulta: todos; criar/editar: operator/admin; excluir: admin |
| `/products`, `/products/:productId` | Produtos | Consulta: todos; criar/editar: operator/admin; excluir: admin |
| `/orders`, `/orders/:orderId` | Pedidos e transições de status | Consulta: todos; criar/alterar: operator/admin |
| `/payments`, `/payments/:paymentId` | Pagamentos, Stripe Test Mode e refunds do pagamento | Consulta: todos; criar/alterar/solicitar refund: operator/admin |
| `/refunds/:refundId` | Detalhes do refund | Viewer, operator, admin |
| `/admin/users`, `/admin/users/:userId` | Usuários, roles e ativação | Admin |

Os controles escondem operações sem permissão e o backend valida cada requisição.
O dashboard usa `/api/v1/statistics/overview`. Listas paginadas usam `page` e
`page_size` da API. Pedidos expõem somente as transições permitidas pelo serviço
backend. A tela de detalhes busca separadamente nomes de clientes e produtos.
Ela oferece acesso à lista paginada de pagamentos; a API não possui filtro de
pagamentos por pedido. Para exibir todos os pagamentos vinculados diretamente
no detalhe do pedido, seria necessário um filtro `order_id` ou um endpoint de
pagamentos do pedido no backend.

## Sessão, idioma e pagamentos

O login envia um formulário OAuth2 para `POST /api/v1/auth/login`; o campo
`username` contém o e-mail. O JWT é guardado em `sessionStorage`, enviado como
`Authorization: Bearer` e validado com `GET /api/v1/auth/me`. Respostas `401`
limpam a sessão e levam de volta ao login. O backend não oferece refresh token,
cadastro público ou recuperação de senha. A preferência de idioma fica no
`localStorage` e inicia em português do Brasil; inglês pode ser escolhido no
painel.

O fluxo Stripe cria um PaymentIntent com `POST /api/v1/payments/stripe` em Test
Mode. A API retorna ID, status, valor e moeda, sem `client_secret`; portanto, o
painel acompanha o registro e os webhooks, mas não oferece formulário de cartão
ou confirmação com Stripe Elements. A criação de PaymentIntent e refund envia
`Idempotency-Key`; ao repetir a mesma tentativa após falha temporária, a mesma
chave é reutilizada. Refunds só podem ser solicitados para pagamentos Stripe
elegíveis, após confirmação explícita. O backend decide o saldo e a elegibilidade
final.

## Código e qualidade

`src/api` centraliza HTTP e cache; `src/features` separa as telas por domínio;
`src/components` contém elementos visuais compartilhados; `src/i18n` contém as
mensagens e a formatação de datas/números. Formulários usam React Hook Form e
Zod; dados do servidor usam TanStack Query. O TypeScript está em modo estrito.

```bash
npm run typecheck
npm run lint
npm run test
npm run build
```

Os testes usam Vitest e React Testing Library. `npm run build` gera `dist/`,
que não é versionado. A CI executa os quatro comandos após `npm ci`.
