# Auditoria de Escopo — Fase 15

Data da auditoria: 20 de setembro de 2026.

## 1. Resumo executivo

O OrderFlow está tecnicamente estável na baseline auditada: 331 testes passam,
incluindo PostgreSQL real; Ruff está limpo; o OpenAPI é gerado; e o Alembic está
sincronizado em `9f3c2a1b7d14 (head)`. A raiz Git é exatamente a pasta
`orderflow`, mas o repositório foi inicializado sem qualquer commit ou staging.
Por isso `git ls-files` está vazio e todo o projeto aparece como não rastreado.

O projeto evoluiu além do escopo original na área financeira. Payment Intents,
webhooks oficiais e refunds Stripe foram evoluções aprovadas e bem testadas. O
custo dessa evolução é um desequilíbrio de escopo: estoque transacional, máquina
de estados de Order, Redis/Celery, infraestrutura completa, logging operacional,
readiness, estatísticas, isolamento/autorização de recursos e CI ainda precisam
de atenção antes de declarar o portfólio concluído.

Não foi encontrada justificativa para remover a arquitetura Stripe/Refund. Ela
é explicável em entrevista e demonstra idempotência, concorrência e consistência
financeira. Novas evoluções financeiras devem, contudo, ficar congeladas até a
conclusão dos requisitos centrais. Outbox, reconciliação e replay são opcionais.

## 2. Baseline técnica

| Evidência | Resultado |
|---|---|
| Python | `3.14.3`; o projeto exige `>=3.12` em `pyproject.toml` |
| Testes | `331 passed, 2 warnings in 4.74s` com `RUN_POSTGRES_INTEGRATION_TESTS=1` |
| Ruff | `All checks passed!`; `86 files already formatted` |
| Alembic | `9f3c2a1b7d14 (head)`; um único head |
| Autogenerate | `No new upgrade operations detected` |
| Banco | PostgreSQL 17 Alpine, 9 tabelas públicas, volume preservado |
| OpenAPI | importação e geração aprovadas; 19 paths documentados |
| Stripe | SDK `15.6.1`, Test Mode, sem chamada real nos testes |
| Tipos | type hints presentes; mypy/pyright não configurados |
| Container inicial | PostgreSQL parado; somente esse serviço foi iniciado para validar |

Os dois warnings são conhecidos e não funcionais: uma depreciação interna do
`Starlette TestClient` e a impossibilidade do pytest criar `.pytest_cache` no
diretório sincronizado pelo OneDrive.

## 3. Diagnóstico do Git

Comandos executados antes de qualquer staging:

```text
git rev-parse --is-inside-work-tree       -> true
git rev-parse --show-toplevel             -> C:/Users/josev/OneDrive/Desktop/orderflow
git status --short --branch               -> ## No commits yet on main
git ls-files                              -> saída vazia
git log --oneline --decorate -n 10        -> branch main sem commits
git branch --show-current                 -> main
git remote -v                             -> saída vazia
```

`.git` existe em `orderflow`, foi criado em `2026-09-19T11:35:57-03:00`, não é
bare e aponta `HEAD` para `refs/heads/main`. Não há `.git` em `Desktop`,
`OneDrive` ou no diretório pessoal imediatamente superior. O config local não
contém remote nem identidade sobrescrita. Nome e e-mail estão disponíveis na
configuração Git já existente, sem que esta auditoria os alterasse.

A causa precisa do Git vazio é: o repositório foi inicializado corretamente na
raiz do projeto, mas nunca recebeu `git add` nem o primeiro commit. Não há perda
de histórico observável; simplesmente não existe histórico. Os candidatos são
somente código, testes, migrations, configuração e documentação do OrderFlow.

## 4. Matriz de aderência

As classificações abaixo usam somente os estados definidos no prompt. Recursos
financeiros posteriores aparecem como concluídos nesta matriz e são identificados
separadamente como evoluções aprovadas fora do escopo original.

### Base

