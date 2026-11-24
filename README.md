# VivoAPK — entrada Mega OTT na aplicação Jellyfin

**O [BOOTSTRAP.md](BOOTSTRAP.md) é o contrato obrigatório deste projeto.** O cliente utiliza a aplicação oficial Jellyfin (Android, iOS, Windows e Smart TV compatível) sem APK nosso nem outro painel.

## Três campos — e mais nada

1. **Servidor:** `https://serveriptv.duckdns.org`
2. **Utilizador:** o da sua subscrição Mega OTT
3. **Password:** a da mesma subscrição Mega OTT

O servidor Jellyfin recebe o login; o plugin VivoAPK valida a subscrição com o backend privado e a API Mega OTT, seleciona o DNS oficial que pertence à subscrição e deve apresentar só os canais, filmes, séries, episódios e EPG dessa conta. **A gestão de clientes e subscrições é exclusivamente no painel Mega OTT.** Nunca exigir acesso administrativo Jellyfin, M3U, MAC, Device Key ou outro DNS ao cliente.

A VPS não deve fazer proxy, remux nem transcodificação de vídeos. O Jellyfin pode usar o leitor interno para uma fonte compatível, mas **Direct Play anunciado pelo servidor não certifica transmissão direta em todas as apps**. Ver [arquitetura](docs/ARQUITETURA_VIVOAPK.md).

## Estado técnico instalado — 24/11/2026

| Componente | Estado confirmado |
|---|---|
| Backend Mega OTT | **Instalado**; APIs privadas de categorias, episódios e EPG; autenticação sem token recusada (HTTP 401) |
| Plugin Jellyfin 12.4 | **Instalado**; `CategoryNavigation=true`; backup seguro e rollback disponíveis |
| TV, filmes, séries e episódios | Pastas nativas por conta e carregamento por categoria, testados com duas contas fictícias num Jellyfin descartável |
| Login real Mega OTT → Jellyfin | **HTTP 200**, com um canal de subscrição privado reservado no plugin real |
| Isolamento de clientes | PASS em servidor descartável para duas contas; limite de 64 canais internos reservados, não escalável sem mais desenvolvimento |
| Guia EPG | API privada existente; **integração visual com guia nativo Jellyfin por certificar** |
| Android físico | Último teste anterior reportou `Source error`; **não revalidado após esta instalação** |
| iOS, Windows, Samsung/LG | Reprodução direta e funcionamento completo por verificar em aparelhos reais |
| PINK IPTV antigo | Backend e VPN ausentes; não reinstalar |
| `jellyfin-pink.service` | É o Jellyfin ativo (nome herdado), não o programa antigo Pink IPTV |

**Provas:** [CI Jellyfin 12.4 #45294305825](https://github.com/martaxi-boss/Vivoapk/actions/runs/45294305825) · [Ferrari/REA ativação #45295919917](https://github.com/martaxi-boss/VPS/actions/runs/45295919917) · [auditoria da VPS #45296130037](https://github.com/martaxi-boss/VPS/actions/runs/45296130037) · [login real #45296571291](https://github.com/martaxi-boss/VPS/actions/runs/45296571291).

**A navegação está implementada num provedor `IChannel` (secção Canais do Jellyfin), não num tuner global da secção Live TV.** Isto evita uma playlist partilhada entre clientes, mas a apresentação nas diferentes aplicações precisa de verificação física.

**Não declarar o sistema 100% concluído até haver imagem, som e isolamento verificados nas aplicações físicas, e até resolver o guia EPG e o limite de clientes.** A ativação do plugin é real, mas o Direct Play sintético da CI não equivale a reprodução nos dispositivos.

## Código ativo

- `backend/`: validador Mega OTT, DNS por plataforma, catálogo de metadados e sessão privada;
- `jellyfin-auth/`: plugin nativo de autenticação e pastas privadas de TV, filmes, séries e episódios no Jellyfin;
- `infra/`: scripts de instalação/rollback com salvaguardas e testes de serviços;
- `BOOTSTRAP.md`: requisitos finais de clientes;
- `docs/`: relatórios históricos e provas das alterações.

**Código antigo de cliente Android Pink e script de migração única:** removidos do HEAD após confirmação de que não são executáveis do produto Jellyfin e de que a instalação Pink já não existe; permanecem no histórico Git do repositório.

## Evidências e diagnóstico

[Auditoria de higiene Ferrari/REA de 23/11/2026](docs/AUDITORIA_HIGIENE_BOOTSTRAP_20261123.md) · [Falha de reprodução RTP1](docs/RTP1_ANDROID_PLAYBACK_FIX_20261123.md) · [Migração técnica da API PINK](docs/PINK_MEGA_INTEROPERABILIDADE.md) · [Catálogo por cliente](docs/PONTO_3_CATALOGO_PRIVADO_DESENVOLVIMENTO_20261123.md).

O trabalho usa **apenas o motor Ferrari/REA/Ruflo**, pela ponte GitHub Actions → SSH → VPS. Não publicar passwords, tokens, URLs privados de subscrições, listas de clientes nem conteúdos. Não modificar o serviço Jellyfin herdado para remover a palavra “pink” do seu nome.
