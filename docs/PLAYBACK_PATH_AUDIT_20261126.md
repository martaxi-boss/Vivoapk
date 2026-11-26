# VivoAPK — percurso de reprodução e bloqueio observado (26/11/2026)

Estado: investigação executada; reprodução real NÃO certificada. Produção preservada em `0994990c024b159eebd44528041746f557596d27`; código inspecionado do candidato `e4746d1b9723ddea213957674ba3a9b91bff5ce0`. Não promover main nem o candidato.

## Prova nova, executada pela ponte existente

[Ferrari/REA/Ruflo r112](https://github.com/martaxi-boss/VPS/actions/runs/45867836029), pedido VPS `0da06c74d37eec3f2930f3dddd06a171abd44d97`, artefacto `15141750499`. ZIP exterior e interior com CRC íntegro, report.status=completed, evidência SHA-256 `7051af7f1a023725e018ee8fb099e52e671a0080a822c11c6428fe89e12b0214`. Processo de leitura apenas, com API oficial e autenticação Xtream existente. Nenhum pedido de media, mudança de subscrição, serviço, DNS ou transporte. Credenciais usadas somente em memória na VPS, sem saída bruta do fornecedor.

| Verificação atual | Geral | Samsung/LG |
|---|---|---|
| Autenticação de metadados | HTTP 200, ativa | HTTP 200, ativa |
| allowed_output_formats reconhecido | presente | presente |
| MPEG-TS anunciado | sim | sim |
| HLS anunciado | sim | sim |
| Ligações ocupadas / máximo | 0 / 2 | 0 / 2 |

Identidade da subscrição e validade verificadas na API oficial; seleção de origem igual ao mapping já existente. Quatro serviços com o mesmo estado e PID antes/depois. Estas são observações atuais, NÃO prova de ocupação no instante r108 nem autorização efetiva para abrir media.

## Onde ocorreu o 401

[Artefacto r108](https://github.com/martaxi-boss/VPS/actions/runs/45829120343/artifacts/15120633762) reaberto, CRCs íntegros. O código de captura observado é `PrivateHlsSession._fetch`, dentro de `httpcore.ConnectionPool.stream`:

1. API Mega e autenticação Xtream passam; RTP1 pertence ao catálogo da linha.
2. GET inicial da origem atribuída, para a entrada HLS composta pela integração.
3. Resposta upstream **HTTP 401**, nas duas famílias. Nenhum redirecionamento seguido.
4. A leitura é recusada antes do parser de manifesto/segmentos.
5. Nenhum FFprobe/FFmpeg de descodificação executado para essa media; zero frames e zero processo residual.

Logo, o 401 observado não foi gerado pelo parser MPEG-TS, por um codec incompatível ou pela aplicação Jellyfin. Isso localiza a recusa na resposta da origem; **não identifica o componente interno do fornecedor nem a razão da recusa**. Nenhum corpo/URL/cabeçalho privado foi guardado pelo relatório. Os metadados r112 anunciam ambos os formatos: não sustenta afirmar que a linha exclui HLS ou que mudar a extensão resolve o 401. Não realizar fallback TS/legacy, alteração de User-Agent, DNS, túnel ou rota como reação à recusa.

## Percurso confirmado no código

| Etapa | Implementação e efeito |
|---|---|
| PlaybackInfo nativo | Jellyfin v12.4 MediaInfoHelper.GetPlaybackInfo → ResolvePlaybackMediaSources → MediaSourceManager.GetPlaybackMediaSources |
| Canal privado | ChannelManager.GetDynamicMediaSources → IRequiresMediaInfoCallback; VivoapkLivePreviewChannel autoriza owner e item |
| Entrada de produção | NativePrivatePlaybackMode.CreateSourceAsync → PrivateNativeHls.BindProbedSourceAsync |
| Admissão atual LIVE | PrivateNativeHls.BindSourceAsync → PrivateHlsLoopback.OpenAsync → PrivateHlsSocketClient.OpenAsync → backend /_internal/media/hls/open |
| Contrato da origem | CatalogService.authorize_media_origin exige m3u8 para LIVE; AuthorizedMediaOrigin e PrivateHlsSession exigem o mesmo contrato |
| Leitura privada | Manifesto HLS claro muxado e segmentos TS na origem autorizada; grants por sessão, entitlement, DNS/IP público fixado, limites e recusa de redirects |
| Metadados | PrivateHlsLoopback.ProbeAsync → FFprobe sobre capability local; observação tipada, sem URL/credenciais externas no processo |
| Reprodução e saída | MediaSourceInfo com codecs observados → negociação do perfil Jellyfin → FFmpeg nativo → saída HLS/HTTPS da sessão ao cliente oficial |

Fontes upstream exatas: [MediaInfoHelper](https://github.com/jellyfin/jellyfin/blob/v12.4/Jellyfin.Api/Helpers/MediaInfoHelper.cs), [ChannelManager](https://github.com/jellyfin/jellyfin/blob/v12.4/src/Jellyfin.LiveTv/Channels/ChannelManager.cs). A cache dinâmica por ExternalId dura cinco minutos no core; a integração existente faz a vinculação/retirada exata da lease. Não alterar esse isolamento para adicionar um formato.

## HLS e MPEG-TS: o que falta realmente

HLS é um manifesto que referencia segmentos; os segmentos podem ser MPEG-TS. MPEG-TS contínuo é um corpo de resposta único e aberto: não existe manifesto nem sequência de URLs de segmentos. Mudar o demuxer ou a extensão da URL não implementa esse transporte.

O candidato tem `PrivateMediaProbe.ReadContinuousTsAsync`, aceita apenas a capability local admitida /stream.ts, observa MPEG-TS e duração infinita. Esse método **não é chamado pelo caminho LIVE atual**. `PrivateHlsLoopback.OpenAsync` só emite /index.m3u8, e o backend só admite HLS nessa rota.

Para uma fonte MPEG-TS explicitamente autorizada, a integração deve estender o adaptador privado existente, sem outro motor: contrato de entrada tipado na admissão, autorização do formato/origem antes de abrir, leitura contínua limitada com backpressure e cancelamento, lease e revalidação por conta, capability /stream.ts protegida e probe TS, seguida do MediaSourceInfo e FFmpeg nativos já existentes. O ciclo probe→reprodução deve ter semântica definida de consumo/reabertura, sem duplicar silenciosamente ligações upstream nem misturar dados entre clientes. Recusa 401/403 encerra a sessão e não escolhe outro transporte. Esta integração **continua por implementar/validar**, condicionada ao contrato de media autorizado; não é apresentada como correção instalada.

## Comparação com o PINK histórico

[Prova real #37395113639](https://github.com/martaxi-boss/VPS/actions/runs/37395113639/job/112049200278) executou teste Android de vídeo/áudio com Media3/ExoPlayer e túnel WireGuard, conta e catálogo resolvidos no runtime PINK. A fonte histórica [PinkVpnLiveTest.kt](https://github.com/martaxi-boss/pink-iptv/blob/9e1fc82c9c964a079d876671990a41a73689955e/clients/extreme/overlay/vpn-tests/PinkVpnLiveTest.kt) confirma o túnel e as verificações de buffers descodificados. O teste percorre até três itens e opções m3u8/ts e não identifica nos marcadores de sucesso qual delas venceu; não afirmar que a prova confirma exclusivamente TS.

O [XtreamPlaybackUrlFactory](https://github.com/martaxi-boss/pink-iptv/blob/9e1fc82c9c964a079d876671990a41a73689955e/android/app/src/main/java/com/pinkiptv/app/player/XtreamPlaybackUrlFactory.kt) de outro caminho PINK constrói live .ts. Não confundir esse caminho com o teste Extreme/Tauri acima. O [diagnóstico histórico 037/038/039](https://github.com/martaxi-boss/pink-iptv/blob/9e1fc82c9c964a079d876671990a41a73689955e/docs/ANDROID_DEVICE_REMEDIATION_037.md) registou recusas 401 também com TS e HLS apesar de conta ativa e formatos anunciados. Nenhum fallback histórico é autorização para o reproduzir agora.

## Validação preservada e limite de conclusão

[CI do candidato #45847563775](https://github.com/martaxi-boss/Vivoapk/actions/runs/45847563775) consultada, sem repetir: success, COMBINED_LAB_GATE_EXIT=0; imagem/áudio de filmes/episódios, seek, isolamento, recuperação de sessões, emissão infinita e cache aprovados com fixtures. [Documentação oficial de codecs](https://jellyfin.org/docs/general/clients/codec-support/) distingue contentor, codecs e capacidades dos clientes. Nenhum desses resultados certifica o acesso ao fornecedor nem aparelhos físicos.

Android, Android TV, iOS, Windows, Samsung e LG: **zero plataformas certificadas com esta origem real**. Filmes/séries reais, continuidade real, simultaneidade real e revogação real durante reprodução permanecem pendentes.

## Bloqueio externo concreto a esclarecer

A [API pública Mega](https://megaott.net/docs/subscriptions) documenta gestão e DNS da subscrição; não esclarece a recusa observada da rota de media. É necessária resposta do fornecedor, pelo canal oficial do proprietário: a linha ativa anuncia TS e HLS e tem capacidade livre na consulta atual, mas o GET HLS do canal autorizado a partir da VPS foi recusado com 401. Confirmar se a arquitetura Jellyfin/FFmpeg na VPS é permitida para esta linha, a interface/formato de reprodução oficialmente aplicável e eventuais condições de origem/IP/cliente/ligação. Não enviar passwords, tokens ou URLs com credenciais na descrição pública. Não foi enviada mensagem ao fornecedor nesta tarefa.

Não há correção de produção nem reprodução real certificada. A investigação avançou com metadados atuais e localização exata da recusa; o motivo interno do 401 depende do fornecedor.
