# VivoAPK — segurança de múltiplos clientes no Jellyfin (23/11/2026)

## Contrato

Conservar o [BOOTSTRAP](../BOOTSTRAP.md): aplicação oficial Jellyfin em todas as plataformas suportadas, entrada única `https://serveriptv.duckdns.org`, username/password Mega OTT, gestão exclusiva no painel Mega e **nenhuma retransmissão/transcodificação pela VPS**.

## Vulnerabilidade reproduzida num Jellyfin descartável

No ramo experimental [experiment/jellyfin-multitenant-isolation-20261123](https://github.com/martaxi-boss/Vivoapk/tree/experiment/jellyfin-multitenant-isolation-20261123), a tentativa inicial de usar **um único** plugin `IChannel` para duas contas simultâneas com snapshots por GUID expôs um item de A à conta B. O código de teste obteve HTTP 200 em `GET /Users/{B}/Items/{itemA}` autenticado como B e lançou a exceção `MULTITENANT_ITEM_ID_BYPASS`.

**Prova:** [GitHub Actions Jellyfin 12.4 real, contas fictícias, #45282374652](https://github.com/martaxi-boss/Vivoapk/actions/runs/45282374652). **Isto não é uma exploração contra a VPS de produção.** O plugin de produção ainda só aceita uma conta no piloto de até 5 canais; não utilizar o ramo inicial que partilha `IChannel` nem declarar o catálogo comum pronto.

A causa aparente é a indexação de itens sob um `ChannelId` partilhado do Jellyfin, enquanto `GetCacheKey(userId)` altera apenas a cache do catálogo. Os IDs gerados por conta não garantem autorização por si.

## Correção experimental — **não instalar em produção**

O mesmo ramo foi alterado para usar **duas instâncias de `IChannel` com `ChannelId` distintos**, atribuídos em RAM a duas subscrições de teste. O login continua normal, sem painel, playlist ou novos DNS para o cliente. As rotas de auditoria experimentais tentam:

- abrir um item de A autenticado como B, e vice-versa;
- pedir `PlaybackInfo` de item estrangeiro com a conta oposta;
- falsificar `UserId` nas consultas de catálogo e reprodução;
- verificar que os dois catálogos continuam independentes e as fontes só são devolvidas ao dono.

**A prova depende de uma CI real completa com o commit final, não apenas da compilação.** Mesmo que passe, o limite de dois canais é apenas experimental; reutilização de slots, cache persistente, expiração/renovação, VOD/séries, metadados, EPG e testes físicos permanecem pendentes. A [PR #4](https://github.com/martaxi-boss/Vivoapk/pull/4) foi criada como **draft**: não deve ser incorporada nem instalada sem demonstração de isolamento.

## Auditoria Ferrari / REA — VPS

O Ferrari original e o REA privado foram executados pela ponte GitHub Actions → SSH → VPS na [ronda #45282452284](https://github.com/martaxi-boss/VPS/actions/runs/45282452284), com prova efetiva em ZIP e revisão independente finalizada na [#45283136535](https://github.com/martaxi-boss/VPS/actions/runs/45283136535):

- `jellyfin-pink.service`, `vivoapk-backend.service`, Nginx e PostgreSQL ativos;
- sockets Jellyfin e VivoAPK responderam HTTP 200;
- revisão do código fonte na VPS: `a2f00bb0eb0bd4ef2d695151238645cc73f110a8`;
- plugin Jellyfin instalado com `Enabled=true`, `PilotLiveChannels=true`, `ProvisionPrivateLibraries=false`, `MonitorEntitlements=false`;
- backend e VPN Pink antigos ausentes;
- sem acesso a palavras-passe, DNS de clientes nem vídeos, sem restart nem alterações na produção.

O fecho Ruflo certifica **uma execução REA e uma revisão de metadados**, com `quality_gate=not_certified`, **não** certifica catálogo multicliente ou imagem e som em aparelhos.

## Gate obrigatório

**Nunca** colocar um catálogo multicliente no Jellyfin sem reproduzir com duas contas independentes: listagem pessoal, negação de IDs cruzados, negação de PlaybackInfo estrangeiro, negação de `UserId` forjado, revogação e persistência segura dos metadados sem credenciais. Não sacrificar isolamento de contas para mostrar mais canais.

## Evolução verificada no servidor descartável — 23/11/2026, 22:55 UTC

- **Isolamento por ChannelId:** no ramo `experiment/jellyfin-multitenant-isolation-20261123`, o CI real [#45283509132](https://github.com/martaxi-boss/Vivoapk/actions/runs/45283509132) passou com **duas contas fictícias simultâneas**, `ChannelId` diferentes, negação de acesso a itens e `PlaybackInfo` estrangeiros, proteção contra `UserId` forjado, preservação do administrador, reprodução HLS sintética fora do Jellyfin e renovação da mesma conta.
- **Catálogo Live alargado:** a versão seguinte, ramo [experiment/jellyfin-full-live-catalog-20261123](https://github.com/martaxi-boss/Vivoapk/tree/experiment/jellyfin-full-live-catalog-20261123), usa o contrato privado já existente `POST /v1/catalog/list` para consultar até 20 000 IDs/nomes (limite de código, **não** tamanho testado), com limite de resposta de 8 MiB e deduplicação de IDs.
- **Configuração fail-closed:** `FullLiveCatalog=false` por defeito; o piloto antigo de até 5 canais não é alterado sem esta opção. O catálogo completo foi habilitado **apenas** no servidor CI descartável.
- **Prova positiva:** [#45284059159](https://github.com/martaxi-boss/Vivoapk/actions/runs/45284059159), com **seis canais sintéticos em cada uma das duas contas**, negação de IDs cruzados e `UserId` forjado, ausência de credenciais gravadas nos itens e HLS sintético com vídeo e áudio. A listagem com seis itens mostra que a limitação de cinco foi removida *nessa experiência*, não que os 20 mil itens funcionem em produção.
- **Redução de exposição:** os itens Jellyfin indexados guardam apenas IDs e títulos. O snapshot em RAM guarda as credenciais **uma única vez por conta** e o URL HLS direto é construído quando o proprietário pede `PlaybackInfo`.
- **PRs ainda não fundidas:** [#4](https://github.com/martaxi-boss/Vivoapk/pull/4) e [#5](https://github.com/martaxi-boss/Vivoapk/pull/5) mantidas como `draft`. Não há implementação dessas experiências na VPS.

### Pendências que impedem ativação para clientes

Aprovação de produção exige resolver **reserva durável de ChannelId por subscritor (para que um slot nunca seja reutilizado com itens antigos após expiração/reinício)**, recuperação automática de catálogo após expiração do snapshot sem pedir configuração/admin, teste do volume de catálogo real, filmes, séries, episódios e EPG, revogação real, e reprodução direta medida em Android/iOS/Windows/Samsung/LG. O teste de vídeo sintético por FFmpeg não equivale à reprodução nas aplicações físicas.

**Veredito:** está resolvida a fuga reproduzida no teste de dois clientes com canais separados; permanece **em desenvolvimento** a integração completa pedida no Bootstrap.
