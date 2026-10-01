# Capturas reais da interface

Este diretório contém cinco screenshots reais do OrderFlow, capturadas em
**01/10/2026** a partir da aplicação executada com Docker Compose em loopback.
O código corresponde ao estado da `main` em `b135ed1`, posterior à `v1.1.0`.

As páginas foram renderizadas pelo Microsoft Edge em uma instância de teste
isolada com Playwright, acessando o frontend Nginx e a API reais. O login ocorreu
pelo formulário da aplicação. As respostas da API e o conteúdo das páginas não
foram simulados; nenhuma imagem foi gerada por IA.

Todas as imagens foram inspecionadas visualmente. A conta `Admin Demo` e os
clientes/produtos são fictícios, com e-mails `@example.com`. O formulário de
login foi capturado vazio; não há senhas, JWT, tokens, chaves Stripe, credenciais
ou dados pessoais reais visíveis. Os pagamentos de demonstração são manuais,
sem chamadas Stripe ou cobranças reais.

## Arquivos publicados

| Arquivo | Página | Dimensões | Tamanho |
| --- | --- | --- | ---: |
| [dashboard.png](dashboard.png) | `/dashboard` | 1440 × 1220 | 83.452 bytes |
| [orders.png](orders.png) | `/orders` | 1440 × 1040 | 87.939 bytes |
| [order-details.png](order-details.png) | `/orders/2` | 1440 × 960 | 67.399 bytes |
| [products.png](products.png) | `/products` | 1440 × 900 | 63.859 bytes |
| [login.png](login.png) | `/login` | 1440 × 900 | 54.868 bytes |

Total: **357.517 bytes (349,1 KiB)**. Os PNGs mantêm a largura de 1440 pixels;
a altura foi ajustada ao conteúdo de cada página para exibir os controles
inteiros. Não foi necessária compressão com perda de qualidade.

## Como renovar as capturas

1. Execute o projeto conforme o [README principal](../../README.md#executando-localmente)
   ou o [runbook de produção local](../DEPLOYMENT.md).
2. Use exclusivamente uma conta de demonstração, como `Admin Demo` com
   `admin@example.com`. O nome da conta aparece na navegação do painel.
3. Crie clientes fictícios, como `Tech Store Demo` e `João Silva Demo`, com
   endereços `@example.com`; deixe telefones em branco.
4. Cadastre entre três e cinco produtos, com SKU `DEMO-*`, preço e estoque.
5. Crie alguns pedidos com itens diferentes e distribua-os pelos estados
   permitidos: pendente → processando → confirmado → enviado → entregue.
   O cancelamento só é permitido quando o pedido está pendente.
6. Para preencher as métricas financeiras, crie pagamentos com provedor
   `manual` e altere alguns para aprovado ou falhou. Essa demonstração não
   depende de chaves Stripe nem de cobranças reais.

Prefira português do Brasil, largura de **1440 pixels** e altura entre **900 e
1220 pixels**, conforme a página. Use zoom de 100%, esconda ferramentas de
desenvolvimento e feche modais.
Espere as consultas terminarem antes de capturar.

### Conteúdo de cada página

| Arquivo nesta pasta | Página | Conteúdo esperado |
| --- | --- | --- |
| `dashboard.png` | `/dashboard` | Métricas de pedidos e pagamentos, com dados fictícios; imagem principal. |
| `orders.png` | `/orders` | Lista de pedidos com valores e estados variados. |
| `order-details.png` | `/orders/<id>` | Cliente, produtos, quantidades, total e transições disponíveis. |
| `products.png` | `/products` | Catálogo de demonstração com preço e estoque. |
| `login.png` | `/login` | Formulário vazio, sem senha nem e-mail preenchidos. |

Capture somente o conteúdo da aplicação. Utilize a função de screenshot do
navegador ou uma captura de tela recortada, mantendo legíveis o título, os
cards e as tabelas. PNG otimizado ou WebP são adequados; prefira arquivos de
até aproximadamente 500 KB por imagem e mantenha o conjunto abaixo de 3 MB.
Se optar por WebP, ajuste as extensões nos links.

## Revisão e apresentação no README

Abra **cada imagem** antes do commit. Confira nomes e e-mails fictícios e
ausência de senha, JWT, token, chave Stripe, `client_secret`, DSN, terminal,
credenciais, dados pessoais e ferramentas de desenvolvimento. A inspeção
visual é necessária mesmo com Gitleaks aprovado.

A imagem principal aparece logo abaixo dos badges do
[README](../../README.md):

```markdown
![Dashboard do OrderFlow com dados fictícios](docs/assets/dashboard.png)
```

Na seção Screenshots, mantenha imagens em tamanho legível e legendas curtas.
Produtos e login ficam em um bloco expansível para limitar o comprimento da
galeria:

```markdown
### Pedidos

![Lista de pedidos do OrderFlow com dados fictícios](docs/assets/orders.png)

### Detalhes do pedido

![Detalhes de um pedido de demonstração](docs/assets/order-details.png)

### Produtos

![Catálogo de produtos fictícios](docs/assets/products.png)

### Login

![Formulário de login do OrderFlow vazio](docs/assets/login.png)
```

Depois de renovar uma imagem, confira os links e atualize sua data, dimensões e
tamanho neste registro. Inclua essa verificação na descrição do PR.