| Requisito | Classificação | Evidência |
|---|---|---|
| Python 3.12+ | CONCLUÍDO E VALIDADO | `requires-python = ">=3.12"`; runtime 3.14.3 |
| FastAPI | CONCLUÍDO E VALIDADO | `app/main.py`; importação e OpenAPI aprovados |
| Uvicorn | CONCLUÍDO E VALIDADO | dependência e comando documentado no README |
| Pydantic | CONCLUÍDO E VALIDADO | schemas explícitos em `app/schemas` |
| Pydantic Settings | CONCLUÍDO E VALIDADO | `app/core/config.py` |
| Configuração por ambiente | CONCLUÍDO E VALIDADO | `.env`, tipos e `SecretStr`; `.env.example` |
| Routes/services/repositories | CONCLUÍDO E VALIDADO | camadas utilizadas em todos os domínios |
| Tratamento de erros | PARCIAL | erros sanitizados por rota, sem handler/envelope global |
| OpenAPI | CONCLUÍDO E VALIDADO | 19 paths e OAuth2 gerados sem secrets |

### Banco

| Requisito | Classificação | Evidência |
|---|---|---|
| PostgreSQL | CONCLUÍDO E VALIDADO | Compose e testes reais |
| SQLAlchemy 2.x | CONCLUÍDO E VALIDADO | mappings tipados e queries 2.x |
| Alembic/migrations | CONCLUÍDO E VALIDADO | duas revisões, um head, check limpo |
| Constraints/índices | CONCLUÍDO E VALIDADO | migrations, models e testes de constraints |
| Relacionamentos | CONCLUÍDO E VALIDADO | Customer/Order/Items/Payments/Refunds |
| Transações | CONCLUÍDO E VALIDADO | commits/rollbacks nos repositories |
| Locks | CONCLUÍDO E VALIDADO | `with_for_update` em Payments e webhooks financeiros |
| Concorrência | CONCLUÍDO E VALIDADO | testes PostgreSQL para payments, webhooks e refunds |
| Rollback | CONCLUÍDO E VALIDADO | testes de falha e reutilização de sessão |

### Autenticação e autorização

| Requisito | Classificação | Evidência |
|---|---|---|
| Registro/login/usuário atual | CONCLUÍDO E VALIDADO | `app/api/routes/auth.py` e testes |
| JWT | CONCLUÍDO E VALIDADO | claims obrigatórios, expiração e algoritmo restrito |
| OAuth2 Password Bearer | CONCLUÍDO E VALIDADO | token URL validada no OpenAPI |
| Hash seguro | CONCLUÍDO E VALIDADO | `pwdlib` com Argon2 recomendado |
| Autorização | PARCIAL | autenticação protege rotas, mas não há papéis/ownership |
| Isolamento de dados | AUSENTE | Customers, Products, Orders e Payments são globais |
| Testes de autenticação | CONCLUÍDO E VALIDADO | unitários e PostgreSQL |

### Customers

| Requisito | Classificação | Evidência |
|---|---|---|
| Criação/listagem/consulta | CONCLUÍDO E VALIDADO | rotas e paginação |
| Atualização/exclusão | CONCLUÍDO E VALIDADO | PATCH/DELETE e conflitos relacionais |
| Validações | CONCLUÍDO E VALIDADO | schema, e-mail único e constraints |
| Testes | CONCLUÍDO E VALIDADO | `test_customers*.py` |

### Products

| Requisito | Classificação | Evidência |
|---|---|---|
| CRUD | CONCLUÍDO E VALIDADO | POST/GET/PATCH/DELETE |
| Preço | CONCLUÍDO E VALIDADO | `Decimal`, `NUMERIC(12,2)`, não negativo |
| Estoque cadastral | CONCLUÍDO E VALIDADO | inteiro e check não negativo |
| Status ativo | CONCLUÍDO E VALIDADO | `is_active` e bloqueio no Order |
| Validações/testes | CONCLUÍDO E VALIDADO | schemas, constraints e testes reais |

### Orders

