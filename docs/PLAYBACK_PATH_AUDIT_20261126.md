# VivoAPK — auditoria do percurso de reprodução em 26/11/2026

## Estado e prova atuais — 28/11/2026

A produção está em **8f7acfd0e3265e845a4413b6cbc5842106d439d0**, instalada pelo r254 [46091540692](https://github.com/martaxi-boss/VPS/actions/runs/46091540692), com transação committed e pós-verificação aprovada. Substitui12b do r201. As secções antigas abaixo conservam o estado histórico das suas datas. **Não instalar main.** A autenticação, a descoberta, as famílias DNS e o catálogo existentes foram preservados.

A reversão dos nove ficheiros com os bytes reais foi ensaiada no r253; só mudaram plugin e referência do backend, sete ficheiros permaneceram iguais. Chaves, administrador, serviços protegidos, núcleo Jellyfin e FFmpeg preservados. CI nativo final46088866872 passou. RTP2 também passou com o perfil DNS de TV no r250:53 autorizações na origem esperada,9segmentos/26,4s e1350 fotogramas/1269 blocos áudio descodificados. Perfil do servidor não certifica aparelho físico. Pós-instalação, filme, episódio e métrica real da cache estão em curso.

O percurso real é Mega OTT autorizado → leitor privado do backend existente → socket privado/bridge dentro do Jellyfin → FFprobe/FFmpeg nativo → HLS HTTPS protegido para as aplicações oficiais. O fornecedor entrega MPEG-TS contínuo; não precisa de oferecer HLS. É o Jellyfin que gera o HLS de saída. A origem e as credenciais do fornecedor ficam no servidor. O teletexto é identificado como faixa auxiliar; só vídeo/áudio é anunciado nesta integração.

| Prova | Resultado observado | Camada e correção |
|---|---|---|
| r178 | Mega API429,60/min,Retry-After28 | Consultas repetidas aos detalhes da subscrição; cacheRAM≤5s, sem dados obsoletos após recusa |
| r210/r212 | MP4 autorizado206; janela4MiB demora11,78s/8,4s; probe tinha8s | Orçamento finito35s/leitura e75s total; TV mantém orçamento existente |
| r239 | Em base descartável,89.ª leitura404 após429 interno; contadores180/180 | Orçamento local acumulado entre casos sintéticos; isolamento só entre cenários sem leases, nunca na produção |
| r245 | Origem302→200,open200/probe200; HEVC/AAC+dvb_teletext; PlaybackInfo404 | Rejeição local da faixa auxiliar pelo parser; não é401 do fornecedor nem falha de credenciais |
| r248 | A mesma fonte reproduz; saídaH264/AAC,1200frames vídeo/1125áudio,27,6s/8segmentos | Reconhecimento tipado e limitado de teletexto na entradaTS contínua; FFmpeg/Jellyfin existentes fazem descodificação/transcodificação |

**Prova real atual de TV:** [r248 #46089313058](https://github.com/martaxi-boss/VPS/actions/runs/46089313058), evidência `77ed38de8c63af6cdc3c73b5c1fc476359abed98c1060bc54a25c01413541b8e`, usa o plugin ordinário8f em instância isolada. A entrada contém HEVC1920×1080/AAC48kHz e teletexto; a saídaH264/AAC foi confirmada porFFprobe e descodificada. Primeiro segmento7,32s após iniciar a reprodução, com PlaybackInfo21,79s medido separadamente. Anónimos recusados,54validaçõesACTIVE, cacheTS0 apósStop e limpeza integral; produção preservada. Esta comparação antes/depois demonstra a incompatibilidade corrigida. Os testes antigos de TV180,1s em produção12b documentam outro estado da fonte e não substituem a revalidação da emissão atual.

**Filme/episódio reais:** r235/r237 no código04657f tiveram27s/26,8s de saída nativa, vídeo+áudio descodificados e cache0. O arranque finito ainda ronda34s, além da análise inicial. A entrega finita começa com512KiB e ajusta até4MiB segundo a duração medida; limites/autorização mantêm-se. A CI completa96f passou e o código de aplicação96f é igual a04657f; a correção8f altera apenas o tratamento comprovado da faixa auxiliar, com regressões adicionais. Exigir CI completa8f e instalação coordenada antes de certificar produção atual.

O PINK usava Media3 no dispositivo e selecionava vídeo/áudio de uma origem com faixas adicionais. O FFmpeg também consegue selecionar esses componentes; o bloqueio estava na validação excessiva da integração privada, não numa necessidade de outro leitor. Não criar APK, motor ou gestão paralela.

**Limites de certificação:** aplicações físicas0; isolamento de duas contas validado em laboratório, sem prova de duas contas reais nesta linha única. Cache progressiva em produção ainda não medida: r241 foi bloqueado no PlaybackInfo e não chegou a produzir segmentos. A configuração existente é true/20s e as provas apósStop são separadas. Higiene pública r240 confirmou TLS válido até20/02/2027, versão12.4, rotas privadas404, temporários ausentes e31,98GiB livres.

As secções seguintes são o registo histórico e mantêm os respetivos âmbitos.

## Reprodução real contínua aprovada em instância isolada — 27/11/2026

[r180 #46033551731](https://github.com/martaxi-boss/VPS/actions/runs/46033551731), artefacto `15216430128`, evidência `a4d4349ee2b2c20f35ecb3b95c58a9925f6f191d9c56de97b0d33354d65f6348`: RTP1 autorizado através de autenticação/catálogo/PlaybackInfo e saída HLS do Jellyfin 12.4; **67,1 s contínuos, 27 segmentos, segmento novo aos 65,07 s; 1650 frames de vídeo e 3095 de áudio descodificados; cache TS=0 após Stop; pedido anónimo recusado**. Origem MPEG-TS infinita H.264/AAC, convertida pelo motor nativo existente. Sem nova APK/leitor/serviço.

A falha de continuidade anterior ficou demonstrada em r178: API Mega HTTP429 com limite60/min e Retry-After28, causada pela repetição de consultas aos detalhes da subscrição. A correção limita a reutilização desses detalhes a **5 s em RAM, por subscrição/contexto**, mantendo verificações Xtream atuais, identidade/DNS/password/expiração e recusa sem dados obsoletos. Respeita Retry-After e não altera autenticação, catálogo ou seleção de DNS públicos.

O piloto usou backend `39f5cbffa2b890916663b5f0411fe8e92e85ec56` e plugin ordinário1e previamente verificado, com código C# idêntico, em namespace temporário privado; todo o resolver apontou ao backend candidato dentro desse namespace. É composição de teste, **não release instalada**. O código de aplicação é igual ao candidato `12b8c5ba6f4c64462adbb79d2f27501ade42de3d`; diferenças posteriores são formatação de testes/documentação. stdout normalizado da evidência r180 foi truncado a8190bytes após os campos explícitos de sucesso: não tratar esse registo como prova completa de todos os campos finais de limpeza. Próximos ensaios usam resumos compactos completos.

Produção continua em r100 `0994990c024b159eebd44528041746f557596d27`, com a reparação privada de monitor r168 preservada. CI backend do candidato12b passou (#46033689558); integração nativa completa ainda em curso. Pendentes: filme/episódio reais, artefacto exato aprovado, ensaio de rollback e instalação coordenada, validação real em produção e aplicações físicas. **Zero plataformas físicas certificadas**. PINK, backups, chaves permanentes e serviço `jellyfin-pink.service` preservados.

## Quota Mega demonstrada e correção privada candidata — 27/11/2026

**Prova real da saída nativa:** [r176 #46028807136](https://github.com/martaxi-boss/VPS/actions/runs/46028807136), evidência SHA-256 `033543dd0eed21d2d5829eaaa0d34cb985aafc8eb77f86819a09b97a68d1d895`: 16 segmentos HLS do Jellyfin entregues e amostra efetivamente descodificada com **122 frames vídeo e 235 áudio**. Não confundir esse PASS de saída real com continuidade: a transmissão terminou ao fim de~32s por UNAVAILABLE.

**Causa precisa [r178 #46029462824](https://github.com/martaxi-boss/VPS/actions/runs/46029462824):** artefacto 15213432862 íntegro, evidência SHA-256 `ec10c580df72148a26b825c9c345a8c851e977d41747a83e6d8eb6b7d46a796e`. A API Mega devolveu `X-RateLimit-Limit:60`, remaining51→0, depois **HTTP429 / Retry-After28**. As verificações repetidas nos pedidos HLS e janelas privadas consumiam a quota; `_authorize_mapping` traduzia essa falha em UNAVAILABLE e retirava o leitor. Não é o limite local180/2400 nem recusa401/403 de media. A resposta429 foi respeitada e a sessão terminou, sem alternativa/repetição.

Correção candidata no mesmo backend: detalhes Mega bem-sucedidos partilhados APENAS entre as verificações privadas, por no máximo5s e até à validade conhecida; estado Xtream, identidade, origem/credenciais, formatos e expiração continuam verificados pelos mecanismos existentes. Login, catálogo público e seleção de DNS não foram reconstruídos. Cache RAM limitada a64 subscrições, cópias isoladas, uma consulta concorrente por ID, sem persistir media/segredos nem reutilizar sucesso expirado após erro. Falhas explícitas não são cacheadas como sucesso; uma conta recusada não invalida outras. Retry-After numérico/data é preservado e a pausa suprime novas consultas privadas até ao prazo. A tarefa de limpeza já existente limpa também estes detalhes e o shutdown elimina-os; sem novo serviço ou motor.

Quinze testes locais passaram: quota60 com duas identidades, concorrência, isolamento, validade/expiração, ausência de fallback após erro, Retry-After e recusa de identidade. Os testes de API mantêm a verificação Xtream imediata e os fixtures agora rejeitam passwords antigas como um fornecedor real. CI completa e ensaio real da correção ainda PENDENTES. Para testar o backend candidato inteiro sem tocar na produção, o piloto usará namespace temporário de montagem privado, com o socket do candidato associado ao caminho fixo do resolver apenas nesse namespace; inode/socket/PIDs da produção terão de permanecer inalterados. Não remover chave permanente do monitor/cofre/backups. Produção de código r100 com correção de configuração r168 mantida.


## Confirmação de subscrição ligada em produção — 27/11/2026

[r164 #45978782285](https://github.com/martaxi-boss/VPS/actions/runs/45978782285) comprovou flags/ambiente/permissões/provider corretos, mas ausência da chave do monitor. [r166 #45979280996](https://github.com/martaxi-boss/VPS/actions/runs/45979280996) confirmou que também faltava no ambiente efetivo do backend e no cofre base. `NativePlaybackAuthorization` exige essa confirmação e recusava antes de chegar ao transporte de media. O erro de paths do piloto era outra lacuna, já corrigida; não explica sozinho todos os 404.

**Correção aplicada e validada [r168 #45979921026](https://github.com/martaxi-boss/VPS/actions/runs/45979921026):** artefacto 15187889960 íntegro, evidência SHA-256 `bb04770dd508afd7a72795eafb955f5458ba501993e66215d3ad9676ae15d485`. Nova chave interna independente, gerada e guardada exclusivamente na VPS: `ENTITLEMENT_MONITOR_KEY` em `/root/project-secrets/VIVOAPK/native-media.env`, ficheiro `/srv/projects/Vivoapk/run/entitlement-monitor.key` owner jellyfinpink/0400. Não publicar os valores. Chave de media existente e credenciais do fornecedor/clientes preservadas. Cofre/runtime coincidem, Jellyfin lê a chave e www-data é recusado; pedido sem chave/errada403; **subscrição da linha real200 ACTIVE**. Cinco testes reais do helper de transação/rollback passaram antes da execução. Backup privado e journal committed em `/srv/projects/Vivoapk/backups/monitor-bootstrap-20261127-r168`. Preservar estes ficheiros permanentes, sobretudo durante higiene/ensaios. Reiniciado apenas vivoapk-backend.service; código continua r100, PIDs de Jellyfin/nginx/postgresql inalterados. Ainda não houve ativação do candidato1e nem saída real Jellyfin certificada. Próximo gate: repetir piloto nativo exato com esta confirmação ativa.



## Estado atual — RTP1 real aceite e descodificada — 27/11/2026

A hipótese de bloqueio externo descrita no histórico de 26/11 foi superada pela investigação HTTP. Não pedir intervenção do fornecedor com base nesses ensaios antigos. Produção continua r100, sem ativação.

Diferenças demonstradas face ao PINK/Media3: o leitor privado omitia User-Agent e rejeitava todos os redirecionamentos. O candidato `12c7860c467b6d4758283fd7ab72493151e3bd72` passou a enviar a identificação verdadeira `Vivoapk/1.0`, já usada pela autenticação e catálogo, sem imitar PINK ou navegador. [r141](https://github.com/martaxi-boss/VPS/actions/runs/45972113080) observou **302**, anteriormente 401; terminou antes de seguir porque o transporte ainda o recusava. Não prova a política interna do fornecedor.

O candidato executável `1e7424cdb257dbf0fe6d9d0f0dd8c26e4f77dbe5` acrescenta opção explícita de continuação da URL emitida pelo fornecedor, desligada por defeito. Máximo dois saltos, validação e resolução/IP público fixado em cada salto, prazo total, fecho da ligação anterior, sem cookies/headers da resposta, sem downgrade HTTPS. 401/403 continuam terminais, sem alternativa. Não altera autenticação, catálogo ou escolha de DNS.

**[r143 — fonte real RTP1](https://github.com/martaxi-boss/VPS/actions/runs/45973269421):** artefacto 15186525542 íntegro; evidência SHA-256 `7acf2b58f03c29de61afb60c4b65fdb53002142d3ddc21636b73f30af70546d0`. Conta ativa, mesma identidade e URL oficial selecionada antes da media; respostas **302→200** sequenciais no probe e na reprodução; amostra probe 385024 bytes e reprodução 3762444 bytes em memória. Jellyfin FFmpeg 8.1.3-2 descodificou **635 fotogramas de vídeo e 1188 de áudio**. source_decode_pass=true, production_activation=false, production_unchanged=true, physical_clients_certified=0, media_saved=false, secrets_emitted=false. [Revisão r144](https://github.com/martaxi-boss/VPS/actions/runs/45974344266) aprova apenas este âmbito.

Isto certifica a entrada real no FFmpeg nativo, ainda não a saída do servidor Jellyfin. Está em execução um piloto temporário com Jellyfin12.4 real, dados e chave efémeros, backend1e e plugin normal b2fd cujos fontes C# são idênticos. A composição é apenas de teste, não release de produção. Gates por concluir: PlaybackInfo/probe/saída HLS real, continuidade e cache reais, VOD/séries, concorrência e revogação reais, seis plataformas físicas. Não remover PINK/backups nesta fase.

Backend [45972964122](https://github.com/martaxi-boss/Vivoapk/actions/runs/45972964122) e loopback [45972964061](https://github.com/martaxi-boss/Vivoapk/actions/runs/45972964061) aprovados; integração completa [45972964003](https://github.com/martaxi-boss/Vivoapk/actions/runs/45972964003) ainda em execução na última consulta.

## Histórico de 26/11/2026 — preservado, não representa o estado atual

### Estado observado em 26/11/2026

Candidato executável `b2fdd911bdf549d7cca89fb7fbbebe3f64b2ff27`, no ramo existente. Produção preservada em `0994990c024b159eebd44528041746f557596d27`; não promover main nem instalar este candidato enquanto o acesso real continuar recusado.

A investigação adicional demonstrou uma diferença de fonte: a playlist oficial get.php autenticada anuncia o RTP1 num caminho sem /live/ nem extensão. r127 confirmou mesmas credenciais/asset, esquema, hostname e porta efetiva; a diferença da origem bruta era a porta predefinida explícita. direct_source do catálogo vazio. [Metadata r127 #45883974840](https://github.com/martaxi-boss/VPS/actions/runs/45883974840), evidência SHA-256 `a87d857e2b61b58e1f531a8be7a5f17b4cc6fc9b2b153f423e41dbd467ce5b4d`.

**Correção realizada:** modo de servidor `private_live_use_provider_playlist`, desligado por defeito e condicionado à entrada TS privada. Obtém a URL emitida pelo fornecedor ANTES de abrir media; exige mesma origem efetiva, credenciais e asset já autorizados. URL/credenciais ficam em memória secreta do backend; seleção de DNS e revalidação das leases preservadas. Não tenta caminhos adivinhados nem faz fallback após recusa. Mantém FFprobe/FFmpeg nativos. Admissão UDS com prazo de 90 s, mantendo 30 s nas operações de media, porque a metadata demorou 24–26 s a responder.

**Ensaio real da fonte emitida pelo fornecedor:** [r133 #45907668781](https://github.com/martaxi-boss/VPS/actions/runs/45907668781), artefacto 15159020653, ZIP exterior/interior CRC OK, report.status=completed; evidência SHA-256 `0fa7d8cdf34ce39cc14211309bbc8401f6629fbb4234ffa76943172d4bf28f7a`. Conta ativa/TS anunciado, RTP1 autorizado e provider_advertised_locator_selected_before_media=true. Uma resposta upstream **401**, antes do corpo. Sessão terminada, sem alternativa/repetição. source_decode_pass=false, physical_clients_certified=0, production_activation=false, production_unchanged=true, secrets_emitted=false, media_saved=false. R131 falhou na instrumentação sem relatório seguro; NÃO é teste de fornecedor aprovado nem recusado. R133 usa chave independente efémera apenas em memória para a configuração do teste isolado; nenhum segredo de produção foi alterado.

[Backend #45907247248](https://github.com/martaxi-boss/Vivoapk/actions/runs/45907247248), [plugin #45907247244](https://github.com/martaxi-boss/Vivoapk/actions/runs/45907247244) e [transporte privado #45907247270](https://github.com/martaxi-boss/Vivoapk/actions/runs/45907247270): SUCCESS. **[Integração completa #45907247240](https://github.com/martaxi-boss/Vivoapk/actions/runs/45907247240): SUCCESS, dez gates PASS, COMBINED_LAB_GATE_EXIT=0.** Sete gates usam a DLL normal, três usam uma build separada exclusivamente sintética. Artefactos íntegros: candidato 15159207523 (ZIP SHA-256 `0897e7e5c51f890b1b9fadab4d2fc0322b62f091313d420c3494ff3383b8dbe9`) e diagnósticos 15158967943 (`736a91749d3f06ca2ffc6927deae7af6550cb4589bd555ad5996544d71ab5d13`). DLL normal `3b8520d02c9a5f4a28a95b0501474a9d5fbcc738007c83c9ac490ac010fab05e`, manifest.source/backend=b2fdd911 e activation_allowed=false. No gate TS: 42,25 s simultâneos; abandono limpo após 81,39 s sem alterar o segundo processo, que serviu mais 27 segmentos; recuperação e limpeza final PASS. Estes resultados são sintéticos e não mudam os 401 reais. O fecho acrescenta apenas documentação e cobertura do novo módulo provider_live nos triggers de CI, sem mudar código executável nem repetir estes testes.

A diferença de seleção de fonte era real e foi corrigida, mas não resolveu a recusa. Está demonstrado que a URL exata emitida pelo fornecedor também devolveu 401 nesta ligação. A origem HTTP da recusa está localizada; o motivo interno (condições de rede/IP/cliente/linha ou outra política) continua desconhecido. Não atribuir a causa a codecs, HLS/TS ou credenciais erradas sem evidência. Não realizar mais tentativas de media por outras identidades, rotas, DNS ou túneis. Para desbloquear, é necessária informação oficial sobre as condições em que o fornecedor permite reproduzir esta fonte da linha a partir da VPS. Nenhuma mensagem foi enviada ao fornecedor.

**Condições expostas pela API:** [r136 #45908140355](https://github.com/martaxi-boss/VPS/actions/runs/45908140355), artefacto 15159096137, CRCs OK, evidência SHA-256 `d03a271352cf23907ab4db26781cf1a023202799e76afedbb278e0463ef5489a`: conta ativa, 0/2 ligações; nem a resposta Mega nem user_info Xtream expuseram os campos de condições consultados (allowed_ips/allowed_ua/allowed_user_agents/ip_restriction/geoip_lock/is_restreamer). Zero pedidos de media e serviços preservados. A ausência desses campos não demonstra ausência de restrições e não revela o motivo interno do 401.

**Segundo canal independente:** [r137 RTP2 #45908804242](https://github.com/martaxi-boss/VPS/actions/runs/45908804242), artefacto 15158962269, CRCs OK, evidência SHA-256 `42f0e28db771f4d373b0ff8b43fa54e1adf90be98924e8b2a4381e31ad63a1bb`. Mesmo candidato, identidade/origem existentes, item RTP2 autorizado e URL exata da própria playlist selecionada antes da media: primeira resposta 401, sem corpo/descodificação. Não houve repetição de RTP1, alteração de origem/identidade ou fallback. Serviços preservados e zero clientes físicos certificados. Duas recusas independentes não demonstram uma recusa de todo o catálogo; filmes/séries reais continuam sem ensaio certificado.

TV/VOD/séries reais, continuidade/concorrência/revogação reais e Android/Android TV/iOS/Windows/Samsung/LG continuam por certificar. Não remover PINK/backups e nunca desinstalar jellyfin-pink.service. Os registos seguintes são históricos; este estado mais recente prevalece.


## Percurso implementado

Mega OTT/autorização existente → categoria/item da conta → fonte privada validada → socket interno e capability de loopback → PlaybackInfo/FFprobe → FFmpeg do Jellyfin → saída negociada para a aplicação oficial. Nenhum endereço/segredo da origem entra no comando FFmpeg ou é entregue ao cliente.

O plugin usa o callback nativo de fontes dos canais. A entrada HLS analisa manifestos e segmentos; a entrada TS fornece um corpo contínuo, com amostra sequencial limitada para o probe e conexão persistente para a reprodução. São transportes de entrada distintos para o mesmo motor nativo. Codecs e saída são negociados pelo Jellyfin. Refusões 401/403 são terminais antes de ler o corpo.

A fonte TS anunciada é descoberta numa consulta de metadata à origem já atribuída, sem seguir qualquer entrada durante a descoberta. Aceitação da metadata não é autorização efetiva de media; o ensaio de r133 demonstra essa distinção.

## Matriz das provas reais

| Ensaio | Entrada | Resultado observado |
|---|---|---|
| [r108 #45829120343](https://github.com/martaxi-boss/VPS/actions/runs/45829120343) | HLS construído /live/*.m3u8 | 401 antes do manifesto |
| [r121 #45871678416](https://github.com/martaxi-boss/VPS/actions/runs/45871678416) | TS construído /live/*.ts | 401 antes de bytes TS |
| [r127 #45883974840](https://github.com/martaxi-boss/VPS/actions/runs/45883974840) | Metadata oficial get.php | 200 e fonte do RTP1 identificada; nenhum media aberto |
| [r133 #45907668781](https://github.com/martaxi-boss/VPS/actions/runs/45907668781) | URL TS exata anunciada para o RTP1 | 401 antes de bytes TS; sem descodificação |
| [r137 #45908804242](https://github.com/martaxi-boss/VPS/actions/runs/45908804242) | URL TS exata anunciada para o RTP2, ensaio independente | 401 antes de bytes TS; sem descodificação |

Não foi estabelecido qual componente interno do fornecedor emitiu a recusa nem o seu motivo. Não houve reprodução real certificada no Jellyfin.

## Comparação com o PINK histórico

[Prova PINK #37395113639](https://github.com/martaxi-boss/VPS/actions/runs/37395113639/job/112049200278): Android Media3/ExoPlayer, buffers reais de áudio/vídeo e WireGuard. O [teste fixado](https://github.com/martaxi-boss/pink-iptv/blob/9e1fc82c9c964a079d876671990a41a73689955e/clients/extreme/overlay/vpn-tests/PinkVpnLiveTest.kt) percorre até três itens e m3u8/ts sem identificar qual combinação venceu. O factory TS de outra aplicação Compose não prova o caminho usado no teste Extreme/Tauri.

O PINK fixa Extreme-InfiniTV@1efcc1b4ab3468db04aedd1c466228c52ef001b7; VideoActivity usa DefaultHttpDataSource, headers fornecidos e autodeteção progressiva fora de HLS, permitindo redirecionamentos entre protocolos. Estas diferenças históricas não provam o motivo do 401 e não foram copiadas para ultrapassar recusas.

O [diagnóstico histórico 037/038/039/040](https://github.com/martaxi-boss/pink-iptv/blob/9e1fc82c9c964a079d876671990a41a73689955e/docs/ANDROID_DEVICE_REMEDIATION_037.md) já registava canonical TS/HLS recusados e uma fonte extensionless anunciada por playlist com bytes aceites noutro momento. Isso motivou comparar metadata atual, não reproduzir fallback após uma recusa. A fonte anunciada atual foi explicitamente selecionada antes de media e recusada em r133.

## Provas de laboratório preservadas

- [CI #45847563775](https://github.com/martaxi-boss/Vivoapk/actions/runs/45847563775): gates combinados anteriores com fixtures.
- [Native TS #45872376651](https://github.com/martaxi-boss/Vivoapk/actions/runs/45872376651): MPEG2/MP2 sintético; 42,4 s de duas tarefas FFmpeg simultâneas; abandono de uma sessão limpo após 82,25 s sem perturbar a segunda; restart/reabertura/negação de rotas antigas e limpeza PASS.
- [VPS r118 #45870728814](https://github.com/martaxi-boss/VPS/actions/runs/45870728814): 33 asserções do leitor, isolamento/revogação/401/403 sintéticos PASS. A tentativa r116 sem pytest é falha preservada.
- Novo backend inclui testes de locator emitido, porta predefinida explícita, credenciais/asset/origem alterados, metadata negada sem corpo/repetição e autorização anterior à descoberta.

Estas provas não certificam a fonte real, capacidade da VPS ou dispositivos físicos. A migração permanece incompleta. O histórico integral anterior permanece no Git; não voltar a tratar as lacunas já corrigidas como trabalho por implementar.

## Pergunta histórica preparada em 26/11 — não necessária com a prova atual

Pergunta pronta para o canal oficial do proprietário (não enviada): «A minha linha autentica e lista o catálogo; tem 0/2 ligações e anuncia TS. A própria playlist get.php devolve os endereços de RTP1 e RTP2, mas os GET desses endereços exatos, a partir da VPS, responderam HTTP 401 antes do corpo em ensaios separados de 26/11/2026, cerca das 22:55 e 23:06 UTC. Quais são as condições autorizadas de reprodução desta linha a partir de Jellyfin/FFmpeg na VPS e o motivo dessa recusa nos vossos registos?»

Identificar a linha apenas no canal privado do fornecedor. Não enviar passwords, tokens, URL com credenciais ou ficheiros do cofre. A resposta sobre as condições autorizadas é informação externa em falta; os metadados atuais não a fornecem.

## Fecho operacional na VPS

[r138 #45909693772](https://github.com/martaxi-boss/VPS/actions/runs/45909693772), artefacto 15159248026 íntegro, evidência SHA-256 `dce7b96ac5c2fb8d1f4878a42b48d63ffca49bd3fa30383a707b7ecd6e9e17c5`: checkout isolado b2fdd911 limpo, backend/plugin/infra sem diferença de código face ao fecho documental 784c080e; Bootstrap de revisão guardado fora do checkout, SHA-256 `9371e4278ee7169c0f33f57519659a58a5fb4ca3542d78455b18e38f54c45eea`; serviços/PIDs preservados, zero media/cofre/ativação.

O campo production_checkout_expected=false desse ensaio não certifica uma divergência de versão: tentou consultar Git na pasta contentora /srv/projects/Vivoapk, que não é o checkout ativo. A correção de método está demonstrada em [r139 #45909850816](https://github.com/martaxi-boss/VPS/actions/runs/45909850816), evidência SHA-256 `5d0bb731101416307ce02f242d5ec66f361fa71710dcd08cc88aab8210040342`: o ExecStart/WorkingDirectory do backend aponta para native-release-stage/0994990c024b159eebd44528041746f557596d27/backend/source/backend. Nenhuma reconfiguração foi executada. O serviço Jellyfin permanece instalado; a presente intervenção não substituiu as DLLs ativas. A versão candidata e as provas ficam disponíveis no GitHub e na área isolada da VPS, sem promover main.
