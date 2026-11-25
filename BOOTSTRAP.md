# BOOTSTRAP — VivoAPK / Jellyfin / Mega OTT

**Regra vinculativa ATUAL — 25/11/2026: o proprietário confirma expressamente a autorização de passagem e processamento de áudio e vídeo em tempo real pela VPS, para reprodução nas aplicações oficiais Jellyfin.** Esta decisão prevalece sobre todas as regras anteriores de transmissão exclusivamente direta. Os ensaios históricos abaixo não definem a arquitetura atual.
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
    → Jellyfin recebe a origem autorizada da própria subscrição e verifica o acesso
    → Jellyfin na VPS usa a reprodução nativa: Direct Stream/remux ou FFmpeg quando necessário
    → entrega áudio e vídeo compatíveis por HTTPS do Jellyfin à aplicação oficial
```

**A VPS pode receber, encaminhar, remultiplexar, descodificar e transcodificar áudio e vídeo em tempo real**, exclusivamente para conteúdos e subscrições autorizados. A preferência é usar a infraestrutura nativa Jellyfin + FFmpeg já existente, não criar outro servidor nem reinstalar Pink. Não guardar canais, filmes ou séries como ficheiros de biblioteca; os segmentos/cache técnicos temporários estritamente necessários à reprodução são permitidos, com limites de espaço e limpeza automática. O URL público e o login atuais mantêm-se.

## Decisão atual de reprodução — Jellyfin + FFmpeg na VPS (25/11/2026)

O proprietário confirma que já tinha solicitado esta alteração: a passagem e compatibilização do áudio e vídeo na VPS é autorizada, precisamente para evitar as incompatibilidades dos leitores oficiais com a origem direta. A regra histórica que proibia retransmissão, remux e transcodificação fica **expressamente substituída**. Não exigir novamente autorização para esta mesma arquitetura.

- Preservar o backend, API Mega OTT, descoberta dos DNS, login, identidade, catálogo, bibliotecas e gestão exclusiva no painel Mega OTT já integrados. Não pedir credenciais de novo nem migrar a base de dados por causa da reprodução.
- Usar os destinos completos atribuídos à subscrição e a autenticação existente; não inventar DNS, grants, formatos ou credenciais. O HTTPS do Jellyfin protege a ligação ao cliente; não confundir com segurança ou disponibilidade da origem do fornecedor. A origem tem de ser autorizada e acessível; não desativar validação TLS nem contornar DRM ou controlos de acesso.
- Integrar a reprodução nativa Jellyfin/FFmpeg com os conteúdos autorizados do VivoAPK. Priorizar remux quando os codecs forem suportados e transcodificar somente quando necessário para o perfil real do cliente. O redirecionamento direto anterior passa a ser experiência histórica, não pré-requisito obrigatório. A inexistência de uma origem HTTPS direta universal não é, por si só, bloqueio do caminho mediado pelo Jellyfin.
- Garantir isolamento por subscrição, revalidação de autorização e expiração, sem exposição de credenciais, URLs privadas ou conteúdo de outro cliente. Não habilitar globalmente a reprodução/transcodificação sem validar as rotas e as permissões de cada conta. Não introduzir alterações de segurança nos clientes ou novos serviços desnecessários.
- Verificar o FFmpeg utilizado pelo Jellyfin, CPU/RAM/rede e espaço temporário; limitar sessões simultâneas e utilização de recursos, preservar rollback e restantes serviços. Não criar ficheiros permanentes nem cache de catálogo com segredos. A utilização de media real requer direitos de reprodução e um ambiente autorizado; não contornar o incidente de segurança da sonda privada anteriormente registado.
- Aceitação: testar media de ensaio legalmente autorizado com imagem e som através do Jellyfin, provar remux/transcodificação e limpeza temporária, testar negação cruzada/expiração, validar a linha existente em segurança, instalar apenas após validação e confirmar reprodução nas seis famílias de clientes. Não certificar por HTTP 200, código compilado ou negociação de formatos sem descodificação/reprodução real.

Esta decisão não autoriza a redistribuição de conteúdos sem direitos, a publicação de segredos, a reinstalação do Pink, aplicações próprias, segundo login ou nova infraestrutura de transmissão fora do Jellyfin existente.

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
- As várias bases DNS fornecidas servem **para selecionar a origem correta de cada subscrição**, sem pedir mais endereços ao cliente. A reprodução compatível pode ser entregue pelo Jellyfin/FFmpeg na VPS, conforme a decisão atual.
- A aplicação fala **protocolo Jellyfin** com o servidor; a API Mega OTT / Xtream é usada privadamente pelo VivoAPK para autenticação, autorização e catálogo. Um simples apontamento DNS não converte Xtream automaticamente para o protocolo Jellyfin: **essa tradução pertence ao plugin do servidor**.

## Requisitos funcionais obrigatórios

- Login de cliente automático com username/password Mega OTT (sem segundo registo), sem tocar na conta administrativa já existente do próprio Jellyfin.
- Catálogo automático e **completo para cada subscrição**, com TV, filmes, séries, episódios e guia EPG dentro do que a Mega OTT autoriza e a plataforma suporta.
- Múltiplos clientes simultâneos e isolamento total: nunca revelar itens, IDs reproduzíveis, origens ou credenciais de uma subscrição a outra.
- Renovação, expiração e revogação reconhecidas a partir da Mega OTT sem gestão manual duplicada.
- A reprodução precisa de funcionar nos clientes Jellyfin de destino; testar login, catálogo e imagem+som em plataformas reais. DirectPlay/DirectStream/Transcode no JSON não constituem prova de reprodução. A transmissão temporária pela VPS está autorizada.
- Não introduzir M3U global, tuner partilhado, ficheiros STRM com credenciais, palavras-passe em logs/URL pública ou bases de dados acessíveis a outros clientes.
- Não modificar apps comerciais, contornar DRM/licenças nem supor compatibilidade física sem testes. Reutilizar o código autorizado do antigo Pink apenas como referência de integração e não reinstalar o Pink. Manter cache técnica de reprodução limitada e temporária.

## Infraestrutura e execução

- Projeto: `martaxi-boss/Vivoapk`. Ferramentas: Skill `@motor-universal-linux` (Ferrari + REA + Ruflo) **pela ponte existente GitHub Actions → SSH → VPS**, sem SSH alternativo, nova Skill, novo servidor ou dependências globais.
- Reutilizar o que está efetivamente implementado no backend VivoAPK; uma nova solução incompatível com a integração existente não deve ser criada em paralelo.
- O serviço `jellyfin-pink.service` é o **nome herdado do serviço Jellyfin ativo**, não um pedido para voltar a instalar Pink IPTV. Nunca apagar o Jellyfin por causa do nome.
- Se existirem componentes antigos do Pink ainda ativos, a remoção definitiva é uma operação separada: inventariar consumidores e backups e nunca destruir serviços ou dados que a integração Jellyfin ainda use. O objetivo final é **não depender do Pink IPTV nem ocupar a VPS com o Pink**.

## Estado operacional e de desenvolvimento — 25/11/2026

**Separar o objetivo atual da versão instalada.** O login, a seleção de DNS e a navegação individual já estão implementados. Não os reconstruir. A produção ainda usa a implementação histórica de Direct Play (não cumpre o novo objetivo de reprodução mediada); a alteração para Jellyfin + FFmpeg não está em produção.

- Backend ativo: checkout fixado em `74b6a02be199a29c305058d2875063eb46c1b61d`; DLL de produção `dff3145ddb80b4644ce94f5485bb2ee2afe7b6cd`, SHA-256 `60630b0ab43d4414739f86b047ed4d62358de8c587b878f1aeb6320e0fa813c2`. [Ferrari/REA r70](https://github.com/martaxi-boss/VPS/actions/runs/45630959576) alinhou as referências Git sem substituir o backend; [r71](https://github.com/martaxi-boss/VPS/actions/runs/45631242577) removeu 175 ficheiros gerados e três checkouts antigos, preservando as revisões de recuperação. Não confundir a main com a instalação ativa.
- O serviço Jellyfin mantém o nome herdado `jellyfin-pink.service`. FFmpeg existente `/usr/bin/ffmpeg` 6.1.1: codificação H.264/AAC sintética aprovada na VPS ([r63](https://github.com/martaxi-boss/VPS/actions/runs/45628645853), [r65](https://github.com/martaxi-boss/VPS/actions/runs/45629024461)). A transcodificação ponta a ponta foi demonstrada num servidor Jellyfin descartável de CI, não ainda na instalação de produção. O serviço de produção mantém o isolamento da rede externa; não remover essas proteções genericamente.
- Laboratório ativo: `vivoapk/native-ffmpeg-20261125`. Exclusivamente sintético, não instalável. A falha anterior [#45628159340](https://github.com/martaxi-boss/Vivoapk/actions/runs/45628159340) é histórica: o ensaio [#45633297408](https://github.com/martaxi-boss/Vivoapk/actions/runs/45633297408) demonstrou 49 fotogramas de vídeo H.264 e 94 de áudio AAC descodificados, com negação anónima e cruzada. Não voltar a tratar esse primeiro ensaio como pendente.
- [#45634523234](https://github.com/martaxi-boss/Vivoapk/actions/runs/45634523234): PASS de autenticação por cabeçalho/query nativos no laboratório, recusa de segmentos em estados EXPIRED, DISABLED e UNAVAILABLE e recuperação da sessão sintética quando ACTIVE. Não equivale a certificar subscrições reais.
- [#45635581864](https://github.com/martaxi-boss/Vivoapk/actions/runs/45635581864): PASS de limpeza nativa ao parar (seis ficheiros, 676436 bytes gerados, zero restantes); origem sintética e outra sessão preservadas. [r76](https://github.com/martaxi-boss/VPS/actions/runs/45635485145) verificou cache de produção sem bytes de media, 32,4 GiB livres, escrita disponível, eliminação de segmentos e throttling desativados, quatro serviços ativos, sem alterações. [r77](https://github.com/martaxi-boss/VPS/actions/runs/45636023104) validou os auxiliares de teste.
- Candidato laboratorial `fd10bb22c64631ccea84e04e6bcc99bfea1f29b3`: novo ensaio de cache de uma gravação finita de seis minutos e interrupção sem evento de paragem. [r78](https://github.com/martaxi-boss/VPS/actions/runs/45636851595) verificou 13 critérios de código e auxiliares, sem mudar a produção. O [teste integrado #45636798377](https://github.com/martaxi-boss/Vivoapk/actions/runs/45636798377) ainda não tinha resultado confirmado no momento deste registo. A janela de 20 s e limite de ensaio de 32 MiB não são configuração aprovada de produção nem prova de limite rígido. A branch `vivoapk/redirect-preflight-20261125` permanece histórica.
- **Atualização de validação de 25/11/2026:** [#45637614009](https://github.com/martaxi-boss/Vivoapk/actions/runs/45637614009) confirmou novamente a transcodificação nativa H.264/AAC (49 fotogramas de vídeo e 94 de áudio), controlo de acesso e limpeza ao parar (6 ficheiros, 677188 bytes, zero restantes). O ensaio prolongado ultrapassou a falha anterior de carregamento dos segmentos, mas terminou com `Native rolling cache deletion not observed`. A limpeza durante reprodução continua NÃO CERTIFICADA; não inferir sucesso do teste de paragem.
- **Retoma da mesma branch de laboratório:** `ef86405114d6c224ecfbfe0ded665343d3951fdd` altera apenas o teste `jellyfin-auth/tests/jellyfin_12_4_smoke.py`, simulando eventos nativos de progresso e separando os gates de retenção durante reprodução e desconexão sem paragem. [Compilação #45638432093](https://github.com/martaxi-boss/Vivoapk/actions/runs/45638432093) PASS. [Integração #45638432246](https://github.com/martaxi-boss/Vivoapk/actions/runs/45638432246) ainda sem resultado final confirmado no momento deste registo. Não duplicar a execução, não alterar produção nem certificar clientes físicos a partir de fixtures. O código público atual do Jellyfin condiciona a limpeza HLS a uma duração conhecida de pelo menos cinco minutos; verificar a versão instalada e o comportamento de TV em direto antes de definir qualquer política de cache.
- **Ferrari/REA r81 — [#45638580759](https://github.com/martaxi-boss/VPS/actions/runs/45638580759):** relatório e ZIP verificados. Duas codificações simultâneas sintéticas H.264/AAC 1280x720, 25 fps, 150 fotogramas cada, duração de 6 s, um thread de vídeo por processo, velocidades FFmpeg 1,92x e 1,96x (fatores de tempo de parede 1,84x e 1,87x). VPS com quatro CPUs lógicas, quatro serviços ativos antes/depois. Execução fora do cgroup de produção; não constitui limite certificado de clientes Jellyfin, nem ensaio prolongado, rede ou carga real. Sem alterações na configuração de produção.
- **Ferrari/REA r82 — [#45638750329](https://github.com/martaxi-boss/VPS/actions/runs/45638750329):** relatório e ZIP verificados, 12 critérios de revisão do candidato `ef86405114d6c224ecfbfe0ded665343d3951fdd` aprovados, incluindo eventos sintéticos de início/progresso, rejeição de respostas não válidas, ausência de eliminação manual e de evento de paragem no cenário de abandono, gates de cache independentes, checkout de produção inalterado. O ensaio integrado #45638432246 continua necessário; revisão de código e mocks não são reprodução funcional prolongada.
- Pendente: verificar o resultado do ensaio prolongado, limites de cache/CPU/rede/concorrência e interrupções; provar a reprodução de media legalmente autorizado (incluindo conteúdos de teste representativos de TV, filmes e episódios) sem expor segredos ou contornar controlos; EPG nativo e testes físicos nas seis famílias de clientes. A integração de origens reais e uma release de produção continuam por validar. Não pedir credenciais novamente nem usar a linha privada fora do ambiente autorizado. Nenhum cliente físico está certificado.

## Organização e higiene

`main` é a referência estável do código e contrato; a produção fica fixada a revisões verificadas por componente. Não fazer checkout da main por cima do backend ativo. Não promover experiências antigas. Preservar rollback e dados de subscrições. A execução é Ferrari/REA pela ponte existente GitHub Actions → SSH → VPS. Consultar o [arquivo histórico](docs/HISTORICO_BOOTSTRAP_20261125.md) para provas, relatórios de versões e decisões revogadas; o arquivo nunca define a arquitetura atual. Os relatórios anteriores em `docs/` são evidências datadas, não instruções vigentes.

## Critério final de aceitação

Um cliente novo, sem instalação nem configuração adicional além do Jellyfin oficial, abre a app, introduz `https://serveriptv.duckdns.org`, username e password Mega OTT, vê **automaticamente só a sua subscrição**, reproduz conteúdos autorizados com imagem e som usando a reprodução nativa do Jellyfin e o processamento FFmpeg na VPS quando necessário, e perde/recupera acesso conforme o painel Mega OTT. Repetir com pelo menos duas contas diferentes e clientes de cada plataforma de destino (Android, Android TV, iOS, Windows, Samsung Tizen e LG webOS). Verificar recursos, cache temporária e ausência de armazenamento permanente de media. Não confundir autorização da nova arquitetura com implementação ou instalação concluídas.

**NÃO DESVIAR DESTE OBJETIVO PARA CORREÇÕES APENAS ANDROID, PAINÉIS NOVOS OU REINSTALAÇÃO DO PINK IPTV.**
