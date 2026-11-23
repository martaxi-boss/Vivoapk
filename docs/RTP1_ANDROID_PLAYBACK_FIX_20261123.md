# VivoAPK — correção da reprodução RTP1 no Jellyfin Android

Data: 23/11/2026. Código instalado: [a2f00bb0](https://github.com/martaxi-boss/Vivoapk/commit/a2f00bb0eb0bd4ef2d695151238645cc73f110a8), também integrado em main.

**Estado:** defeito de negociação corrigido e instalado; RTP1 real descodificada com imagem não preta e áudio não silencioso. **Atualização após teste físico às 15:57: a aplicação Jellyfin Android 2.7.3 continua a falhar com «Source error». A correção de negociação abaixo foi validada, mas não resolveu a reprodução no dispositivo. A investigação continua pela ponte GitHub Actions → SSH → VPS.**

## Causa comprovada

O plugin anunciava `Container=m3u8` e `MediaStreams=[]`. O manifesto HLS real transporta segmentos MPEG-TS com H.264/AAC. Ao enviar um perfil de dispositivos compatível com o cliente Android nativo, o próprio Jellyfin de produção devolvia os três métodos desativados: `SupportsDirectPlay=false`, `SupportsDirectStream=false`, `SupportsTranscoding=false`. Não existia um método que o leitor pudesse selecionar.

A mesma falha foi reproduzida num Jellyfin 12.4 descartável. O teste antigo enviava um corpo vazio ao PlaybackInfo: recebia DirectPlay=true e ocultava esta falha. Uma resposta HTTP 200 não provava reprodução.

A subscrição real autorizada e os segmentos do fornecedor funcionaram nos ensaios. A falha demonstrada está nos metadados do plugin e na negociação com o perfil do cliente; os ensaios não certificam eventuais problemas adicionais no dispositivo físico.

## Alteração mínima

- `VivoapkLivePreviewChannel.cs`: contentor dos segmentos descrito como MPEG-TS, pistas H.264 e AAC e atualização de DataVersion para invalidar metadados antigos. A URL continua HLS direta do fornecedor.
- `jellyfin_12_4_smoke.py`: perfil Android explícito e fornecedor HLS sintético separado do Jellyfin, com redirecionamento e descodificação da URL exata devolvida pelo servidor.

`MegaResolverClient.cs` e `MegaAuthenticationProvider.cs` foram inspecionados e conservados. Não foram introduzidos serviços, APKs, Pinky, listas globais, armazenamento de conteúdos, proxy ou transcoding de produção.

Os descritores correspondem ao perfil observado da RTP1 no piloto atual. Não constituem deteção automática universal de codecs de outros canais.

## Provas verificadas

| Ensaio | Resultado | Evidência |
|---|---|---|
| Código anterior com perfil Android em Jellyfin descartável | FALHA esperada: todos os métodos desativados | [CI 45203744361](https://github.com/martaxi-boss/Vivoapk/actions/runs/45203744361) |
| PlaybackInfo de produção antes da correção | HTTP 200, mas DirectPlay=false com perfil Android; sem pistas no pedido sem perfil | [REA 45204957356](https://github.com/martaxi-boss/VPS/actions/runs/45204957356) |
| Compilação e testes .NET corrigidos | PASS | [CI 45204432970](https://github.com/martaxi-boss/Vivoapk/actions/runs/45204432970) |
| Jellyfin descartável corrigido | PASS: DirectPlay=true; URL direta, 49 fotogramas de vídeo e 94 blocos de áudio descodificados; isolamento e administrador preservados | [CI 45204432932](https://github.com/martaxi-boss/Vivoapk/actions/runs/45204432932) |
| RTP1 real antes da instalação | PASS: aproximadamente 8 s, 397 fotogramas de saída e áudio descodificado | [REA 45204563814](https://github.com/martaxi-boss/VPS/actions/runs/45204563814) |
| Estado real após erro do invólucro de instalação | Código e DLL corretos, backup presente e serviços ativos | [REA 45205770024](https://github.com/martaxi-boss/VPS/actions/runs/45205770024) |
| Verificação final da produção | PASS para reprodução, perfil Android, binário, rollback, HTTPS e serviços | [REA/Ruflo 45206133426](https://github.com/martaxi-boss/VPS/actions/runs/45206133426) |
| Revisão de papel distinto, com SHA-256 da evidência | Aprovação com limites explícitos | [Ruflo 45206407393](https://github.com/martaxi-boss/VPS/actions/runs/45206407393) |

A prova final descodificou cerca de 8 s: **398 fotogramas de saída**, 407 analisados pelo filtro, **0–10% de píxeis pretos** e áudio médio **−26,0 dB**, máximo −9,9 dB. Vídeo H.264 High 1280×720/50 fps; áudio AAC-LC estéreo 48 kHz. A análise produziu apenas métricas e saída de descarte, sem guardar conteúdos.

O PlaybackInfo corrigido de produção contém MPEG-TS e as pistas H.264/AAC; com perfil Android, DirectPlay=true, SupportsTranscoding=false e nenhuma URL de transcoding. O Jellyfin pode também marcar SupportsDirectStream=true durante a negociação de DirectPlay; o cliente nativo seleciona DirectPlay e a URL aponta ao fornecedor. Não foi adicionado um encaminhador para satisfazer esse indicador.

A URL devolvida foi comparada em memória com a URL autorizada do fornecedor: igualdade confirmada, sem publicação do endereço. Jellyfin mantém PrivateNetwork=yes. O teste de descodificação foi um diagnóstico direto do Ferrari; não foi uma retransmissão pelo Jellyfin nem uma medição de tráfego de um Android físico.

SHA-256 da evidência final `artifacts/evidence-1.json`:
`16464cd29db0af269ee85a2d4dc7c560b770120f07e4f4af1d61978fc8de2cb0`.
ZIPs testados quanto a integridade; `report.json.status=completed`, `FERRARI_SKILL_JOB_STATUS=completed` e `FERRARI_SKILL_END_TO_END=PASS` confirmados. O gate genérico do motor não equivale a certificação de dispositivos.

## Instalação, testes e falhas

O instalador existente criou backup em `/srv/projects/Vivoapk/backups/live-channel-pilot/a2f00bb0eb0bd4ef2d69`, com DLL, XML e marcador da revisão anterior. O invólucro [45205243351](https://github.com/martaxi-boss/VPS/actions/runs/45205243351) terminou com erro REA, apesar de a atualização ter sido aplicada. Não se repetiu a instalação: duas verificações posteriores confirmaram o código exato, igualdade do DLL compilado/instalado e backup.

Jellyfin, backend e Nginx ativos; HTTPS com validação TLS e HTTP 200; socket do backend 0660. Autenticação e catálogo de cinco canais responderam corretamente.

Os **143 testes do backend passaram no ambiente de trabalho**. Os **24 testes de infraestrutura passaram na VPS**. A tentativa inicial conjunta no ambiente de trabalho teve duas falhas de permissão ao criar sockets Unix; esses testes passaram depois na VPS. A tentativa de pytest na VPS foi bloqueada por ausência de pytest nesse ambiente; não foi instalada uma dependência de testes no serviço de produção.

Sondas preliminares interrompidas por subscrições indisponíveis, opções de diagnóstico FFmpeg ou cabeçalhos do cliente de teste não foram tratadas como prova de reprodução. As conclusões assentam nos ensaios finais acima.

## Limite físico

Não havia ligação remota disponível ao Android físico. Falta **abrir a RTP1 na aplicação oficial já instalada e observar imagem e som durante pelo menos 30 segundos**. Regressar à lista e abrir o canal permite testar os metadados atuais; não se propõe terminar e voltar a iniciar sessão como solução.

O piloto existente conserva uma subscrição de cada vez e snapshot efémero de 30 minutos. Não há certificação de catálogo completo, outros canais, iOS ou Smart TV nesta correção.
