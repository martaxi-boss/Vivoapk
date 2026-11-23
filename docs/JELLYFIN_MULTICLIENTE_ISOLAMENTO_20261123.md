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

## Atualização: leitura por categorias e ponte nativa Jellyfin

- Backend oficial de desenvolvimento: [PR #6](https://github.com/martaxi-boss/Vivoapk/pull/6) integrada no `main` (commit `58693551ef3515edaa012f47eb29b74eda019947`). Os novos endpoints privados `/v1/catalog/categories` e `/v1/catalog/category-items` estão protegidos por JWT de subscrição, verificação de pertença da categoria, limites de resposta e sanitização. [CI backend #45286517471](https://github.com/martaxi-boss/Vivoapk/actions/runs/45286517471) PASS.
- [Ferrari/REA #45287110504](https://github.com/martaxi-boss/VPS/actions/runs/45287110504): no **servidor instalado**, backend ainda na revisão `a2f00bb0eb0bd4ef2d695151238645cc73f110a8`; ambos endpoints novos devolvem HTTP 404, embora VivoAPK e Jellyfin estejam ativos. Auditoria apenas de leitura, sem divulgar credenciais. **O merge do GitHub não corresponde a deploy na VPS.**
- [PR #7, ainda draft](https://github.com/martaxi-boss/Vivoapk/pull/7): integração experimental na aplicação **oficial Jellyfin**, com `CategoryNavigation=false` por defeito. Pastas nativas para categorias de TV, depois carregamento de canais apenas quando a pasta é aberta; token privado em RAM renovável a partir da autenticação Mega existente. Mantém ChannelIds distintos por cliente e media HLS direto, não cria M3U global nem proxy VPS.
- A compilação da primeira versão passou em [CI #45287538165](https://github.com/martaxi-boss/Vivoapk/actions/runs/45287538165). O primeiro ensaio de pastas [#45287779518](https://github.com/martaxi-boss/Vivoapk/actions/runs/45287779518) identificou uma divergência no teste que esperava ordem fixa de categorias; a correção está no commit `151ed0920a1e6e23e580ec298dbc27498ee5a060`. **Aguardar resultado do ensaio seguinte antes de afirmar integração de pastas funcional.**
- Fora do âmbito deste ensaio: catálogo por categorias de filmes/séries, EPG, e compatibilidade real em Android/iOS/Windows/Tizen/webOS. O piloto de produção não foi alterado.

## Certificação posterior — navegação TV ao vivo por categorias (23/11/2026 23:31 UTC)

- **Prova Jellyfin real descartável:** [CI #45289368197](https://github.com/martaxi-boss/Vivoapk/actions/runs/45289368197) **PASS**, nas duas execuções de teste, incluindo `JELLYFIN_SYNTHETIC_NO_EAGER_GLOBAL_CHANNEL_DOWNLOAD=PASS`, `JELLYFIN_SYNTHETIC_LAZY_FETCH_ONLY_ON_FOLDER_OPEN=PASS`, `JELLYFIN_SYNTHETIC_NATIVE_CATEGORY_FOLDERS_A/B=PASS`, `JELLYFIN_SYNTHETIC_CATEGORY_FOLDER_ISOLATION=PASS`, `JELLYFIN_SYNTHETIC_USERID_SPOOFING_DENIED=PASS`, `JELLYFIN_SYNTHETIC_RESTART_CHANNEL_OWNER_STABLE=PASS`, `JELLYFIN_PILOT_NO_CREDENTIALS_PERSISTED=PASS`, `JELLYFIN_RETURNED_HLS_VIDEO_AUDIO_DIRECT=PASS` e preservação da conta administrativa local.
- O percurso nativo de teste passa a ser **Canal VivoAPK do cliente → TV ao vivo → Categoria → canais**. O nó adicional `TV ao vivo` impede que o comando nativo Jellyfin `Channels/Items/Latest` descarregue todos os itens de cada categoria automaticamente. Só os metadados de categorias são indexados na navegação inicial.
- **Sem limite acumulativo de 20k itens em RAM:** o código da experiência passou a derivar a origem HLS direta a partir de ID de media codificado por categoria, com validação de pertença à lista autorizada. Não guarda lista global de 28 841 streams no plugin. O limite de segurança do backend por **resposta de categoria** continua a existir.
- **Fica experimental:** [PR #7 (draft)](https://github.com/martaxi-boss/Vivoapk/pull/7); `CategoryNavigation=false` por defeito; não foi incorporada em `main` nem instalada na VPS. A prova utiliza 2 subscrições artificiais e 6 canais em cada categoria, não o catálogo completo real, filmes, episódios, EPG ou aplicações físicas. A revalidação de subscrições para sessões longas, seleção de DNS com vários dispositivos do mesmo cliente e escalabilidade além dos 2 slots continuam por resolver.
- **A VPS continua em `a2f00bb0` na auditoria REA #45287110504**, com novas rotas backend HTTP 404 até deploy protegido. Não alterar a VPS ou expor um catálogo multicliente sem fechar os gates acima.
