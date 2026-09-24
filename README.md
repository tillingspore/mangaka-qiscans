# mangaka-qiscans
Wrapper de conteúdo do [Qi Scans](https://qiscansmanga.org/) para o Mangaka.

Levantamento inicial para adicionar Qi Scans como fonte do Mangaka:
[mapeamento técnico](docs/qiscans-discovery.md).

Domínio confirmado: `https://qiscansmanga.org`.

O wrapper fornece catálogo, busca, filtros por gênero, detalhes, capítulos e
proxy de imagens em uma API HTTP para uso interno pelo Mangaka.

## Executar localmente

Requer Python 3.11 ou superior:

```bash
python -m venv .venv
.venv/bin/pip install '.[test]'
.venv/bin/gunicorn --bind 127.0.0.1:3002 --workers 1 --threads 4 --timeout 120 'qiscans.app:create_app()'
```

Com Docker:

```bash
docker build -t mangaka-qiscans .
docker run --rm -p 127.0.0.1:3002:3002 mangaka-qiscans
```

A construção da imagem executa os testes sem consultar o site. A API foi
projetada para a rede interna do Compose, sem autenticação própria.
No Mangaka, configure `QISCANS_API_URL` com o endereço alcançável pelo servidor
web e pelo worker. A integração Docker usa `http://qiscans:3002`.

## Contrato HTTP

Todas as respostas JSON incluem `source: "qiscans"`. O parâmetro opcional
`source` aceita somente `qiscans`.

| GET | Parâmetros / resposta |
| --- | --- |
| `/api/health` | Saúde do processo; não consulta o site |
| `/api/manga/catalog` | `page`, `tag`, `q`; `results`, `total: null`, `has_next` |
| `/api/manga/search` | `q`, `page`, `tag`; mesmo formato do catálogo |
| `/api/manga/tags` | `tags` com `id` numérico em string e `name` |
| `/api/manga/{id}` | Título, sinopse, capa, autor, ano, status e gêneros |
| `/api/manga/{id}/chapters` | `chapters` com ID, número, título, idioma e data |
| `/api/manga/{id}/chapters/{chapter_id}/pages` | `pages`: URLs na ordem do leitor |
| `/api/proxy/image` | `url`; bytes da imagem com seu Content-Type |

IDs são os slugs dos caminhos públicos, sem o domínio. Exemplo: obra `kingdom`
e capítulo `kingdom-chapter-889`. A API verifica que o capítulo pertence à obra.
O catálogo usa a ordenação por atualização do site. `page` aceita 1–500;
`q` aceita até 120 caracteres. A paginação acompanha a fonte, sem simular blocos
de 20 itens: clientes devem usar `has_next`, mesmo após uma página pequena ou vazia.
`limit` não altera a quantidade de itens por página.

Novels identificadas nos cards são excluídas. Busca com gênero valida cada
resultado contra os metadados da obra, pois a busca do site pode ignorar
`genre[]`; essa combinação pode ser mais lenta e produzir páginas vazias com
próxima página. Os capítulos são identificados como inglês (`en`) nesta fonte.
Isso não oferece tradução automática, nem garante capítulos antigos ausentes
na listagem do site.

## Cache e falhas

Catálogo, buscas e obras ficam em cache por 15 minutos; páginas do leitor por
10 minutos; gêneros por 24 horas. O cache mantém até 256 entradas em memória.
Use um processo com múltiplas threads para compartilhar cache, consultas
simultâneas e pausa após falhas. O Mangaka mantém também seu cache Redis.

HTTP 429 respeita `Retry-After`, inclusive em formato de data; falhas de conexão
ou outros status inesperados pausam consultas por 30 segundos. Entradas já
armazenadas continuam disponíveis. O wrapper não faz tentativas automáticas.
Downloads têm limite de 4 MiB para HTML e 20 MiB para imagens.

O proxy aceita somente HTTPS nos hosts observados, verifica cada redirecionamento
e rejeita respostas que não sejam imagens raster. Mudanças de CDN exigem revisão
da lista em `qiscans/client.py`. O JSON do leitor é interpretado como dados,
sem executar scripts. Capítulos protegidos ou sem imagens geram erro explícito.

## Validar e atualizar a integração

```bash
.venv/bin/python -m pytest -q
# Consulta uma pequena amostra do site e uma imagem em memória:
.venv/bin/python scripts/smoke.py
# Após commit, exporta uma cópia rastreável para o Mangaka:
python scripts/export.py ../mangaka/integrations/qiscans
```

A exportação usa somente arquivos do commit atual e grava a origem em
`UPSTREAM.md`. O Mangaka inclui essa cópia no seu próprio Git, permitindo
instalação e atualização sem clonar o wrapper separadamente. Mudanças no
wrapper precisam ser exportadas e commitadas também no Mangaka.
