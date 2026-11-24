# BOOTSTRAP — VivoAPK / Jellyfin / Mega OTT

**Regra vinculativa do produto, confirmada pelo proprietário em 23/11/2026.**
Este documento é a fonte de verdade do funcionamento pretendido para clientes. Ler antes de qualquer alteração ao VivoAPK, ao plugin Jellyfin, aos DNS ou à VPS. O histórico de experiências com Pink IPTV e Vivo Player não altera este contrato.

## Objetivo único: entrada automática dos clientes no Jellyfin

O produto utiliza **a aplicação oficial Jellyfin já existente** em Android, Android TV, iOS, Windows e Smart TV compatível (Samsung Tizen / LG webOS, quando suportadas), em vez da antiga app Pink IPTV. O cliente **não instala o Pink**, não precisa de APK nosso, painel, conta de administração ou intervenção manual do revendedor.

O cliente introduz **apenas**:

1. **Servidor Jellyfin:** `https://serveriptv.duckdns.org`.
2. **Utilizador:** o username da sua subscrição Mega OTT ativa e autorizada.
3. **Password:** a password dessa mesma subscrição Mega OTT.

Fluxo obrigatório:

```text
Aplicação Jellyfin oficial
    → URL público único: https://serveriptv.duckdns.org
    → login normal do Jellyfin com username/password Mega OTT
    → plugin nativo VivoAPK do Jellyfin na VPS
    → backend privado VivoAPK valida a própria subscrição via API Mega OTT + Xtream
    → encontra o DNS oficial completo atribuído pela Mega à subscrição
    → cria/associa automaticamente a identidade restrita Jellyfin desse cliente
    → disponibiliza apenas o catálogo da subscrição (TV / filmes / séries / episódios / EPG)
    → o dispositivo reproduz do fornecedor diretamente, quando tecnicamente compatível
```

**A VPS é o ponto de autenticação, controlo e apresentação de metadados Jellyfin; não deve armazenar vídeos nem retransmitir, converter, remultiplexar ou transcodificar media para os clientes.** Não introduzir proxy/relay de vídeo como solução automática. Se determinada app só funcionar com relay, reportar essa limitação e não declarar conformidade.

## Gestão exclusiva pelo painel Mega OTT

- **O painel Mega OTT do proprietário é o único painel de gestão de subscrições/clientes.** A aplicação Jellyfin do cliente não deve pedir configurações administrativas.
- O proprietário gere ativações, expirações, DNS e subscrições no próprio painel Mega OTT; o VivoAPK sincroniza/valida automaticamente o estado relevante. Não criar painel adicional de gestão de clientes nem exigir ao proprietário criar manualmente utilizadores Jellyfin.
- O plugin interno utiliza permissões técnicas estritamente necessárias para integrar com o Jellyfin, invisíveis ao cliente. **Nunca solicitar ao cliente credenciais administrativas do Jellyfin**.
- Não pedir ao cliente endereço Xtream, segundo DNS, M3U, playlist, MAC/Device Key, operador ou outra aplicação. O cliente vê apenas o DNS Jellyfin único mais o seu login Mega OTT.

## DNS e protocolo: usar o que já foi fornecido

- **URL visível ao cliente:** `https://serveriptv.duckdns.org`, que é a entrada HTTPS do Jellyfin, não o endereço de media Mega OTT.
- O backend usa as credenciais de API **já existentes no cofre privado da VPS**, não as solicita novamente nem as copia para o GitHub.
- Selecionar os **URLs completos atribuídos à própria subscrição** pela Mega OTT; não inventar ou substituir subdomínios, não tratar a lista de bases DNS como uma whitelist fixa.
- O backend existente suporta `dns_link` para Android, Android TV, Windows e, sujeito a validação, iOS; preserva `dns_link_for_samsung_lg` para Samsung Tizen e LG webOS quando essa origem se aplica ao cliente. A aplicação Jellyfin pode não identificar a plataforma como um leitor Xtream nativo: a seleção e a compatibilidade reais exigem validação.
- As várias bases DNS fornecidas servem **para selecionar a origem correta de cada subscrição**, não para pedir mais endereços ao cliente e não para redirecionar o streaming pela VPS.
- A aplicação fala **protocolo Jellyfin** com o servidor; a API Mega OTT / Xtream é usada privadamente pelo VivoAPK para autenticação, autorização e catálogo. Um simples apontamento DNS não converte Xtream automaticamente para o protocolo Jellyfin: **essa tradução pertence ao plugin do servidor**.

## Requisitos funcionais obrigatórios

