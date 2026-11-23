# VivoAPK — entrada Mega OTT na aplicação Jellyfin

**O [BOOTSTRAP.md](BOOTSTRAP.md) é o contrato obrigatório deste projeto.** O cliente utiliza a aplicação oficial Jellyfin (Android, iOS, Windows e Smart TV compatível) sem APK nosso nem outro painel.

## Três campos — e mais nada

1. **Servidor:** `https://serveriptv.duckdns.org`
2. **Utilizador:** o da sua subscrição Mega OTT
3. **Password:** a da mesma subscrição Mega OTT

O servidor Jellyfin recebe o login; o plugin VivoAPK valida a subscrição com o backend privado e a API Mega OTT, seleciona o DNS oficial que pertence à subscrição e deve apresentar só os canais, filmes, séries, episódios e EPG dessa conta. **A gestão de clientes e subscrições é exclusivamente no painel Mega OTT.** Nunca exigir acesso administrativo Jellyfin, M3U, MAC, Device Key ou outro DNS ao cliente.

A VPS não deve fazer proxy, remux nem transcodificação de vídeos. O Jellyfin pode usar o leitor interno para uma fonte compatível, mas **Direct Play anunciado pelo servidor não certifica transmissão direta em todas as apps**. Ver [arquitetura](docs/ARQUITETURA_VIVOAPK.md).

## Estado técnico verificado — 23/11/2026

| Componente | Estado |
|---|---|
| Backend VivoAPK + plugin Jellyfin 12.4 | Instalados e ativos; validação Mega demonstrada |
| Catálogo de TV nativo | **Piloto de até 5 canais, uma subscrição de cada vez**, expiração em RAM de 30 minutos |
| Filmes, séries, episódios, EPG por conta | Backend de metadados desenvolvido; integração nativa completa no Jellyfin **não concluída** |
| Android físico | Login e catálogo piloto vistos, mas a reprodução continua com `Source error` |
| iOS, Windows, Samsung/LG | Fluxo final não certificado em aparelhos reais |
| PINK IPTV antigo | Pasta e unidades backend/VPN ausentes; não reinstalar |
| `jellyfin-pink.service` | **É o serviço Jellyfin ativo**, com nome herdado — preservar |

**Não declarar solução pronta ou apagar backups porque os testes CI deram sucesso.** Requer reprodução em aparelhos reais, isolamento por dois clientes e prova de ausência de media relay pela VPS. Nenhum cliente deve receber itens ou credenciais de outra subscrição.

## Código ativo

- `backend/`: validador Mega OTT, DNS por plataforma, catálogo de metadados e sessão privada;
- `jellyfin-auth/`: plugin nativo de autenticação e piloto de canais Jellyfin;
- `infra/`: scripts de instalação/rollback com salvaguardas e testes de serviços;
- `BOOTSTRAP.md`: requisitos finais de clientes;
- `docs/`: relatórios históricos e provas das alterações.

**Código antigo de cliente Android Pink e script de migração única:** removidos do HEAD após confirmação de que não são executáveis do produto Jellyfin e de que a instalação Pink já não existe; permanecem no histórico Git do repositório.

## Evidências e diagnóstico

[Auditoria de higiene Ferrari/REA de 23/11/2026](docs/AUDITORIA_HIGIENE_BOOTSTRAP_20261123.md) · [Falha de reprodução RTP1](docs/RTP1_ANDROID_PLAYBACK_FIX_20261123.md) · [Migração técnica da API PINK](docs/PINK_MEGA_INTEROPERABILIDADE.md) · [Catálogo por cliente](docs/PONTO_3_CATALOGO_PRIVADO_DESENVOLVIMENTO_20261123.md).

O trabalho usa **apenas o motor Ferrari/REA/Ruflo**, pela ponte GitHub Actions → SSH → VPS. Não publicar passwords, tokens, URLs privados de subscrições, listas de clientes nem conteúdos. Não modificar o serviço Jellyfin herdado para remover a palavra “pink” do seu nome.