| Requisito | Classificação | Evidência |
|---|---|---|
| Criação | CONCLUÍDO E VALIDADO | criação atômica de Order e itens |
| Order Items | CONCLUÍDO E VALIDADO | snapshot de preço e unicidade produto/order |
| Subtotal e total | PARCIAL | total é calculado; subtotal não é exposto como campo |
| Estados | CONCLUÍDO E VALIDADO | enum com seis estados |
| Transições | AUSENTE | PATCH aceita qualquer salto entre valores do enum |
| Cancelamento | PARCIAL | estado existe, sem regras de transição ou efeito de estoque |
| Concorrência | PARCIAL | integridade relacional existe; não há lock de estoque |
| Testes | PARCIAL | cobrem comportamento atual, não FSM/estoque pretendidos |

### Estoque

| Requisito | Classificação | Evidência |
|---|---|---|
| Disponibilidade | AUSENTE | criação de Order não compara quantidade e estoque |
| Proteção contra negativo | CONCLUÍDO E VALIDADO | schema e check `stock >= 0` |
| Atualização transacional | AUSENTE | Order deliberadamente não altera estoque |
| Locks/concorrência | AUSENTE | Product não é bloqueado na criação/transição de Order |
| Rollback de estoque | AUSENTE | não existe mutação de estoque a reverter |
| Testes PostgreSQL do fluxo | AUSENTE | testes atuais confirmam estoque inalterado |

### Payments

| Requisito | Classificação | Evidência |
|---|---|---|
| Pagamento mock originalmente previsto | CONCLUÍDO E VALIDADO | endpoint genérico e provider configurável |
| Entidade/estados | CONCLUÍDO E VALIDADO | Payment e máquina central de estados |
| Payment Intent Stripe | CONCLUÍDO E VALIDADO | evolução aprovada em Test Mode |
| Idempotência/falhas | CONCLUÍDO E VALIDADO | chave Stripe e convergência local |
| Testes | CONCLUÍDO E VALIDADO | unitários e concorrência PostgreSQL |

### Webhooks

| Requisito | Classificação | Evidência |
|---|---|---|
| Endpoint manual | CONCLUÍDO E VALIDADO | `/webhooks/{provider}` |
| Endpoint Stripe/raw body/assinatura | CONCLUÍDO E VALIDADO | evolução aprovada com SDK oficial |
| Auditoria/idempotência | CONCLUÍDO E VALIDADO | WebhookEvent e ID global único |
| Concorrência/rollback | CONCLUÍDO E VALIDADO | locks e testes PostgreSQL |
| Eventos desconhecidos/política de retry | CONCLUÍDO E VALIDADO | auditados com 200; 503 apenas transitório |
| Testes | CONCLUÍDO E VALIDADO | assinatura SDK, adulteração e falhas |

### Refunds — evolução aprovada fora do escopo original

| Requisito | Classificação | Evidência |
|---|---|---|
| Entidade/migration | CONCLUÍDO E VALIDADO | `Refund` e revisão `9f3c2a1b7d14` |
| Total/parcial/múltiplos | CONCLUÍDO E VALIDADO | agregado persistido e 54 testes da Fase 14 |
| Idempotência/concorrência/reserva | CONCLUÍDO E VALIDADO | hash, fingerprint, locks e constraints |
| Refunds externos | CONCLUÍDO E VALIDADO | criação local por evento autenticado |
| Eventos fora de ordem | CONCLUÍDO E VALIDADO | timestamp e máquina de estados |
| Webhook | CONCLUÍDO E VALIDADO | três eventos do objeto Refund |
| Preservação de Order/estoque | CONCLUÍDO E VALIDADO | invariantes verificadas no PostgreSQL |

### Background jobs

