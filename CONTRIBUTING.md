# Contribuindo

Este projeto de portfólio aceita correções pequenas e reproduzíveis. Abra uma issue
com o comportamento observado e um exemplo sem dados sensíveis antes de propor uma
mudança ampla de arquitetura ou de regra de negócio.

## Ambiente

Use Python 3.12, Docker Compose e os comandos do [README](README.md). Instale com
`python -m pip install --group dev .`, copie `.env.example` para `.env` e escolha
um `JWT_SECRET_KEY` local. Nunca inclua `.env`, tokens, payloads reais da Stripe,
dumps de banco ou logs com dados pessoais em commits ou issues.

Antes de abrir um pull request, execute `ruff check .`, `ruff format --check .`,
`mypy` e `pytest -m 'not integration'`. Com PostgreSQL disponível, execute também
`RUN_POSTGRES_INTEGRATION_TESTS=1 pytest` e a cobertura descrita no README. A CI
deve passar, e mudanças de comportamento devem ter testes que verifiquem o efeito
observável. Dependabot só propõe atualizações; elas exigem revisão e CI.

Para falhas de segurança, siga [SECURITY.md](SECURITY.md) sem publicar detalhes em
issues abertas.
