# Ponto 3 — catálogo privado VivoAPK / Jellyfin — desenvolvimento (23/11/2026)

**Estado: backend de metadados + provisionador de biblioteca nativa por utilizador em ramo de desenvolvimento; NÃO instalado em produção e NÃO equivale à playlist automática do Jellyfin.**

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

## Fase B — isolamento nativo Jellyfin (desenvolvimento)

- A auditoria ao código Jellyfin `ChannelManager` confirmou que os itens `IChannel` passam por indexação comum. Não assumir que `GetCacheKey(userId)` protege um item indexado.
- A prova com o servidor Jellyfin 12.4 real e duas bibliotecas de vídeo **fictícias** passou em [CI #45092098008](https://github.com/martaxi-boss/Vivoapk/actions/runs/45092098008). Cada utilizador só viu a sua biblioteca; um ID de outro utilizador não abriu o ficheiro; a desativação revogou o token Jellyfin. Foram usadas duas amostras MP4 locais no runner; nenhum media foi colocado na VPS.
- O plugin de autenticação ganhou o opt-in `ProvisionPrivateLibraries=false` por defeito. Com o opt-in ativo num Jellyfin descartável, a autenticação Mega bem-sucedida pode criar uma biblioteca **vazia** própria com pasta por GUID em `IApplicationPaths.DataPath/vivoapk-private-libraries/<user-guid>`, e configurar `EnabledFolders` apenas para a biblioteca dessa conta, desabilitando partilha pública, download e canais globais. O login original com nome e password não muda.
- O provisionador recusa bibliotecas com mesmo nome mas caminho inesperado, e diretórios-alvo simbólicos. Usa uma serialização por GUID para impedir criação duplicada em logins simultâneos.
- **A prova de provisionamento automático deve ser considerada pendente até a CI específica ficar verde.** Um teste falhou inicialmente porque assumiu um caminho de dados Jellyfin distinto do realmente usado; a validação agora usa `IApplicationPaths.DataPath` observado e exige que a localização esteja debaixo da árvore de dados, com sufixo GUID correto. Não declarar esta fase aprovada apenas porque o plugin compila.
- Enquanto o backend não disponibilizar URLs de media autorizadas num contrato privado seguro, estas bibliotecas ficam vazias: não guardar credenciais Mega em ficheiros `.strm` públicos ou inserir uma playlist global temporária.

### Auditoria real Ferrari (23/11/2026)

A [preservação read-only #45092203903](https://github.com/martaxi-boss/VPS/actions/runs/45092203903) terminou com `status=passed` **dentro do artefacto `rea-mcp-result.json`**, além do `FERRARI_SKILL_JOB_STATUS=completed`. Confirmou seis serviços ativos (PINK backend/VPN, VivoAPK, Jellyfin, Nginx e PostgreSQL), sockets internos Jellyfin e VivoAPK HTTP 200 e plugin Auth presente. O backend de produção permanece na revisão `540846e48ca3655acb3b0a8601dea700a082979d`, não no ramo do ponto 3. O pedido não acedeu a credenciais e não alterou serviços.

### Portas restantes

1. Demonstrar os detalhes da API Mega OTT reais e autorizados, sem publicar credenciais.
2. Definir e testar um vínculo duradouro revogável entre a identidade Jellyfin e a subscrição Mega, **sem guardar passwords em texto claro**.
3. Preencher a biblioteca correta apenas com metadados autorizados (canais, filmes, temporadas, episódios e EPG), sem indexação partilhada.
4. Provar que a reprodução é direta no Android/iOS/Samsung/LG, sem pass-through de vídeo pela VPS; limitar e medir consumo.
5. Só depois ativar a funcionalidade na VPS com backup, rollback e teste da password administrativa original.

## Fase C — temporadas/episódios e EPG curto (backend apenas)

- Adicionados `POST /v1/catalog/series` e `POST /v1/catalog/epg`, ambos apenas no backend privado Unix, protegidos pelo mesmo JWT curto `vivoapk`; requests com campos extra e IDs malformados são recusados.
- Antes de consultar o detalhe, o backend volta a exigir mapeamento e assinatura válidos, recupera a credencial por via privada, verifica Xtream e confirma que o identificador de série/canal **consta da listagem daquela subscrição**. Um ID de outra conta não serve como autorização.
- `series` interpreta `get_series_info` e devolve somente temporadas, identificadores de episódios, títulos, números e extensões permitidas. `epg` usa `get_short_epg` com limite máximo de 20 e devolve título/horários básicos. Sem URLs de streaming, passwords, DNS, `direct_source` ou imagens de terceiros.
- Para resposta de fornecedor, permanece limite de 8 MiB aplicado durante a leitura HTTP via `PinnedHTTPTransport`, com recusa de redirecionamentos e de resolução para IP privado.
- Testes sintéticos verificam dois clientes, IDs de outra subscrição negados, parâmetros errados e exclusão dos campos sensíveis; o resultado da CI deve ser consultado antes de anunciar PASS.
- Isto ainda **não é apresentação no Jellyfin**. Falta a ligação do plugin ao backend, o catálogo nativo e a reprodução/EPG visíveis na app.

### Prova da criação automática no Jellyfin descartável

O fluxo integral com `ProvisionPrivateLibraries=true` apenas em teste passou em [CI #45092669249](https://github.com/martaxi-boss/Vivoapk/actions/runs/45092669249). Confirmou contas fictícias, criação automática das bibliotecas privadas por GUID, permissões, login repetido sem duplicação, listagem isolada, acesso por ID estrangeiro negado, revogação de utilizador desativado e administrador intacto. A opção é `false` por omissão e não existe no binário atualmente instalado na VPS.

**Sem ativação em produção:** PINK, VPN, Jellyfin, VivoAPK e as credenciais guardadas no cofre privado permanecem intocados neste desenvolvimento.
