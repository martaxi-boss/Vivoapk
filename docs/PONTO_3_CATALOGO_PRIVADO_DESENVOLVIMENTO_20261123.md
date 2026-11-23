# Ponto 3 — catálogo privado VivoAPK / Jellyfin — desenvolvimento (23/11/2026)

**Estado: backend de metadados em ramo de desenvolvimento; NÃO instalado em produção e NÃO equivale à playlist automática do Jellyfin.**

## Contrato do cliente

O utilizador continua a configurar apenas o servidor Jellyfin
`https://serveriptv.duckdns.org` e a sua conta Mega OTT no ecrã de login.
Não se instalam listas globais Xtream, não se modificam as contas PINK,
não se criam URLs públicos de catálogo e não se usa a VPS para armazenar filmes.

## Implementação de fase A

- `POST /v1/catalog/list` no backend privado, pensado para ser utilizado
  pelo plugin Jellyfin via socket Unix; requer JWT VivoAPK assinado,
  `kind=live|vod|series` e `platform=android|android_tv|ios|windows|tizen|webos`.
- O token contém apenas o ID interno de mapeamento e tem TTL curto.
  A rota ignora usernames e DNS do corpo e recusa campos extra.
- A cada pedido consulta o ID de subscrição Mega OTT, exige correspondência
  exata com o mapeamento, verifica data de expiração e torna a validar o
  estado Xtream. Falha fechada se a password não estiver disponível no
  detalhe devolvido pela API oficial.
- Seleciona apenas o DNS completo oficialmente atribuído à subscrição,
  incluindo o campo Samsung/LG quando essa plataforma é solicitada.
- Consulta apenas `get_live_categories/streams`,
  `get_vod_categories/streams`, `get_series_categories/series`,
  e devolve categorias e títulos/IDs/extensões validados.
- **Nunca** inclui no JSON de resposta username, password, token da API
  do revendedor, URLs de streaming, `dns_link`, URLs de imagens ou
  `direct_source`.
- Reutiliza o bloqueio a IPs privados/SSRF e a escolha de DNS com ligação
  pinada. O limite de 8 MiB é aplicado durante a leitura da resposta
  HTTP (antes de colocar um corpo gigante em memória).
- Reutiliza o orçamento de pedidos por conta/global e coloca
  `Cache-Control: no-store` na resposta.

## Verificações e limitações

Os testes usam duas subscrições fictícias com tokens assinados diferentes.
Verificam que as duas recebem nomes diferentes, que campos para trocar de
utilizador são rejeitados, e que sessões expiradas, assinaturas revogadas,
password ausente e status bloqueado não dão catálogo. Testes de DNS local,
redirecionamentos e resposta de tamanho excessivo também rejeitam a origem.

**A integração ainda não fornece catálogo visível à app oficial Jellyfin.**
O plugin de autenticação instalado não guarda o JWT devolvido pelo resolver
nem implementa um browser de catálogo. Além disso, os itens `IChannel`
do Jellyfin passam por indexação global em `ChannelManager`; um
`GetCacheKey(userId)` distinto, sozinho, não comprova separação.
Não instalar um tuner Xtream partilhado ou um `IChannel` multicliente sem
ensaio real de duas contas e verificação de IDs, acesso direto e revogação.

As séries têm ainda de ter temporadas/episódios e o EPG integrado no
Jellyfin. A reprodução `Direct Play` real precisa de teste em Android,
iOS e TV; o Jellyfin pode encaminhar, remuxar ou transcodificar.
Ocultar URLs do ecrã não os torna invisíveis no tráfego de rede do player.

## Porta de produção (ainda não cumprida)

1. Verificar, com conta real autorizada, `GET /v1/subscriptions/{id}`
   e a disponibilidade do campo `password` na API Mega OTT.
2. Demonstrar separação de 2 contas reais na integração nativa Jellyfin,
   incluindo revogação e expiração.
3. Demonstrar categorias/filmes/episódios/EPG completos na app Jellyfin.
4. Medir tráfego da VPS com reprodução direta, remux e transcoding.
5. Só depois instalar o componente de catálogo na VPS com backup,
   smoke test real, proteção `pink-admin` e rollback.

## Ferrari / produção observada

A auditoria read-only de capacidade Mega OTT enviada pelo Ferrari
foi executada em [VPS #45091000869](https://github.com/martaxi-boss/VPS/actions/runs/45091000869).
O transporte de sondagem `urllib` devolveu HTTP 403 na listagem;
a sondagem ficou `status=blocked`, apesar de o invólucro Ferrari ter
`FERRARI_SKILL_JOB_STATUS=completed`. **Não é um PASS de API de
catálogo.** Uma auditoria anterior com `httpx trust_env=False`
tinha devolvido 200 na listagem, pelo que não é correto concluir que a
chave do PINK deixou de funcionar. Nenhuma credencial foi publicada.

O PINK e o Jellyfin de produção ficam inalterados enquanto este trabalho
está no ramo de desenvolvimento.