| Requisito | Classificação | Evidência |
|---|---|---|
| Redis | AUSENTE | só existe `REDIS_URL` não consumida no `.env.example` |
| Celery/worker/tasks | AUSENTE | sem dependência, módulos ou serviço Compose |
| Confirmação de pedido | AUSENTE | sem task assíncrona |
| Notificação de pagamento | AUSENTE | sem task ou adapter mock |
| Geração de relatório | AUSENTE | sem task ou endpoint |
| Retry/backoff/limite | AUSENTE | somente retry HTTP/Stripe síncrono |
| Testes de jobs | AUSENTE | nenhum job implementado |

### Infraestrutura

| Requisito | Classificação | Evidência |
|---|---|---|
| Dockerfile | AUSENTE | arquivo não existe |
| Docker Compose | PARCIAL | configuração válida, somente PostgreSQL |
| API containerizada | AUSENTE | sem build/serviço API |
| PostgreSQL | CONCLUÍDO E VALIDADO | healthcheck e volume nomeado |
| Redis/worker | AUSENTE | nenhum serviço |
| Volumes | PARCIAL | volume do PostgreSQL existe; demais não aplicáveis ainda |
| Health checks | PARCIAL | PostgreSQL e `/health`; sem API container/readiness |
| Execução local | CONCLUÍDO E VALIDADO | API no host e banco no Compose documentados |

### Operação

| Requisito | Classificação | Evidência |
|---|---|---|
| Logging da aplicação | AUSENTE | apenas logging padrão do Alembic/servidor |
| Proteção de dados sensíveis | CONCLUÍDO E VALIDADO | SecretStr, respostas sanitizadas, scan limpo |
| Correlation ID | AUSENTE | sem middleware/contexto |
| Health | CONCLUÍDO E VALIDADO | `GET /health` |
| Readiness | AUSENTE | health não verifica PostgreSQL/Redis |
| Estatísticas de pedidos | AUSENTE | não há endpoint/query agregada |
| Observabilidade | AUSENTE | sem métricas/tracing estruturado |
| Erros consistentes | PARCIAL | HTTP status coerentes, formatos definidos por rota |

### Qualidade

| Requisito | Classificação | Evidência |
|---|---|---|
| Pytest | CONCLUÍDO E VALIDADO | 331 casos aprovados |
| HTTPX | CONCLUÍDO E VALIDADO | TestClient usa `httpx2` do grupo dev |
| Ruff/formatter | CONCLUÍDO E VALIDADO | lint e format check aprovados |
| Type hints | CONCLUÍDO E VALIDADO | models, schemas, services e repositories tipados |
| Análise estática de tipos | AUSENTE | sem mypy/pyright configurado |
| Testes unitários | CONCLUÍDO E VALIDADO | fakes e adapters isolados |
| Testes de integração | CONCLUÍDO E VALIDADO | rotas e banco |
| Testes PostgreSQL | CONCLUÍDO E VALIDADO | migrations, locks, constraints e rollback |
| README | PARCIAL | bom guia atual; falta consolidação final do roadmap |
| Documentação técnica | PARCIAL | README e esta auditoria; sem ADRs/guia de operação final |

## 5. Itens concluídos

Estão concluídos e comprovados: configuração tipada, API FastAPI, CRUD de
Customers/Products, criação e consulta de Orders, autenticação JWT, banco e
migrations, Payment mock, Stripe Test Mode, webhooks manuais e oficiais,
Refunds, idempotência, concorrência financeira, rollback e uma suíte ampla de
testes reais. `app/api/router.py` registra todas as rotas esperadas e o OpenAPI
confirma autenticação nos recursos administrativos.

## 6. Itens parciais

- Orders possuem estados, mas não uma máquina de transições.
- Cancelamento não possui regras laterais nem integração com estoque.
- Há autenticação, porém não ownership/RBAC nem isolamento por usuário.
- Tratamento de erro é seguro, mas distribuído e sem envelope global.
- Docker Compose é válido, porém cobre somente o PostgreSQL.
- Health é liveness simples, não readiness.
- README é suficiente para o estado atual, mas ainda não é o guia final.

## 7. Itens ausentes

