# OrderFlow — desenvolvimento e referência técnica

Este guia reúne a configuração do ambiente, os contratos da API e os detalhes
operacionais do projeto. Para conhecer a aplicação e executar a demonstração
completa, consulte o [README](../README.md). Para o painel com Vite, use o
[guia do frontend](../frontend/README.md); para a stack de produção local,
secrets, HTTPS opcional, backup e rollback, siga o [runbook](DEPLOYMENT.md).

O projeto é apresentado publicamente no GitHub e executado localmente. Não
mantém ambiente público permanente, por decisão de evitar custos recorrentes.

- [Ambiente e banco](#ambiente-de-desenvolvimento)
- [Autenticação e RBAC](#autenticação-usuários-e-autorização)
- [Logs e correlation ID](#logs-estruturados-e-correlation-id)
- [Health e readiness](#liveness-e-readiness)
- [Estatísticas](#estatísticas)
- [Clientes](#customers), [produtos](#products) e [pedidos](#orders)
- [Pagamentos e refunds](#payments)
- [Webhooks](#webhooks)
- [Verificações de qualidade](#verificações-de-qualidade)
- [Redis, Celery e Docker](#redis-celery-e-stack-docker)
- [Segurança](#segurança-e-manutenção) e [dados de demonstração](#dados-de-demonstração-e-limites)

## Ambiente de desenvolvimento

Crie e ative um ambiente virtual e instale as dependências de desenvolvimento:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade "pip>=26.2,<27"
.\.venv\Scripts\python.exe -m pip install --group dev .
```

Copie `.env.example` para `.env` e substitua `JWT_SECRET_KEY` por um segredo
aleatório e exclusivo de pelo menos 32 caracteres. O arquivo `.env` não deve ser
versionado. O algoritmo e a expiração do access token também podem ser definidos
por `JWT_ALGORITHM` e `ACCESS_TOKEN_EXPIRE_MINUTES`.

Para criar Payment Intents, configure ainda uma chave secreta da Stripe de Test
Mode em `STRIPE_SECRET_KEY`. Chaves live são recusadas. `STRIPE_CURRENCY` tem
valor fixo/default `brl` nesta fase. Nunca versione `.env` ou qualquer chave
secreta da Stripe.

Para receber webhooks Stripe, configure separadamente
`STRIPE_WEBHOOK_SECRET=whsec_...`. O endpoint recusa eventos quando esse valor
não está configurado ou não tem o formato de signing secret. Esse segredo não é
a API key: ele pertence ao endpoint configurado no Dashboard/Workbench ou ao
listener local da Stripe CLI.

## PostgreSQL

Inicie somente o banco de dados:

```bash
docker compose up -d --wait postgres
```

A aplicação executada no host usa o `DATABASE_URL` de `.env.example`, apontando
para o PostgreSQL do Compose em `localhost:5432`.
Para encerrar o contêiner preservando os dados, execute:

```bash
docker compose down
```

O volume pode ser removido explicitamente com `docker compose down --volumes`.

## Migrações

Com o PostgreSQL em execução, aplique todas as migrations pendentes:

```bash
alembic upgrade head
```

Consulte a revisão atual ou valide a sincronização com a metadata:

```bash
alembic current
alembic check
```

Use downgrade somente em banco descartável: `alembic downgrade base` é
destrutivo para os dados das tabelas removidas.

Em upgrades de instalações anteriores ao RBAC, a migration
`b6f42d1c8a90_add_user_roles` atribui `admin` a **todos** os usuários existentes
para preservar acesso às operações antigas. Em banco vazio, não promove ninguém:
crie o primeiro administrador pelo comando abaixo. Depois de migrar um ambiente
existente, revise imediatamente `SELECT id, email, role, is_active FROM users
WHERE role = 'admin' ORDER BY id` e reduza privilégios pela API de administração
conforme a função real de cada pessoa. Não execute downgrade em dados reais apenas
para revisar roles; a migration aplicada permanece intacta.

## Execução

```powershell
.\.venv\Scripts\python.exe -m app.server
```

Para reload local, `uvicorn app.main:app --reload` continua possível. O launcher
`app.server` desabilita o access log duplicado do Uvicorn e usa a configuração
estruturada da aplicação. Swagger e ReDoc ficam em `/docs` e `/redoc`.

## Autenticação, usuários e autorização

Não existe cadastro público. `POST /api/v1/auth/login` recebe formulário OAuth2
com `username` (e-mail) e `password`; `GET /api/v1/auth/me` retorna somente os
dados públicos do usuário autenticado. Senhas usam Argon2 pela configuração
recomendada do `pwdlib` e jamais são retornadas. O JWT assinado contém apenas
`sub`, `role`, `type`, `iat` e `exp`; algoritmo e duração são definidos por
`JWT_ALGORITHM` e `ACCESS_TOKEN_EXPIRE_MINUTES` (30 minutos por padrão). A role
do token deve coincidir com a role atual persistida, portanto uma alteração de
papel invalida tokens antigos. Não há refresh token nesta fase.

Crie o primeiro administrador após `alembic upgrade head`. O prompt não ecoa a
senha:

```powershell
.\.venv\Scripts\python.exe -m app.cli bootstrap-admin `
  --email "<valid-admin-email>" --full-name "Local Admin"
```

No Docker:

```bash
docker compose run --rm api python -m app.cli bootstrap-admin \
  --email '<valid-admin-email>' --full-name "Local Admin"
```

Em automação não interativa, defina temporariamente
`ORDERFLOW_ADMIN_PASSWORD`; a variável fica vazia no `.env.example`, não deve
ser persistida e nunca é registrada. E-mail duplicado é recusado.

```powershell
$env:ORDERFLOW_ADMIN_PASSWORD = "replace-this-temporary-password"
.\.venv\Scripts\python.exe -m app.cli bootstrap-admin `
  --email "<valid-admin-email>" --full-name "Local Admin"
Remove-Item Env:ORDERFLOW_ADMIN_PASSWORD
```

Depois do bootstrap, somente `ADMIN` pode usar:

- `POST /api/v1/users`;
- `GET /api/v1/users` e `GET /api/v1/users/{id}`;
- `PATCH /api/v1/users/{id}/role`;
- `PATCH /api/v1/users/{id}/active`.

Não há exclusão física. O serviço bloqueia os administradores ativos antes de
uma desativação/rebaixamento e impede que o último `ADMIN` ativo seja removido.

Exemplo de login e uso do token (valores fictícios):

```bash
curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode 'username=<valid-admin-email>' \
  --data-urlencode 'password=replace-this-local-password'

curl -s http://localhost:8000/api/v1/orders \
  -H 'Authorization: Bearer <access-token>'
```

### Matriz de permissões

| Operação | VIEWER | OPERATOR | ADMIN |
| --- | :---: | :---: | :---: |
| Consultar Customers, Products, Orders, Payments, Refunds e eventos | Sim | Sim | Sim |
| Consultar estatísticas | Sim | Sim | Sim |
| Criar/alterar recursos operacionais e estados | Não | Sim | Sim |
| Excluir Customers ou Products | Não | Não | Sim |
| Administrar usuários | Não | Não | Sim |
| Login, liveness e readiness | Público | Público | Público |
| Webhook Stripe | `Stripe-Signature` | `Stripe-Signature` | `Stripe-Signature` |

O receptor genérico legado exige token de `OPERATOR` ou `ADMIN`, pois eventos
reconhecidos podem alterar Payments. Ele não substitui o endpoint Stripe assinado.
Uma ausência ou falha de autenticação retorna `401` com `WWW-Authenticate: Bearer`; um usuário
válido sem a role necessária recebe `403`.

## Logs estruturados e correlation ID

API e worker escrevem um evento JSON por linha, configurado por `LOG_LEVEL`,
`LOG_FORMAT`, `SERVICE_NAME` e `ENVIRONMENT`. Em JSON, os campos incluem
`timestamp` UTC, `level`, `logger`, `message`, serviço, ambiente e, quando
aplicável, `correlation_id`, método, path sem query string, status, duração,
task ID, task name, tentativa e tipo/traceback sanitizado da exceção.

Chaves e conteúdo sensível são redigidos defensivamente: Authorization/JWT,
cookies, senha/hash, segredos, assinaturas/API keys Stripe e credenciais em URLs.
Respostas `500` não expõem traceback. `LOG_FORMAT=text` existe apenas para
desenvolvimento; JSON é o padrão.

O middleware aceita `X-Correlation-ID` somente com até
`CORRELATION_ID_MAX_LENGTH` (128 por padrão) e caracteres alfanuméricos,
`.`, `_`, `:`, `-`. Valor ausente ou inválido é substituído por UUID e toda
resposta, inclusive erro tratável, recebe o header. O contexto usa
`contextvars` e é limpo ao final. Publicações Celery levam o mesmo ID em headers,
sem alterar os argumentos de negócio; tasks manuais ganham um UUID próprio e
retries preservam o ID original.

## Liveness e readiness

- `GET /health` preserva o contrato histórico `{"status":"ok"}`;
- `GET /health/live` apenas confirma que o processo HTTP responde;
- `GET /health/ready` executa `SELECT 1` no PostgreSQL e `PING` no Redis.

Readiness usa `READINESS_TIMEOUT_SECONDS` (2 segundos por padrão), fecha sessão e
cliente, retorna `200` quando ambos estão disponíveis e `503` com estados seguros
por componente em caso contrário. Não consulta Stripe nem inspeciona workers.
O healthcheck do container usa liveness para evitar reinício em cascata durante
uma falha transitória de dependência; readiness é o sinal correto para retirar a
instância do tráfego. Readiness não garante entrega transacional das tasks.

## Estatísticas

`GET /api/v1/statistics/overview` aceita todas as roles autenticadas. Os query
params opcionais `start` e `end` exigem timezone, são normalizados para UTC e
formam o intervalo semiaberto `[start, end)`. Todas as métricas usam a mesma
janela; sem filtros, representam todo o histórico.

As queries usam `COUNT`, `SUM`, `AVG` e `GROUP BY` no PostgreSQL, sem carregar
entidades. O retorno contém Orders totais/por status, valor total, ticket médio,
criados na janela e visão operacional; Payments totais/por status, volume bruto
processado, volume bruto bem-sucedido, quantidade bem-sucedida e taxa percentual
de sucesso. `approved`, `partially_refunded` e `refunded` contam como tentativas
bem-sucedidas; o denominador exclui `pending` e inclui bem-sucedidas + `failed`.
O volume é bruto e não representa receita líquida após refunds. Valores usam
`Decimal`, nunca `float`, e status ausentes retornam zero.

## Customers

Todos os endpoints exigem um Bearer access token válido. Customers são recursos
globais porque o domínio atual não define ownership entre `User` e `Customer`.

- `POST /api/v1/customers`: cria um Customer.
- `GET /api/v1/customers`: lista com `page` e `page_size` (máximo 100).
- `GET /api/v1/customers/{customer_id}`: consulta um Customer.
- `PATCH /api/v1/customers/{customer_id}`: atualiza campos parcialmente.
- `DELETE /api/v1/customers/{customer_id}`: exclui um Customer sem relações que
  impeçam a operação.

## Products

Todos os endpoints exigem um Bearer access token válido. Products são recursos
globais porque o domínio atual não define ownership entre `User` e `Product`.
Preços usam valores decimais e SKU mantém a capitalização fornecida.

- `POST /api/v1/products`: cria um Product.
- `GET /api/v1/products`: lista com `page` e `page_size` (máximo 100).
- `GET /api/v1/products/{product_id}`: consulta um Product.
- `PATCH /api/v1/products/{product_id}`: atualiza campos parcialmente.
- `DELETE /api/v1/products/{product_id}`: exclui um Product sem relações que
  impeçam a operação.

## Orders

Orders são globais e exigem Bearer access token válido. O servidor obtém o preço
atual de cada Product, grava-o como snapshot no OrderItem e calcula o total usando
`Decimal`. A criação não altera estoque.

- `POST /api/v1/orders`: cria atomicamente um Order e seus OrderItems.
- `GET /api/v1/orders`: lista com `page` e `page_size` (máximo 100).
- `GET /api/v1/orders/{order_id}`: retorna o Order com seus itens.
- `PATCH /api/v1/orders/{order_id}`: aplica uma transição da máquina de estados.

O ciclo permitido é `pending -> processing -> confirmed -> shipped -> delivered`;
o único cancelamento permitido é `pending -> cancelled`. Repetir o estado atual é
idempotente e retorna o recurso sem novo efeito. Saltos, regressões, mudanças de
estados terminais, estoque insuficiente e produtos inativos durante a confirmação
retornam `409 Conflict`.

O estoque é debitado exclusivamente em `processing -> confirmed`, usando a
quantidade persistida em cada OrderItem. A confirmação bloqueia primeiro o Order e
depois todos os Products com `SELECT ... FOR UPDATE`; Products são bloqueados em
ordem crescente de ID. Disponibilidade, débitos e mudança para `confirmed` usam um
único commit, portanto uma falha reverte o pedido e todos os estoques. As demais
transições, inclusive `pending -> cancelled`, não movimentam nem repõem estoque.
Payments, Refunds e webhooks financeiros permanecem separados desse ciclo e não
alteram Order ou estoque.

Exemplo de criação:

```json
{
  "customer_id": 1,
  "items": [
    {"product_id": 10, "quantity": 2},
    {"product_id": 20, "quantity": 1}
  ]
}
```

Preço, total e status inicial não são aceitos do cliente. Não existe endpoint de
exclusão ou CRUD independente de OrderItem nesta fase.

Limitações deliberadas: não há cancelamento depois de `pending`, reposição por
Refund/Payment, reserva antecipada em `processing` nem exigência de pagamento
aprovado para confirmar.

## Payments

Payments são registros financeiros globais e todos os endpoints exigem Bearer
access token válido. O servidor copia `Order.total_amount` para `Payment.amount`
como snapshot `Decimal`; o cliente não pode enviar ou alterar o valor. Pedidos
cancelados ou com total não positivo não recebem novos pagamentos.

- `POST /api/v1/payments`: cria um Payment inicialmente `pending`, recebendo
  `order_id`, `provider` e o `provider_reference` opcional.
- `GET /api/v1/payments`: lista com `page` e `page_size` (máximo 100).
- `GET /api/v1/payments/{payment_id}`: consulta um Payment.
- `PATCH /api/v1/payments/{payment_id}`: altera somente `status`.

Quando informado, `provider_reference` funciona como chave idempotente global.
Uma repetição com o mesmo pedido e provider retorna o registro existente; o uso
da referência com dados estruturais diferentes gera conflito. As transições são
`pending` para `approved` ou `failed`, `approved` para `partially_refunded` ou
`refunded`, e `partially_refunded` para `refunded`; repetir o status atual não
produz efeito. Estados financeiros não regressam. Payment não altera status do
Order nem estoque. Não existe endpoint de exclusão de Payment.

### Stripe Payment Intents (Test Mode)

`POST /api/v1/payments/stripe` cria um Payment Intent pela SDK oficial e, em
seguida, persiste um Payment interno com `provider="stripe"`, a referência
`pi_...`, o total do Order e status `pending`. O endpoint exige Bearer JWT e o
header `Idempotency-Key`; o corpo aceita somente o Order:

```http
POST /api/v1/payments/stripe
Authorization: Bearer <token>
Idempotency-Key: checkout-attempt-123
Content-Type: application/json

{"order_id": 123}
```

O servidor deriva `amount` exclusivamente de `Order.total_amount`, converte-o
com `Decimal` para centavos e usa a moeda configurada no servidor. O cliente não
pode enviar valor, moeda, provider, status ou referência externa. A resposta
contém apenas o Payment interno e os campos seguros `payment_intent_id`, `status`,
`amount` e `currency` do provider; `client_secret` não é exposto nem persistido.

A mesma chave deve ser reutilizada ao repetir a mesma tentativa lógica. Uma
chave diferente representa uma nova tentativa e é compatível com a relação
Order 1:N Payments. A chave enviada à Stripe é namespaced e inclui um hash da
chave do cliente. Stripe é chamado antes do PostgreSQL e a unique constraint de
`provider_reference` converge requisições concorrentes para o mesmo Payment.
Assim, uma falha de persistência pode ser recuperada repetindo a requisição com
a mesma chave, sem manter lock de banco durante a chamada externa.

Como o schema atual não armazena a chave da operação, a recuperação de uma
falha ocorrida depois da criação externa depende da janela de retenção da chave
idempotente pela Stripe. Garantia local permanente exigiria uma futura mudança
de modelo; nenhuma migration foi introduzida nesta fase.

Criar um Payment Intent não aprova o Payment. O status interno permanece
`pending` até um webhook Stripe autenticado informar sucesso ou falha. O backend
não confirma o Intent, não recebe dados de cartão e não implementa Stripe
Elements ou Checkout.

### Refunds Stripe (Test Mode)

Refunds exigem Bearer JWT, pertencem obrigatoriamente a um Payment e possuem
valor, moeda, provider, referência `re_...`, timestamps e estado próprios. A API
expõe somente campos seguros:

- `POST /api/v1/payments/{payment_id}/refunds`: cria um refund total ou parcial;
- `GET /api/v1/payments/{payment_id}/refunds`: lista com `page` e `page_size`;
- `GET /api/v1/refunds/{refund_id}`: consulta um refund.

A criação exige `Idempotency-Key`. O corpo aceita `amount` opcional e `reason`
opcional (`duplicate`, `fraudulent` ou `requested_by_customer`). Omitir `amount`
usa todo o saldo ainda reembolsável; a moeda vem do Payment e nunca é escolhida
pelo cliente:

```http
POST /api/v1/payments/42/refunds
Authorization: Bearer <token>
Idempotency-Key: <unique-request-key>
Content-Type: application/json

{"amount": "25.50", "reason": "requested_by_customer"}
```

Valores são `Decimal` com duas casas e convertidos exatamente para minor units.
Somente Payments Stripe aprovados ou parcialmente reembolsados, com referência
`pi_...`, podem receber novos refunds. A soma das operações ativas (`pending`,
`requires_action` ou `succeeded`) reserva o saldo contra concorrência, mas apenas
refunds `succeeded` contam como dinheiro devolvido. Uma soma bem-sucedida menor
que o valor original deixa o Payment `partially_refunded`; a soma exata deixa-o
`refunded`. `Payment.amount`, Order e estoque nunca são alterados.

A chave recebida é armazenada somente como SHA-256 e combinada ao Payment por
uma unique constraint. Um fingerprint canônico protege contra reutilização da
mesma chave com outro payload. A chave encaminhada à Stripe é determinística,
namespaced e limitada; a metadata contém apenas os IDs internos de Refund e
Payment para correlação.

O servidor faz uma transação curta para bloquear o Payment e reservar o saldo,
fecha o lock antes da chamada externa e, depois da resposta, abre outra transação
para sincronizar Refund e Payment. Erros definitivos da Stripe liberam a reserva;
erros transitórios ou resultado incerto a preservam para uma repetição segura
com a mesma chave. Se a Stripe aceitar e o PostgreSQL falhar, a idempotência da
Stripe evita uma segunda operação e o retry ou webhook reconcilia o registro.
Não há chamada real à Stripe nos testes automatizados.

## Webhooks

`POST /api/v1/webhooks/{provider}` recebe o contrato interno legado somente com
Bearer JWT de `OPERATOR` ou `ADMIN`. A rota é para simulação ou integrações
internas confiáveis: não valida assinatura própria de cada provider. A mudança
intencional remove o acesso público anterior, que permitia alterar Payments sem
autenticidade. O provider `stripe` é reservado à rota oficial assinada.

O envelope interno é:

```json
{
  "provider_event_id": "evt_123",
  "event_type": "payment.approved",
  "payload": {
    "provider_reference": "pay_123",
    "metadata": {"attempt": 1}
  }
}
```

`provider_event_id` identifica globalmente o evento recebido e garante sua
idempotência. `provider_reference` identifica o Payment no provider; são conceitos
distintos. Reentrega idêntica retorna o evento existente, enquanto reutilização do
ID com provider, tipo ou payload diferente retorna conflito.

Os tipos internos reconhecidos são `payment.approved`, `payment.failed` e
`payment.refunded`. Eles reutilizam as transições de Payment, validam também o
provider e nunca alteram amount, Order ou estoque. Eventos desconhecidos são
persistidos sem efeito e permanecem com `processed_at` nulo. Eventos reconhecidos
rejeitados também permanecem auditáveis e não são reprocessados automaticamente.

Para auditoria autenticada existem `GET /api/v1/webhook-events` e
`GET /api/v1/webhook-events/{webhook_event_id}`. Não existem operações de PATCH,
DELETE, retry ou replay nesta fase.

### Webhook oficial Stripe (Test Mode)

`POST /api/v1/webhooks/stripe` é uma rota estática declarada antes de
`/webhooks/{provider}`. Ela não exige Bearer JWT: autentica a origem pelo header
`Stripe-Signature`, pelo `STRIPE_WEBHOOK_SECRET` e por
`stripe.Webhook.construct_event`. A verificação usa os bytes exatos retornados
por `await request.body()`, antes de qualquer parsing ou reserialização. O SDK
aplica também sua tolerância padrão de timestamp contra replay.

Esta fase usa snapshot events dos recursos Stripe API v1/PaymentIntent. Depois
da verificação, o JSON autenticado é armazenado em `WebhookEvent`, com
`provider="stripe"`, `event.id` como `provider_event_id` globalmente único e o
`event.type` original. Headers, API keys e signing secrets nunca são persistidos.

Estes eventos de Payment Intent alteram Payment:

- `payment_intent.succeeded`: `pending` para `approved`;
- `payment_intent.payment_failed`: `pending` para `failed`.

O Payment é localizado exclusivamente por `PaymentIntent.id` em
`provider_reference`; metadata não substitui essa ligação. Antes da transição,
o servidor bloqueia o Payment com `SELECT ... FOR UPDATE` e valida provider
`stripe`, amount convertido exatamente para minor units e currency `brl`. A
máquina de estados central de Payment continua decidindo transições repetidas ou
inválidas. O evento, a alteração de status e `processed_at` são commitados na
mesma transação.

Os eventos `refund.created`, `refund.updated` e `refund.failed` sincronizam a
mesma entidade Refund por `refund.id`. O Payment é localizado por
`refund.payment_intent`; provider, amount e currency são validados, e metadata é
apenas uma ajuda de correlação. Um refund legítimo criado no Dashboard pode ser
materializado localmente mesmo sem uma requisição anterior no OrderFlow.

Cada sincronização bloqueia Payment e Refund, aplica a máquina central de estados
do Refund e considera o timestamp do evento para recusar regressões e eventos
antigos. Em seguida recalcula o agregado de todos os refunds `succeeded` e grava
Refund, Payment, WebhookEvent e `processed_at` atomicamente. Assim,
`refund.updated` pode chegar antes de `refund.created`, eventos diferentes não
duplicam o recurso e estados terminais não voltam a `pending`. Refunds `failed`
ou `canceled` preservam os valores já devolvidos por operações anteriores.

Eventos autenticados desconhecidos, Payments ausentes, provider/amount/currency
divergentes e transições fora de ordem são auditados com `processed_at` nulo e
recebem acknowledgement `200`, evitando retries infinitos para situações não
recuperáveis automaticamente. Reentregas do mesmo `event.id` também recebem
`200`; a unique constraint e o tratamento de `IntegrityError` garantem um evento
e um efeito lógico mesmo sob concorrência. Falhas de banco/configuração recebem
`503`, enquanto assinatura/header/evento inválido recebe `400` sem persistência.

`charge.refunded` permanece apenas auditável, sem efeito financeiro, para não
duplicar o efeito já representado pelos eventos do objeto Refund. Outros eventos
autenticados desconhecidos também são apenas auditados. O webhook não altera
Order, estoque nem `Payment.amount`.

Para teste manual opcional, inicie a API e use a Stripe CLI:

```bash
stripe listen --events payment_intent.succeeded,payment_intent.payment_failed,refund.created,refund.updated,refund.failed,charge.refunded --forward-to localhost:8000/api/v1/webhooks/stripe
stripe trigger payment_intent.succeeded
```

Copie temporariamente o signing secret `whsec_...` mostrado por `stripe listen`
para `STRIPE_WEBHOOK_SECRET`. O secret do listener CLI pode ser diferente daquele
do endpoint criado no Dashboard/Workbench. A CLI, internet e uma conta Stripe
não são necessárias para `pytest`; os testes locais geram assinaturas pelo SDK.

A implementação segue a documentação oficial de
[refunds](https://docs.stripe.com/refunds),
[criação de Refund](https://docs.stripe.com/api/refunds/create),
[objeto Refund](https://docs.stripe.com/api/refunds/object),
[idempotência](https://docs.stripe.com/api/idempotent_requests),
[webhooks](https://docs.stripe.com/webhooks),
[verificação de assinatura](https://docs.stripe.com/webhooks/signature),
[testes locais](https://docs.stripe.com/webhooks/test) e
[tipos de evento](https://docs.stripe.com/api/events/types), usando a
[Stripe Python SDK](https://docs.stripe.com/sdks/python) declarada no projeto.

## Verificações de qualidade

```bash
ruff check .
ruff format --check .
mypy
pytest -m 'not integration'
python -m pip_audit --local
```

Com o PostgreSQL do Compose em execução, a integração real é validada com:

PowerShell:

```powershell
$env:RUN_POSTGRES_INTEGRATION_TESTS = "1"
pytest -m integration
```

Bash:

```bash
RUN_POSTGRES_INTEGRATION_TESTS=1 pytest -m integration
```

Para medir statements e branches em toda a suíte, com PostgreSQL disponível:

```bash
RUN_POSTGRES_INTEGRATION_TESTS=1 pytest --cov=app --cov-branch \
  --cov-report=term-missing:skip-covered --cov-report=xml \
  --cov-report=html --cov-fail-under=90
```

`coverage.xml` e `htmlcov/` são artefatos ignorados pelo Git. Migrations Alembic
ficam fora da medição do pacote `app`; a integração as valida separadamente. O
launcher `app/server.py` é validado pelo smoke test da stack e não entra na
cobertura unitária. O
limite de 90% combina testes locais e PostgreSQL, pois os repositories dependem do banco
real. O repositório usa `pip` e grupos de dependências em `pyproject.toml`, sem
lockfile; os intervalos de versão permitem atualização compatível e a auditoria
verifica o ambiente resolvido. Atualizações do Dependabot precisam passar pela CI
e por revisão antes de merge.

A CI executa esses gates em Linux e Python 3.12. A integração usa PostgreSQL
real e Redis; o worker é exercitado em testes locais no modo eager e no smoke
test da stack.

## Redis, Celery e stack Docker

A stack de desenvolvimento completa possui seis serviços. `frontend` serve o
painel React pelo Nginx; `postgres` mantém os
dados do domínio, `redis` atua como broker e result backend, `migrate` aplica o
Alembic uma única vez, `api` serve o FastAPI e `worker` executa as tasks Celery.
API e worker usam a mesma imagem e executam como usuário não-root. A ordem de
startup é PostgreSQL saudável, migrations concluídas e, então, API e worker; o
worker também aguarda o Redis ficar saudável.

O projeto usa `celery[redis]>=5.6,<6` e declara também `redis>=6.4,<7`
diretamente, pois a própria API importa o cliente para readiness. As URLs e a
observabilidade são centralizadas nas seguintes variáveis:

```dotenv
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1
CELERY_TASK_ALWAYS_EAGER=false
CELERY_TASK_EAGER_PROPAGATES=false
CELERY_LOG_LEVEL=INFO
LOG_LEVEL=INFO
LOG_FORMAT=json
SERVICE_NAME=orderflow-api
ENVIRONMENT=development
CORRELATION_ID_MAX_LENGTH=128
READINESS_TIMEOUT_SECONDS=2
```

No Compose, essas URLs usam o hostname `redis`, nunca `localhost`. O banco lógico
0 transporta mensagens e o banco lógico 1 guarda resultados por até uma hora.
O Redis local é deliberadamente efêmero, sem volume, RDB ou AOF: reiniciá-lo pode
perder mensagens enfileiradas e resultados. O volume nomeado `postgres_data`
continua persistente.

### Tasks disponíveis

- `orderflow.tasks.send_order_confirmation`: recarrega um Order confirmado (ou
  já avançado), registra a confirmação mockada e ignora o resultado.
- `orderflow.tasks.send_payment_notification`: recarrega um Payment fora de
  `pending`, registra a notificação mockada e ignora o resultado.
- `orderflow.tasks.generate_order_report`: retorna contagens JSON-safe dos
  Orders por estado, sem criar arquivo e sem alterar o banco.

As mensagens recebem somente IDs inteiros. Cada execução cria e fecha sua própria
sessão SQLAlchemy, consulta o estado persistido atual e não altera Order, Payment,
Refund, estoque ou WebhookEvent. As tasks são idempotentes porque seus únicos
efeitos são leituras e logs. `acks_late` é seguro nesse conjunto específico e
permite reentrega; por isso a semântica normal é de pelo menos uma vez e logs
duplicados são possíveis. Uma notificação externa futura exigirá outbox e chave
idempotente persistente.

Somente `sqlalchemy.exc.OperationalError` recebe retry: três retries além da
tentativa inicial, backoff exponencial, teto de 60 segundos e jitter. Entidade
ausente, estado incompatível, payload inválido e erros de programação não são
repetidos. Não há `sleep`, retry infinito ou captura genérica por `Exception`.

A confirmação de Order é publicada após a primeira transição efetiva
`processing -> confirmed`. A notificação de Payment é publicada após uma mudança
efetiva pelo fluxo `PATCH /payments/{id}`. Repetições idempotentes e rollbacks não
publicam. Os webhooks Stripe preservam sua política de acknowledgement e não
foram acoplados ao broker nesta fase.

Existe uma janela inevitável entre o commit PostgreSQL e a publicação no Redis.
Se o broker falhar nessa janela, o estado do domínio permanece commitado, a falha
é registrada e a resposta não finge que ocorreu rollback. Sem transactional
outbox, a notificação pode ser perdida; esta é uma limitação deliberada da fase.

### Execução local

Copie `.env.example` para `.env`, substitua `JWT_SECRET_KEY` por um valor local
único com pelo menos 32 caracteres e mantenha as URLs com `localhost`. Em seguida:

```powershell
docker compose up -d postgres redis
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000
celery -A app.celery_app:celery_app worker --loglevel=INFO --pool=solo
```

`--pool=solo` é indicado apenas para desenvolvimento no host Windows. O worker
do container roda em Linux com o pool padrão do Celery.

### Stack completa

O comando principal faz build, executa migrations e sobe todos os serviços:

```bash
docker compose up -d --build
docker compose ps
```

No Compose de desenvolvimento, frontend, PostgreSQL, Redis e API são publicados
apenas em `127.0.0.1` por padrão;
containers conversam pela rede interna do Compose. A imagem executa como usuário
`orderflow` sem privilégios de root e tem healthcheck HTTP de liveness.

A API fica em `http://localhost:8000`, com liveness público em `/health/live`,
readiness em `/health/ready` e OpenAPI em `/openapi.json`. Diagnóstico:

```bash
docker compose logs api
docker compose logs worker
docker compose logs redis
docker compose logs migrate
docker compose exec worker celery -A app.celery_app:celery_app inspect ping
docker compose exec worker celery -A app.celery_app:celery_app inspect registered
```

Para publicar e recuperar um relatório real:

```bash
docker compose exec api python -c "from app.tasks import generate_order_report; result = generate_order_report.delay(); print(result.id); print(result.get(timeout=15))"
```

Para testar uma notificação com um ID que já exista no banco:

```bash
docker compose exec api celery -A app.celery_app:celery_app call orderflow.tasks.send_order_confirmation --args='[1]'
docker compose logs worker
```

Migrations também podem ser executadas explicitamente com
`docker compose run --rm migrate`. Para encerrar sem apagar dados, use
`docker compose down` ou `docker compose stop`; ambos preservam
`postgres_data`. `docker compose down -v` apaga permanentemente os volumes e
deve ser usado somente quando a destruição dos dados for intencional.

Os testes comuns usam broker `memory://`, backend `cache+memory://`, eager mode e
propagação de exceptions; portanto, não exigem Redis nem worker. A integração
PostgreSQL continua opcional com `RUN_POSTGRES_INTEGRATION_TESTS=1`. Nenhum teste
ou smoke test desta fase faz chamada real à Stripe.

Falhas comuns: `401` indica token ausente/inválido/inativo ou role do token
desatualizada; `403` indica role insuficiente; `503` em readiness identifica o
componente indisponível sem revelar DSN; falha de bootstrap por duplicidade
significa que o e-mail já existe. Confira `docker compose logs api worker
migrate`, sem copiar tokens ou segredos para tickets.

Limitações deliberadas: não há refresh token, recuperação de senha, OAuth
social, rate limit distribuído, Prometheus/Grafana/Loki/ELK/OpenTelemetry,
providers reais para notificações ou outbox transacional. Logs JSON não
substituem uma plataforma completa de observabilidade. Continua existindo a
janela entre commit do domínio e publicação no broker; sem outbox, uma task pode
não ser publicada embora o commit tenha sido concluído. Não há deploy público.

## Segurança e manutenção

Os workflows de qualidade verificam o frontend (tipos, lint, testes e build),
Ruff, mypy, testes, PostgreSQL, migrations, cobertura de branches e os manifests
Compose. O job de containers constrói as duas imagens e valida health, SPA,
proxy, JWT, roles, Celery, persistência e indisponibilidade da API. O workflow
de segurança audita dependências Python instaladas e dependências npm do frontend,
varre árvore e histórico Git com Gitleaks, faz build e scan HIGH/CRITICAL das imagens
backend e frontend com Trivy e gera duas SBOMs CycloneDX como artefatos da CI.
Nenhum workflow publica
imagem ou faz deploy. As execuções remotas podem ser acompanhadas na aba Actions
do repositório.

O webhook Stripe público exige `Stripe-Signature`; o receptor legado interno
exige role operacional. Chaves da Stripe aceitas pela aplicação devem ser de Test
Mode. `.env` é ignorado pelo Git e pelo contexto Docker. O Compose fornece senhas
apenas para desenvolvimento local; substitua todos os defaults em qualquer outro
ambiente. Consulte [SECURITY.md](../SECURITY.md) para reportar vulnerabilidades e
[a revisão de segurança da Fase 19](SECURITY_REVIEW_PHASE_19.md) para as
exceções temporárias do scan da imagem.

## Dados de demonstração e limites

Um smoke test manual pode criar registros persistentes no volume `postgres_data`.
Testes automatizados de integração usam schemas descartáveis próprios; registros
do smoke test são dados de demonstração e não devem ser confundidos com eles.
Use identificadores novos, por exemplo um sufixo UUID em e-mails e SKUs, em cada
execução manual. Para limpar demonstrações, faça backup, localize os IDs exatos em
consultas `SELECT` e revise referências de Order, Payment, Refund e WebhookEvent;
remova apenas os IDs aprovados em uma transação explícita. `docker compose down`
preserva o volume. Nunca use `down -v` para limpar alguns registros.

Além das limitações acima, não há refresh token, rate limiting distribuído,
outbox transacional ou notificações reais. O projeto não mantém ambiente público
permanente por decisão de escopo; a stack de produção é executada localmente.
A entrega Celery é ao menos uma vez; no Compose de desenvolvimento o Redis é
efêmero e pode perder mensagens/resultados quando reiniciado. A autenticação
de providers genéricos não usa assinatura própria:
somente operadores autenticados podem enviar seus eventos.

## Contribuição e referências

Outbox transacional, notificações reais e observabilidade distribuída estão
fora do escopo atual. Nenhuma dessas funções é necessária para executar
o portfólio local. Mantido por José Vitor; contribuições pequenas seguem
[CONTRIBUTING.md](../CONTRIBUTING.md). Distribuído sob a
[licença MIT](../LICENSE).

A configuração segue a documentação oficial de
[tasks do Celery](https://docs.celeryq.dev/en/stable/userguide/tasks.html),
[configuração do Celery](https://docs.celeryq.dev/en/stable/userguide/configuration.html),
[Celery com Redis](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html),
[Redis em Docker](https://redis.io/docs/latest/develop/setup/),
[Docker Compose](https://docs.docker.com/compose/),
[ordem de startup](https://docs.docker.com/compose/how-tos/startup-order/) e
[serviços do Compose](https://docs.docker.com/reference/compose-file/services/).
