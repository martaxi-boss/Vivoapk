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
