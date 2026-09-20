# OrderFlow API

Backend para gerenciamento de pedidos e automações, desenvolvido de forma
incremental com Python, FastAPI, PostgreSQL e SQLAlchemy. Redis e Celery estão
planejados no roadmap, mas ainda não foram implementados.

## Estado do projeto

A Fase 14 adiciona refunds Stripe totais e parciais como entidades financeiras
próprias, com idempotência, concorrência protegida no PostgreSQL e sincronização
por webhooks assinados. A Fase 15 preserva essa baseline e registra a auditoria
de escopo, as pendências reais e o roadmap finito em
[`docs/SCOPE_AUDIT_PHASE_15.md`](docs/SCOPE_AUDIT_PHASE_15.md). O receptor
provider-agnostic da Fase 11, a criação de Payment Intents da Fase 12 e os
webhooks de Payment da Fase 13 permanecem disponíveis.

## Requisitos

- Python 3.12 ou superior
- `pip`
- Docker com Docker Compose

## Ambiente de desenvolvimento

Crie e ative um ambiente virtual e instale as dependências de desenvolvimento:

```bash
python -m venv .venv
python -m pip install --group dev
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

A aplicação executada no host se conecta a
`postgresql+psycopg://orderflow:orderflow_password@localhost:5432/orderflow`.
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

Reverta todas as migrations com `alembic downgrade base`.

## Execução

```bash
uvicorn app.main:app --reload
```

O health check está disponível em `GET /health`. A documentação interativa
gerada pelo FastAPI está disponível em `/docs` e `/redoc`.

## Autenticação

- `POST /api/v1/auth/register`: recebe JSON com `full_name`, `email` e `password`.
- `POST /api/v1/auth/login`: recebe formulário OAuth2 com `username` (e-mail) e
  `password`, e retorna um Bearer access token.
- `GET /api/v1/auth/me`: exige `Authorization: Bearer <token>` e retorna somente
  os dados públicos do usuário atual.

Não há refresh token nesta fase.

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
Idempotency-Key: refund-order-42-attempt-1
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

`POST /api/v1/webhooks/{provider}` continua recebendo o contrato interno
provider-agnostic da Fase 11 sem JWT. Esse receptor genérico não ganha uma
assinatura inventada e não deve ser usado para entregas Stripe.

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
[Stripe Python SDK](https://docs.stripe.com/sdks/python) já fixada no projeto.

## Verificações de qualidade

```bash
ruff check .
ruff format --check .
pytest
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

A documentação completa de instalação, execução, API, Docker e decisões
técnicas será consolidada na fase final.