Estoque transacional; locks de Product; Redis; Celery; worker; tasks de
confirmação, notificação e relatório; retry/backoff de jobs; Dockerfile; serviços
API/Redis/worker no Compose; logging estruturado; correlation ID; readiness;
estatísticas de pedidos; isolamento de dados; type checker e CI.

## 8. Evoluções aprovadas

As Fases 12–14 adicionaram Stripe Payment Intents, assinatura oficial de webhook,
sincronização financeira e Refunds totais/parciais. Essas entregas não estavam no
núcleo original, mas foram explicitamente aprovadas e estão concluídas. Elas não
substituíram o Payment mock: o endpoint genérico continua disponível. Também não
substituíram Order/estoque/background jobs; esses requisitos foram apenas adiados.

## 9. Itens fora do escopo original

- Adapter real Stripe em Test Mode e tradução de exceções do SDK.
- Verificação criptográfica de `Stripe-Signature` e política de acknowledgement.
- Entidade Refund, reserva de saldo e agregado financeiro.
- Sincronização de refunds iniciados no Dashboard.
- Proteção contra eventos fora de ordem e corridas API/webhook.

Todos são evoluções aprovadas, não implementações acidentais.

## 10. Avaliação da Stripe

A integração agrega valor real de portfólio: demonstra boundaries de adapter,
conversão monetária exata, idempotência externa, sanitização de falhas e testes
sem rede. A complexidade é superior ao necessário para um CRUD júnior, porém é
defensável porque as invariantes estão centralizadas e testadas. O endpoint
manual de PATCH de Payment deve ser revisto no hardening: hoje um usuário
autenticado pode alterar também um Payment Stripe segundo a máquina genérica.

## 11. Avaliação dos Refunds

Refunds são a parte mais avançada do projeto. A entidade própria, os estados e o
agregado evitam modelagem incorreta por campos soltos no Payment. A reserva de
saldo e as duas fronteiras transacionais são justificadas pela chamada externa.
Não há indicação de remoção. O próximo ganho marginal financeiro seria pequeno
comparado ao custo; disputes, Connect, live mode e novos fluxos devem ficar fora.

## 12. Análise de complexidade

O projeto continua explicável para uma vaga Python Backend Júnior se a narrativa
for incremental: CRUD e autenticação primeiro; banco e concorrência depois;
Stripe como aprofundamento opcional. A complexidade não deve ser apresentada
como qualidade por si só. O valor está nas invariantes verificáveis e nos testes.

O desequilíbrio atual é relevante: a camada financeira possui mecanismos de
produção enquanto o fluxo central de estoque e os jobs originais não existem.
Portanto, a prioridade deve voltar ao domínio principal e à operação básica.

## 13. Possíveis excessos

| Achado | Impacto | Prioridade | Recomendação | Fase |
|---|---|---|---|---|
| Muitos tipos de exceção financeira | aumenta carga cognitiva, mas preserva erros sanitizados | baixa | manter; consolidar apenas se houver dor real | 18 |
| Dois adapters Stripe com mapeamento semelhante | pequena duplicação | baixa | considerar helper comum sem alterar contratos | 18 |
| `get_refund_by_provider_id` e `get_refund_by_id` sem consumidores | código morto pequeno | baixa | remover após confirmar cobertura | 18 |
| Repository faz commit internamente | dificulta unit of work ampla | média | não refatorar globalmente; definir padrão antes de estoque/jobs | 16 |
| Robustez financeira antes do core | atraso de requisitos originais | alta | congelar novas features financeiras | imediato |

Protocols/gateways usados por uma única implementação não foram classificados
como excesso: eles permitem fakes determinísticos e impedem rede nos testes.
Arquivos `__init__.py` vazios são marcadores normais de pacote. Não foram
encontrados TODO/FIXME, módulos desconectados, endpoint não registrado, schema
sem consumidor relevante ou repository/service vazio.

## 14. Auditoria de segurança