- Login de cliente automático com username/password Mega OTT (sem segundo registo), sem tocar na conta administrativa já existente do próprio Jellyfin.
- Catálogo automático e **completo para cada subscrição**, com TV, filmes, séries, episódios e guia EPG dentro do que a Mega OTT autoriza e a plataforma suporta.
- Múltiplos clientes simultâneos e isolamento total: nunca revelar itens, IDs reproduzíveis, origens ou credenciais de uma subscrição a outra.
- Renovação, expiração e revogação reconhecidas a partir da Mega OTT sem gestão manual duplicada.
- A reprodução precisa de funcionar nos clientes Jellyfin de destino; testar login, catálogo e imagem+som em plataformas reais. DirectPlay no JSON não é prova de playback ou de ausência de tráfego de vídeo pela VPS.
- Não introduzir M3U global, tuner partilhado, ficheiros STRM com credenciais, palavras-passe em logs/URL pública ou bases de dados acessíveis a outros clientes.
- Não modificar apps comerciais, contornar DRM/licenças nem supor compatibilidade física sem testes. Reutilizar o código autorizado do antigo Pink apenas como referência de integração e não reinstalar o Pink.

## Infraestrutura e execução

- Projeto: `martaxi-boss/Vivoapk`. Ferramentas: Skill `@motor-universal-linux` (Ferrari + REA + Ruflo) **pela ponte existente GitHub Actions → SSH → VPS**, sem SSH alternativo, nova Skill, novo servidor ou dependências globais.
- Reutilizar o que está efetivamente implementado no backend VivoAPK; uma nova solução incompatível com a integração existente não deve ser criada em paralelo.
- O serviço `jellyfin-pink.service` é o **nome herdado do serviço Jellyfin ativo**, não um pedido para voltar a instalar Pink IPTV. Nunca apagar o Jellyfin por causa do nome.
- Se existirem componentes antigos do Pink ainda ativos, a remoção definitiva é uma operação separada: inventariar consumidores e backups e nunca destruir serviços ou dados que a integração Jellyfin ainda use. O objetivo final é **não depender do Pink IPTV nem ocupar a VPS com o Pink**.

## Estado comprovado versus objetivo — 24/11/2026 (revisto após ativação)

