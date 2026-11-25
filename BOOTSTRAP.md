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

- Backend ativo: checkout fixado em `74b6a02be199a29c305058d2875063eb46c1b61d`, confirmado pelo [Ferrari/REA r66](https://github.com/martaxi-boss/VPS/actions/runs/45629560192). Última DLL de produção documentada: `dff3145ddb80b4644ce94f5485bb2ee2afe7b6cd`, verificação anterior [#45390604631](https://github.com/martaxi-boss/VPS/actions/runs/45390604631). Não confundir com o HEAD da main. Backend, Jellyfin, Nginx e PostgreSQL ativos; serviços antigos Pink/VPN ausentes (r66).
- O serviço Jellyfin mantém o nome herdado `jellyfin-pink.service`. O FFmpeg existente `/usr/bin/ffmpeg` 6.1.1 passou codificação H.264/AAC sintética ([r63](https://github.com/martaxi-boss/VPS/actions/runs/45628645853)); a conta do serviço também passou o ensaio ([r65](https://github.com/martaxi-boss/VPS/actions/runs/45629024461)). Caminho efetivo selecionado pelo servidor e transcodificação ponta a ponta ainda não verificados. O serviço está isolado da rede externa; não remover essas proteções genericamente.
- A branch `vivoapk/native-ffmpeg-20261125` é o laboratório ativo, exclusivamente sintético, não instalável. [Reteste #45628159340](https://github.com/martaxi-boss/Vivoapk/actions/runs/45628159340) falhou no controlo de acesso anónimo/de outra conta (HTTP 200 inesperado). Não ativar em produção. A branch `vivoapk/redirect-preflight-20261125` é referência histórica, não a direção do produto.
- Pendente: uma entrada de media autorizada e isolada por subscrição, proteção das rotas nativas/segmentos, revalidação durante sessões, remux/transcodificação com imagem e som, limites de cache/recursos, EPG nativo e testes físicos nas seis famílias de clientes. Não pedir credenciais novamente nem expor a linha privada; não contornar recusas de segurança. Nenhum cliente físico está certificado.

## Organização e higiene

`main` é a referência estável do código e contrato; a produção fica fixada a revisões verificadas por componente. Não fazer checkout da main por cima do backend ativo. Não promover experiências antigas. Preservar rollback e dados de subscrições. A execução é Ferrari/REA pela ponte existente GitHub Actions → SSH → VPS. Consultar o [arquivo histórico](docs/HISTORICO_BOOTSTRAP_20261125.md) para provas, relatórios de versões e decisões revogadas; o arquivo nunca define a arquitetura atual. Os relatórios anteriores em `docs/` são evidências datadas, não instruções vigentes.

## Critério final de aceitação

Um cliente novo, sem instalação nem configuração adicional além do Jellyfin oficial, abre a app, introduz `https://serveriptv.duckdns.org`, username e password Mega OTT, vê **automaticamente só a sua subscrição**, reproduz conteúdos autorizados com imagem e som usando a reprodução nativa do Jellyfin e o processamento FFmpeg na VPS quando necessário, e perde/recupera acesso conforme o painel Mega OTT. Repetir com pelo menos duas contas diferentes e clientes de cada plataforma de destino (Android, Android TV, iOS, Windows, Samsung Tizen e LG webOS). Verificar recursos, cache temporária e ausência de armazenamento permanente de media. Não confundir autorização da nova arquitetura com implementação ou instalação concluídas.

**NÃO DESVIAR DESTE OBJETIVO PARA CORREÇÕES APENAS ANDROID, PAINÉIS NOVOS OU REINSTALAÇÃO DO PINK IPTV.**
