# Capturas reais da interface

As screenshots desta fase estão **pendentes de captura manual**. A stack local
iniciou corretamente, mas o navegador integrado não estava disponível e o
Computer Use encerrou a captura por não conseguir identificar a URL atual no
Windows com confiança suficiente para aplicar sua política. Nenhuma imagem
artificial ou arquivo de screenshot inexistente foi incluído no README.

## Preparação

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

Prefira português do Brasil e viewport **1440 × 900**, ou **1920 × 1080** se
for necessário mostrar mais conteúdo. Mantenha a mesma resolução nas imagens;
use zoom de 100%, esconda ferramentas de desenvolvimento e feche modais.
Espere as consultas terminarem antes de capturar.

## Imagens a capturar

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

## Revisão e inclusão no README

Abra **cada imagem** antes do commit. Confira nomes e e-mails fictícios e
ausência de senha, JWT, token, chave Stripe, `client_secret`, DSN, terminal,
credenciais, dados pessoais e ferramentas de desenvolvimento. A inspeção
visual é necessária mesmo com Gitleaks aprovado.

Após salvar os arquivos reais, inclua a imagem principal logo abaixo dos badges
do [README](../../README.md):

```markdown
![Dashboard do OrderFlow com dados fictícios](docs/assets/dashboard.png)
```

Na seção Screenshots, use imagens em tamanho legível, com uma legenda curta:

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

Remova o aviso de pendência somente quando as imagens estiverem salvas,
inspecionadas e com links válidos. Registre página, resolução e tamanho de cada
arquivo na descrição do PR que adicionar as capturas.