Não existe `.env`. `git check-ignore` confirma sua proteção. `.env.example`
contém apenas defaults locais de desenvolvimento, segredo JWT explicitamente de
substituição e campos Stripe vazios. A URL local PostgreSQL e a senha do Compose
são credenciais descartáveis de desenvolvimento, não credenciais externas.

A busca por formatos `sk_test_`, `sk_live_`, `rk_*`, `whsec_*` e private keys não
encontrou candidatos. Referências JWT nos testes usam fixtures explicitamente de
teste. O OpenAPI não contém secrets, private keys nem stack traces. Nenhum valor
suspeito foi impresso durante a auditoria.

O `.gitignore` foi ampliado somente para caches temporários observados,
arquivos SQLite locais e dados do Celery. O `.dockerignore` recebeu os padrões
equivalentes, além de build/egg-info e IDE. Migrations, testes, docs,
`.env.example`, Compose e código continuam incluídos.

## 15. Auditoria arquitetural

| Achado | Evidência | Impacto/prioridade | Recomendação |
|---|---|---|---|
| Sem máquina de Order | `OrderService.update` delega qualquer enum | alto | implementar transições explícitas na Fase 16 |
| Sem lifecycle de estoque | criação só lê preço/ativo | alto | lock, disponibilidade, débito/reposição e rollback na Fase 16 |
| Recursos globais | routers exigem JWT sem ownership | médio | decidir ownership/admin e testar na Fase 18 |
| PATCH manual em Stripe Payment | rota genérica não distingue provider | médio | restringir estados geridos por webhook na Fase 18 |
| Sem app container/jobs | Compose só possui PostgreSQL | alto | Dockerfile, Redis, API e worker na Fase 17 |
| Sem logging/readiness | somente `/health` estático | médio | middleware/log estruturado e probes na Fase 18 |
| Erros por rota | muitos blocos de tradução locais | baixo | avaliar handlers compartilhados, sem refatoração ampla |
| Métodos Stripe sem uso | duas leituras no repository de webhook | baixo | remover apenas em fase de hardening |
| `REDIS_URL` não consumida | `.env.example` antecipa feature | baixo | conectar na Fase 17; não é secret |

Uma inspeção estática encontrou referências recíprocas somente entre models sob
`TYPE_CHECKING`, necessárias às relações SQLAlchemy; não são ciclos de import em
runtime. Ruff não encontrou imports mortos. Todas as dependências runtime têm
uso identificável; `httpx2` atende indiretamente o TestClient. Alembic check
confirma que models e migrations estão coerentes.

## 16. Resultado das validações

```text
RUN_POSTGRES_INTEGRATION_TESTS=1 pytest -q
331 passed, 2 warnings in 4.74s

ruff check .
All checks passed!

ruff format --check .
86 files already formatted

alembic current / alembic heads
9f3c2a1b7d14 (head)

alembic check
No new upgrade operations detected.

docker compose config
válido; serviço postgres, healthcheck e volume postgres_data
```

A aplicação importou com sucesso. O OpenAPI foi gerado, os paths críticos foram
comparados, OAuth2 apontou para `/api/v1/auth/login`, rotas protegidas continham
security e o webhook Stripe permaneceu público por assinatura. Endpoints Stripe
e Refund estavam presentes. Não existe analisador de tipos configurado, portanto
nenhum comando de mypy/pyright era aplicável.

## 17. Riscos atuais

1. Orders podem saltar arbitrariamente entre estados.
2. Pedidos ignoram disponibilidade e não movimentam estoque.
3. Qualquer usuário autenticado acessa recursos globais.
4. PATCH manual pode competir semanticamente com status Stripe.
5. Não há worker para tarefas lentas nem retry operacional.
6. Não há imagem da API ou stack local completa reproduzível.
7. Logs, readiness e correlation ID são insuficientes para diagnóstico.
8. O workspace está no OneDrive, que já causa warnings de cache e pode afetar IO.

## 18. Dívida técnica