**Instalação efetiva na VPS:** backend VivoAPK permanece na revisão `74b6a02be199a29c305058d2875063eb46c1b61d`; plugin Jellyfin instalado a partir da revisão da `main` `b73bfb7e46761b7bdfa70765f3175c55360929a1`. O binário compilado e instalado coincide com o estágio protegido, cujo SHA-256 é `9f5e0d1746d8ba77411a48860abbaaa3a3d670954215da7f40ec3ba432402272`. Provas Ferrari/REA: [estágio #45320359949](https://github.com/martaxi-boss/VPS/actions/runs/45320359949) com `status=staged`; [ativação #45320503468](https://github.com/martaxi-boss/VPS/actions/runs/45320503468) com `status=plugin_activated`; [verificação independente #45320636863](https://github.com/martaxi-boss/VPS/actions/runs/45320636863) com `status=verified`, integridade da DLL e Jellyfin saudável. O instalador confirmou `admin_preserved=true`, `backend_untouched=true`, `stream_proxy_installed=false`, `pink_modified=false`. Backup da DLL/XML anteriores foi criado pelo mecanismo de upgrade, com rollback previsto; não houve alteração do serviço antigo Pink IPTV.

**Verificação pública pós-instalação sem credenciais:** [Ferrari/REA #45320895824](https://github.com/martaxi-boss/VPS/actions/runs/45320895824) devolveu `status=passed`: HTTPS público válido; Jellyfin 12.4.0, backend VivoAPK, Nginx e PostgreSQL ativos; DLL instalada com SHA-256 da release; ficheiros de backup DLL/XML existentes; configuração com navegação por categorias ativa; backend original preservado. Não foram consultadas credenciais nem descarregados conteúdos multimédia.

**Modelo único e multiplataforma atualmente implementado:** `CategoryNavigation=true` expõe três raízes nativas de **TV ao vivo, Filmes e Séries**, com categorias, temporadas e episódios sob consulta. O plugin usa `IChannel` do Jellyfin, não um tuner Live TV nem o EPG/guia nativo. Há **64 reservas lógicas de subscrição**, não clientes ilimitados; para manter origens distintas, cada reserva possui dois `IChannel` técnicos internos (geral e Samsung/LG), até 128 canais internos. A aplicação Jellyfin usa o mesmo servidor e as mesmas credenciais Mega OTT em Android, Android TV, iOS, Windows, Tizen e webOS. A origem é selecionada pelo perfil identificado nos pedidos; os IDs das fontes de media incluem o slot da subscrição e a família de origem para impedir colisões na cache global do Jellyfin (5 minutos). Não foi criada app própria, playlist M3U global, painel adicional ou retransmissão via VPS.

**Testes de integração efetivos:** [Jellyfin 12.4 descartável #45319868489](https://github.com/martaxi-boss/Vivoapk/actions/runs/45319868489) e [#45319972534](https://github.com/martaxi-boss/Vivoapk/actions/runs/45319972534) / [#45319978728](https://github.com/martaxi-boss/Vivoapk/actions/runs/45319978728) concluíram com sucesso: duas contas fictícias com stream IDs coincidentes, negação de acessos cruzados, fontes separadas, identidade e catálogo após reinício, navegação por filmes/séries/episódios, renovação sintética e administração preservada. O mesmo cliente fictício foi testado com os seis perfis e origens distintas, com a família correta selecionada em `PlaybackInfo`. O vídeo HLS e MP4 sintético foi descodificado diretamente do fornecedor **de teste** com imagem e áudio, sem passar pelo Jellyfin. Esta prova de CI não equivale a reproduzir canais reais nos aparelhos do cliente.

**Linha Mega OTT real:** antes da instalação desta revisão, a autenticação HTTP 200 no Jellyfin e a reserva de um canal tinham sido verificadas em [#45296571291](https://github.com/martaxi-boss/VPS/actions/runs/45296571291). Os seis perfis de autenticação e catálogo do backend passaram no [#45300818198](https://github.com/martaxi-boss/VPS/actions/runs/45300818198); a verificação do Jellyfin anterior à correção [#45301215945](https://github.com/martaxi-boss/VPS/actions/runs/45301215945) revelou a seleção errada da origem para Samsung/LG, motivando esta release. **O login e o `PlaybackInfo` da linha real ainda não foram repetidos após o upgrade; não apresentar a CI como essa prova.** Nunca publicar username/password/token da linha no GitHub ou em logs.

**Limitações e trabalhos ainda NÃO certificados:** reprodução com imagem e som nas apps reais Android, Android TV, iOS, Windows, Samsung Tizen e LG webOS; apresentação real de EPG nativo no Jellyfin; prova física da ausência de tráfego media pela VPS; escalabilidade além das 64 reservas; gestão real de expiração e renovação em sessões longas. No teste de conectividade [#45300671229](https://github.com/martaxi-boss/VPS/actions/runs/45300671229), as duas origens de media atribuídas à linha real eram HTTP e não aceitavam HTTPS normal na porta 443. Isto pode impedir Direct Play em navegadores e algumas Smart TVs por conteúdo misto, mas não demonstra, sozinho, a causa de falhas nos clientes. Não inventar um DNS HTTPS nem criar proxy/relay em violação do contrato. O último teste físico anterior tinha reportado `Source error` no Android, e não foi repetido depois da release.

**Regra de honestidade:** a correção **está instalada e o servidor está verificado**, mas a compatibilidade universal e o playback físico ainda não estão certificados. Não criar novo painel, APK, proxy, modificação DRM ou reinstalação Pink para esconder lacunas.

## Falha física reproduzida em 24/11/2026 — diagnóstico posterior ao upgrade

**Evidência do proprietário às 08:17 (hora de Portugal):** a aplicação Jellyfin entra na biblioteca «VivoAPK — Conteúdos 1», apresenta raízes TV ao vivo/Filmes/Séries, categorias «EU | PORTUGAL ...» e 55 itens de uma categoria portuguesa. O canal «PT | A BOLA TV FHD» aparece no ecrã de reprodução, mas fica a preto e devolve «Source error» a 00:00. A captura é de uma aplicação Android; o objetivo do produto mantém-se **Android, Android TV, iOS, Windows, Samsung Tizen e LG webOS**, e este resultado NÃO certifica as outras plataformas.

**Ferrari/REA depois da captura:** [#45355365080](https://github.com/martaxi-boss/VPS/actions/runs/45355365080), pedido `vivoapk-postdeploy-source-error-20261124-r38`, executou auditoria **só de metadados**: `jellyfin-pink.service` ativo, journal disponível, 35 linhas desde 07:30 UTC, zero exceções, zero `PlaybackInfo` e zero `source error` nas linhas observadas. Ausência destas mensagens no journal **não prova** que o cliente recebeu um manifesto válido, nem que chegou ao CDN; o erro pode ocorrer inteiramente no dispositivo.

**Origem real, provas prévias:** [Ferrari/REA #45180196872](https://github.com/martaxi-boss/VPS/actions/runs/45180196872), para uma linha real autorizada, comprovou que os formatos `/live/.../id.ts` e `/live/.../id.m3u8` do canal português RTP 1 deram primeiro `HTTP 302`, ambos para destino `http` de outro host. A [continuação #45181317350](https://github.com/martaxi-boss/VPS/actions/runs/45181317350) reportou `RequestError` nas tentativas de consultar o redirecionamento; **não valida nem invalida** o fluxo final, porque o limite de bytes da sondagem também pode gerar `RequestError`. O teste TLS [#45300671229](https://github.com/martaxi-boss/VPS/actions/runs/45300671229) constatou origens genérica e Smart TV com HTTP e sem HTTPS funcional na porta 443. Nunca fabricar endereço HTTPS nem passar vídeo pela VPS.

**Compatibilidade a confirmar:** no cliente oficial [jellyfin-android `QueueManager.kt`](https://github.com/jellyfin/jellyfin-android/blob/master/app/src/main/java/org/jellyfin/mobile/player/queue/QueueManager.kt), `MediaProtocol.HTTP` em `DIRECT_PLAY` é entregue ao leitor com `MimeTypes.APPLICATION_M3U8`. A extensão `.m3u8` da URL inicial NÃO demonstra que o redirecionamento final devolva um manifesto HLS com segmentos válidos. Clientes baseados em browser, se receberem HTTP a partir da sessão Jellyfin HTTPS, enfrentam ainda as restrições normais de *mixed content*. Não afirmar causa única sem resposta/erro real da origem final.

**Segurança do diagnóstico:** tentativa de nova sonda privada `vivoapk-rea-redirect-head-only-20261124-r39` foi **bloqueada pela ferramenta** antes de criar o pedido; NÃO executou, nem deve ser contornada por outra forma de transporte. As credenciais privadas não foram publicadas. Não mexer no Jellyfin em produção nem fazer downgrade com base em hipóteses; atualização desta secção é apenas documentação.

**Nova auditoria de acesso após a captura:** [Ferrari/REA #45357114166](https://github.com/martaxi-boss/VPS/actions/runs/45357114166), pedido `vivoapk-playback-nginx-20261124-r40`, confirmou em 200 linhas de Nginx (janela 07:50–08:59 UTC) **4 pedidos `PlaybackInfo` HTTP 200 e 3 pedidos Live TV HTTP 200**. O pedido ao Jellyfin está funcional; o resultado **não prova transferência nem descodificação de media do fornecedor**. O relatório não expôs contas, URLs ou credenciais. Não alterar a origem `dns_link` sem confirmação de HLS/HTTPS válido pelo próprio fornecedor; a API MegaOTT documenta endereços atribuídos por linha e não fornece prova de compatibilidade universal do Jellyfin.

**Corroboração dos pedidos de reprodução (mesma manhã):** [Ferrari/REA #45371776922](https://github.com/martaxi-boss/VPS/actions/runs/45371776922) examinou o log Nginx sem divulgar linhas nem credenciais. [Ferrari/REA #45371975320](https://github.com/martaxi-boss/VPS/actions/runs/45371975320) contou **um login `POST /Users/AuthenticateByName` HTTP 200 às 08:14 e quatro pedidos `POST .../PlaybackInfo` HTTP 200 às 08:16**, imediatamente antes da captura física «Source error» às 08:17. Esta prova elimina um erro HTTP de `PlaybackInfo` no servidor para esses pedidos e mostra que a causa remanescente está **a jusante da resposta de media** (acesso ao destino remoto, redirecionamento, formato HLS, codec, política do leitor ou dispositivo), não necessariamente no CDN. Nenhuma origem ou conta privada foi revelada; nenhuma media foi descarregada por estes testes. Não afirmar que o servidor, por si só, consegue corrigir uma origem HTTP sem HLS válido, nem sugerir relaying via VPS.

**Porta de conclusão em aberto:** validar a resposta final dos redirecionamentos da linha com autorização e dados de reprodução privados (código HTTP final, MIME, existência de HLS válido, HTTPS e acessibilidade pelo dispositivo), sem expor credenciais/media no GitHub; se o fornecedor só disponibilizar HTTP ou fluxo não-HLS, confirmar com a Mega OTT uma origem segura e compatível associada à própria linha ou reconhecer a incompatibilidade. Depois repetir reprodução com imagem/som em cada família oficial, mantendo media fora da VPS.

## Critério final de aceitação

Um cliente novo, sem instalação nem configuração adicional além do Jellyfin oficial, abre a app, introduz `https://serveriptv.duckdns.org`, username e password Mega OTT, vê **automaticamente só a sua subscrição**, reproduz conteúdos autorizados sem retransmissão pela VPS e perde/recupera acesso conforme o painel Mega OTT. Repetir com pelo menos duas contas diferentes e clientes de cada plataforma de destino.

**NÃO DESVIAR DESTE OBJETIVO PARA CORREÇÕES APENAS ANDROID, PAINÉIS NOVOS OU REINSTALAÇÃO DO PINK IPTV.**
