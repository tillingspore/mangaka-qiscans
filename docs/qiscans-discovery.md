# Levantamento da fonte Qi Scans

Investigação em 24/09/2026. Integração de referência: checkout local
`../mangaka`, commit `a213e06`. Este documento descreve observações e uma
proposta de contrato; não representa uma integração implementada.

## Endereço e método

`qiscanmanga.org`, registrado originalmente no README, falhou na resolução
DNS, inclusive fora do sandbox. A busca encontrou
[qiscansmanga.org](https://qiscansmanga.org/), que se apresenta como Qi Scans.
A confirmação pelo usuário de que esse é o site pretendido está pendente.
Não foi estabelecida identidade com outros domínios ou grupos de mesmo nome.

Consultas HTTP GET públicas recuperaram seis páginas HTML sem autenticação:
home, duas páginas do catálogo, busca, uma obra e um capítulo. Não foi
necessário executar JavaScript para obter esses documentos. Os caminhos
dos assets indicam WordPress com tema MangaReader. Uma API documentada
não foi validada; a proposta inicial usa o HTML observado.

## Mapeamento observado

| Operação | Endereço / extração |
| --- | --- |
| Catálogo | [`/manga/`](https://qiscansmanga.org/manga/); cards `.listupd .bs .bsx`, link de obra, `.tt`, imagem da capa |
| Próxima página | [Exemplo `?page=2`](https://qiscansmanga.org/manga/?page=2); seguir `.pagination a.next` e resolver URL relativa |
| Busca | [`/?s=kingdom`](https://qiscansmanga.org/?s=kingdom); cards no mesmo formato |
| Detalhes | [`/manga/kingdom/`](https://qiscansmanga.org/manga/kingdom/); `h1.entry-title`, `.alternative`, `.entry-content[itemprop=description]` |
| Capa e ficha | `.info-left .thumb img`; `.tsinfo .imptdt`, identificados pelos rótulos Status, Type, Released, Author e Artist |
| Gêneros da obra | `.mgen a`, com links `/genres/{slug}/` |
| Capítulos | `#chapterlist li`: `data-num`, `.eph-num a`, `.chapternum`, `.chapterdate` |
| Leitor | [`/kingdom-chapter-889/`](https://qiscansmanga.org/kingdom-chapter-889/); JSON passado a `ts_reader.run(...)` |

Os seletores são candidatos derivados do HTML inspecionado; ainda precisam
de testes de parser. Delimitar a área principal evita coletar recomendações
e rankings laterais como resultados.

As duas páginas de catálogo retornaram 30 cards cada, com conjuntos diferentes.
A navegação indicava cinco páginas naquele momento, sem total exato de obras.
Não fixar esses números no código. O formulário anuncia `genre[]` (IDs numéricos),
`status`, `type` e `order`; valores de ordenação incluem `title`, `titlereverse`,
`update`, `latest` e `popular`. A aplicação dos filtros não foi testada.
O catálogo também inclui novels, que precisam ser distinguidas de obras com imagens.

A busca por `kingdom` retornou quatro cards, incluindo títulos diferentes;
não assumir correspondência exclusiva pelo título. Paginação de busca ainda
não foi validada. Na obra examinada, a lista continha apenas os capítulos
889, 888 e 887; não inferir que capítulos anteriores estejam disponíveis.

## Imagens

O capítulo examinado tem `#readerarea` vazio no HTML inicial. O objeto do
leitor contém `sources`, `defaultSource`, `prevUrl`, `nextUrl`, `protected`
e `is_novel`. `sources[].images` fornece a ordem das páginas. Nessa amostra,
há um servidor, 19 URLs, `protected=false` e `is_novel=false`.

As URLs usam `i0.wp.com` a `i3.wp.com`. Um HEAD seguindo redirecionamentos
de [uma imagem da amostra](https://i0.wp.com/i.imgur.com/GH2mHCB.jpeg)
retornou 302 para `i.imgur.com` e depois 200 com `Content-Type: image/jpeg`,
sem Referer explícito. Isso não valida todas as imagens nem a leitura completa.

Extrair o objeto como dados JSON, sem executar scripts. Respeitar a fonte
selecionada e a ordem fornecida. Não incluir imagens de banners, placeholders
ou recomendações. Estados protegidos, novels, JSON ausente e lista vazia
precisam de tratamento explícito. Não persistir campos de tokens do leitor.

## Contrato proposto para o wrapper

Compatível com as chamadas atuais de `app/libs/manga_novel.py`:

| Rota proposta | Resposta mínima |
| --- | --- |
| `GET /api/manga/catalog` | `source`, `results`, `total` anulável, `has_next` |
| `GET /api/manga/search?q=...` | `source`, `results`; definir também paginação explícita |
| `GET /api/manga/tags` | `source`, `tags` |
| `GET /api/manga/{id}` | `source`, `title`, `description`, `coverUrl`, `genres`, `tagLinks`, `authors`, `year`, `status` |
| `GET /api/manga/{id}/chapters` | `source`, `chapters` com `id`, `number`, `title`, `lang` |
| `GET /api/manga/{id}/chapters/{chapter_id}/pages` | `source`, `pages` como URLs ordenadas |
| `GET /api/proxy/image?url=...` | Bytes e tipo de imagem |

Usar `source=qiscans`. Cada resultado de catálogo/busca precisa de `id`,
`title` e, preferencialmente, `coverUrl`. Proposta: IDs derivados do caminho
canônico, independentes do host, para reduzir impacto de mudanças de domínio.
Preservar números fracionários de capítulo como strings. O idioma inglês é
uma hipótese baseada na interface/metadados; validar nas obras antes de
generalizar `lang=en` para toda a fonte.

## Ajustes necessários no Mangaka

- `app/libs/manga_novel.py`: cadastrar a fonte em `SOURCES` e `VISIBLE_SOURCES`,
  escolher URL do serviço por fonte e habilitar gêneros para Qiscans.
- Ajustar paginação: `limit=20` atual difere dos 30 cards observados; a busca
  atual infere próxima página pelo tamanho e ignora `has_next` da resposta.
  Preferir paginação explícita do wrapper. Para capítulos, a implementação
  atual só percorre múltiplas páginas de ComicK; Qiscans deve retornar a lista
  inteira ou ganhar suporte explícito a paginação.
- `app/libs/library.py`: hoje instancia `MangaNovel` para todas as fontes
  adicionais e depende de `MANGA_NOVEL_API_URL` para exibi-las. Se o wrapper
  for um serviço independente, adaptar configuração e seleção do serviço.
- Reutilizar `SourceReference` e os UUIDs derivados de fonte/tipo/pai/ID remoto:
  esse mecanismo já separa favoritos e histórico por provedor.
- O proxy existente em `integrations/manga-novel/imageProxy.ts` não aceita
  os hosts observados. Implementar proxy da nova fonte com validação de host
  e de cada redirecionamento, incluindo o destino observado em `i.imgur.com`.
- Adicionar serviço ao Compose e ao fluxo de instalação caso a opção seja
  um wrapper independente. Preservar cache e tratamento de indisponibilidade
  existentes; tratar HTTP 429 e `Retry-After` no novo transporte.

## Validação restante

Antes de implementar: confirmar domínio pretendido. Antes de integrar:
validar filtros, busca paginada, outra obra, idioma e imagem via GET; definir
IDs, estratégia de paginação e roteamento do serviço. Criar fixtures pequenas
para casos de capítulos fracionários, novels, conteúdo protegido, falha de
parser e redirecionamentos de imagem. Não considerar catálogo vazio uma
resposta válida quando a página recebida for um erro ou desafio de acesso.

Amostras HTML ficaram temporariamente em `/tmp/qiscans-*.html`, fora do Git.
Nenhuma imagem de capítulo foi baixada e nenhuma alteração foi feita no Mangaka.