- Formalizar FSM de Order e momento exato do débito/reposição de estoque.
- Definir boundary de autorização e ownership.
- Decidir padrão transacional para casos de uso que abrangem vários repositories.
- Restringir mutações manuais de Payments Stripe.
- Consolidar tradução de erros somente onde reduzir duplicação real.
- Remover os dois métodos de leitura não usados no repository Stripe.
- Configurar type checker e CI.
- Completar containerização e operação.

## 19. Roadmap obrigatório

### Fase 16 — Orders e estoque transacional

- Objetivo: concluir o domínio central antes de novas integrações.
- Escopo: FSM de Order, disponibilidade, locks em Product, débito/reposição,
  cancelamento, concorrência e rollback PostgreSQL.
- Dependências: schema atual; decisão documentada sobre quando reservar estoque.
- Aceite: nenhuma quantidade negativa, duas compras concorrentes seguras,
  transições inválidas recusadas e testes unitários/PostgreSQL.
- Complexidade: alta. Obrigatória.

### Fase 17 — Jobs e stack Docker completa

- Objetivo: cumprir o requisito assíncrono original e tornar o ambiente reproduzível.
- Escopo: Redis, Celery, worker, tasks de confirmação/notificação mock/relatório,
  retry com backoff e limite, Dockerfile e serviços API/Redis/worker no Compose.
- Dependências: lifecycle de Order estabilizado na Fase 16.
- Aceite: stack sobe saudável, tasks são idempotentes e testes não dependem de
  timing frágil.
- Complexidade: alta. Obrigatória.

### Fase 18 — Autorização e operação básica

- Objetivo: fechar segurança e operação esperadas de um backend de portfólio.
- Escopo: ownership ou papel administrativo explícito, restrição do PATCH Stripe,
  logging estruturado, correlation ID, readiness, estatísticas e consistência de
  erros; pequenos dead codes podem ser removidos com testes.
- Dependências: decisões de usuário/domínio e serviços da Fase 17.
- Aceite: isolamento testado, logs sem secrets, probes reais e estatísticas
  documentadas.
- Complexidade: média/alta. Obrigatória.

### Fase 19 — Qualidade e publicação do portfólio

- Objetivo: produzir a versão final auditável para GitHub.
- Escopo: type checker, CI, segurança de dependências, testes Docker end-to-end,
  revisão OpenAPI, README final, diagramas/ADRs essenciais e checklist de secrets.
- Dependências: Fases 16–18.
- Aceite: CI verde em clone limpo, setup reproduzível, documentação suficiente
  para demonstração e nenhuma credencial.
- Complexidade: média. Obrigatória.

## 20. Roadmap opcional

### Fase 20 — Confiabilidade financeira assíncrona

- Escopo: outbox, reconciliação de operações incertas e replay administrativo
  autenticado/auditável.
- Dependências: worker e observabilidade básica.
- Aceite: recuperação comprovada de falhas entre Stripe e banco, sem duplicidade.
- Complexidade: alta. Opcional; necessária apenas para ambição de produção.

### Fase 21 — Extensões de produto/produção

- Escopo selecionável: observabilidade avançada, deploy, frontend, live mode e
  disputes. Stripe Connect permanece fora salvo requisito futuro explícito.
- Dependências: segurança e operação concluídas.
- Aceite: deve ser definido por item, sem transformar o pacote inteiro em meta.
- Complexidade: média/alta. Opcional.

Classificação explícita: outbox, reconciliação, replay, observabilidade avançada,
live mode, disputes e frontend são opcionais. CI, logging básico, readiness,
Docker completo e documentação final são obrigatórios para o portfólio.

## 21. Definição objetiva de conclusão

O OrderFlow estará concluído como portfólio quando as Fases 16–19 estiverem
aceitas, a stack puder ser iniciada a partir de um clone limpo, CI e testes reais
estiverem verdes, o domínio de Order/estoque for concorrente e transacional, jobs
originais funcionarem com retries limitados, autorização estiver explicitamente
definida e a documentação permitir explicar cada decisão sem depender das fases
opcionais. Fases 20–21 não bloqueiam essa conclusão.
