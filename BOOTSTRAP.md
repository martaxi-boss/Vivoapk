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

## Estado comprovado versus objetivo — 24/11/2026

**Instalado de facto na VPS:** backend VivoAPK na revisão `74b6a02be199a29c305058d2875063eb46c1b61d` e plugin Jellyfin da revisão `7989e4371f8b0e0f5daf152745361851b1d41e03` (DLL SHA-256 `b443c40c70104c02c32b4b1cf5c5df0e06638289fe2146bcab59a0c2d78c0640`). **Não é a antiga versão de cinco canais / uma conta.** A opção `CategoryNavigation=true` apresenta estruturas nativas de **TV ao vivo, Filmes e Séries**, com categorias, temporadas e episódios carregados à medida que são consultados. O limite atual é de **64 canais internos de subscrição reservados**, não de clientes ilimitados.

**Verificado por Ferrari/REA na VPS:** [ativação #45295919917](https://github.com/martaxi-boss/VPS/actions/runs/45295919917), [pós-instalação independente #45296130037](https://github.com/martaxi-boss/VPS/actions/runs/45296130037) e [reserva após login real #45296571291](https://github.com/martaxi-boss/VPS/actions/runs/45296571291). Serviço Jellyfin, backend, Nginx e PostgreSQL ativos; quatro APIs privadas respondem HTTP 401 sem credenciais; backup do plugin anterior presente; conta administrativa preservada; versão antiga Pink backend/VPN ausente. Uma subscrição Mega OTT real autenticou-se no Jellyfin com HTTP 200 e recebeu um canal interno privado reservado. O ensaio posterior de consulta de canais teve um cabeçalho de autorização incorreto **no próprio script de diagnóstico** e, portanto, **não comprova a navegação real pela app**.

**Testes com Jellyfin 12.4 descartável:** [#45294305825](https://github.com/martaxi-boss/Vivoapk/actions/runs/45294305825) passou com duas contas fictícias: TV, filmes, séries, temporadas, episódios, fontes HLS/MP4 sintéticas diretas, recusa de itens de outra conta, recusa de IDs de utilizador forjados, nenhuma descarga antecipada de catálogos globais e recuperação após reinício.

**Ainda não certificado como produto final:** imagem e som em Android físico (o último teste anterior reportou `Source error`), iOS, Windows, Samsung Tizen e LG webOS; EPG visível no guia nativo Jellyfin (a API EPG do backend existe, não equivale à UI Live TV); escalabilidade acima das 64 reservas, revogação/reautenticação em sessões longas e recuperação após reinício sem intervenção; prova de que os clientes reais não encaminham media pela VPS. As fontes `DirectPlay` no JSON e a CI **não substituem testes nos aparelhos reais**.

**Regra de honestidade:** este é o estado atual da **instalação técnica com catálogo ativo**, não uma certificação de compatibilidade universal nem de reprodução direta física. Não criar novo painel, APK, proxy ou reinstalação Pink para esconder lacunas.

## Critério final de aceitação

Um cliente novo, sem instalação nem configuração adicional além do Jellyfin oficial, abre a app, introduz `https://serveriptv.duckdns.org`, username e password Mega OTT, vê **automaticamente só a sua subscrição**, reproduz conteúdos autorizados sem retransmissão pela VPS e perde/recupera acesso conforme o painel Mega OTT. Repetir com pelo menos duas contas diferentes e clientes de cada plataforma de destino.

**NÃO DESVIAR DESTE OBJETIVO PARA CORREÇÕES APENAS ANDROID, PAINÉIS NOVOS OU REINSTALAÇÃO DO PINK IPTV.**
