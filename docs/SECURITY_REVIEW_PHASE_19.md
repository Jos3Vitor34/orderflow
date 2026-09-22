# Revisão de segurança — Fase 19

Data: 22 de setembro de 2026. Escopo: código da aplicação, dependências
instaladas, histórico Git disponível, imagem local e fluxos de desenvolvimento.
Este registro não representa auditoria de produção ou garantia de segurança.

## Decisões de aplicação

- JWT usa segredo obrigatório, algoritmo permitido por configuração tipada,
  expiração e tipo de token verificados. A role do token precisa corresponder à
  role persistida; usuário inativo é recusado. Senhas usam Argon2 via `pwdlib`.
- Schemas de escrita proíbem campos extras. Respostas públicas não expõem hash,
  segredo ou credenciais. A API não configura CORS permissivo; consultas ao banco
  usam SQLAlchemy com parâmetros, sem interpolação SQL de entrada do usuário.
- O endpoint Stripe oficial permanece público, mas exige assinatura verificada
  com o corpo bruto e chave de Test Mode configurada. Testes usam assinaturas
  sintéticas, sem chamada à Stripe. O receptor genérico legado **não** validava
  autenticidade de provider e podia alterar estado de Payment; agora requer
  `OPERATOR` ou `ADMIN`. O nome `stripe` é reservado para a rota assinada.
- Logs JSON redigem tokens, senhas, assinaturas e URLs com credenciais. O
  correlation ID é validado e propagado até tasks Celery. Readiness revela
  somente o estado de PostgreSQL/Redis. Redis e PostgreSQL do Compose publicam
  portas apenas em loopback. A imagem roda como usuário sem privilégios.
- A migration de roles já aplicada é preservada. Ela promove todos os usuários
  preexistentes a administrador para manter compatibilidade; em banco vazio não
  cria nem promove usuários. Após upgrade de um banco existente, o operador deve
  revisar e reduzir essas roles conforme o README. Downgrade só em banco
  descartável.

## Auditorias locais

`pip-audit --local` não reportou vulnerabilidades após atualizar o `pip` do
ambiente de 25.3 para 26.2.1. Os dez avisos iniciais pertenciam ao próprio
`pip`. A análise estática usa as regras `S` do Ruff para todo o pacote `app`;
os testes usam credenciais fictícias e são isentos apenas dessas regras.
Gitleaks 8.30.1 examinou a árvore e os quatro commits anteriores. As exceções
do Gitleaks se restringem a chaves Stripe sintéticas em testes e a uma antiga
chave idempotente de exemplo no README; nenhum segredo real foi encontrado.

## Imagem Docker

O scan com Trivy 0.74.0 da imagem `python:3.12.14-slim-trixie` encontrou 44
ocorrências HIGH, representando **oito CVEs de pacotes do Debian**, e nenhum
CRITICAL. Não havia versão corrigida indicada pelo Debian em 22/09/2026.
Nenhuma das 44 ocorrências é de biblioteca Python do projeto. As exceções em
`.trivyignore.yaml` são individuais, têm motivo e expiram em **22/10/2026**.
Vulnerabilidades novas continuam a reprovar o scan. Reavaliar a imagem base e
os advisories antes dessa data; remover cada exceção assim que houver correção.

| CVE | Pacote fonte | Ocorrências | Tracker primário |
| --- | --- | ---: | --- |
| CVE-2025-69720 | ncurses | 4 | [Debian](https://security-tracker.debian.org/tracker/CVE-2025-69720) |
| CVE-2026-16742 | systemd | 2 | [Debian](https://security-tracker.debian.org/tracker/CVE-2026-16742) |
| CVE-2026-54369 | acl | 1 | [Debian](https://security-tracker.debian.org/tracker/CVE-2026-54369) |
| CVE-2026-76642 | util-linux | 9 | [Debian](https://security-tracker.debian.org/tracker/CVE-2026-76642) |
| CVE-2026-78408 | util-linux | 9 | [Debian](https://security-tracker.debian.org/tracker/CVE-2026-78408) |
| CVE-2026-78409 | util-linux | 9 | [Debian](https://security-tracker.debian.org/tracker/CVE-2026-78409) |
| CVE-2026-78410 | util-linux | 9 | [Debian](https://security-tracker.debian.org/tracker/CVE-2026-78410) |
| CVE-2026-9538 | perl | 1 | [Debian](https://security-tracker.debian.org/tracker/CVE-2026-9538) |

A imagem foi reduzida removendo `pip` do runtime depois da instalação, o que
eliminou dois avisos HIGH corrigíveis em pacotes vendorizados do instalador.
Uma SBOM CycloneDX é gerada como artefato da CI e não é versionada para evitar
inventário desatualizado. O risco residual é o uso local de pacotes do sistema
com CVEs sem patch, mesmo quando a funcionalidade vulnerável não é utilizada
diretamente pela aplicação.

## Limites restantes

Não há rate limiting distribuído, outbox transacional ou deploy público. Redis
é efêmero e o broker pode perder uma mensagem entre commit e publicação. O
Compose tem credenciais de exemplo, adequadas somente ao desenvolvimento local.
As versões de dependências são intervalos compatíveis, sem lockfile; a CI audita
o ambiente resolvido e atualizações do Dependabot exigem revisão.
