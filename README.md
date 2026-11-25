# VivoAPK — entrada Mega OTT na aplicação Jellyfin

**O [BOOTSTRAP.md](BOOTSTRAP.md) é o contrato obrigatório deste projeto.** O cliente utiliza a aplicação oficial Jellyfin (Android, iOS, Windows e Smart TV compatível) sem APK nosso nem outro painel.

## Três campos — e mais nada

1. **Servidor:** `https://serveriptv.duckdns.org`
2. **Utilizador:** o da sua subscrição Mega OTT
3. **Password:** a da mesma subscrição Mega OTT

O servidor Jellyfin recebe o login; o plugin VivoAPK valida a subscrição com o backend privado e a API Mega OTT, seleciona o DNS oficial que pertence à subscrição e deve apresentar só os canais, filmes, séries, episódios e EPG dessa conta. **A gestão de clientes e subscrições é exclusivamente no painel Mega OTT.** Nunca exigir acesso administrativo Jellyfin, M3U, MAC, Device Key ou outro DNS ao cliente.

A arquitetura atual autoriza o Jellyfin + FFmpeg na VPS a processar media autorizada em tempo real, sem armazenamento permanente, com isolamento por subscrição e transcodificação só quando necessária. A instalação atual ainda não implementa essa reprodução mediada. Ver [BOOTSTRAP](BOOTSTRAP.md) e [arquitetura](docs/ARQUITETURA_VIVOAPK.md).

## Estado atual

| Componente | Estado |
|---|---|
| Backend, login, DNS, catálogo privado | Instalados; preservar |
| Jellyfin 12.4 | Ativo; serviço herdado `jellyfin-pink.service` |
| FFmpeg | Existente; ensaio sintético H.264/AAC passou também na identidade do serviço |
| Reprodução mediada | Desenvolvimento isolado; falha de controlo de acesso no laboratório, sem deploy |
| Apps oficiais | Imagem e som físicos ainda não certificados em nenhuma das seis famílias |
| EPG nativo | Por validar |
| Pink IPTV | Serviços antigos backend/VPN ausentes; não reinstalar |

## Código e referências

- `backend/`: sessão, autorização, DNS e catálogo privado.
- `jellyfin-auth/`: integração nativa Jellyfin; ver [README](jellyfin-auth/README.md).
- `infra/`: instalação, atualização e rollback; ver [README](infra/README.md).
- `BOOTSTRAP.md`: contrato atual, estado e aceitação.
- `docs/HISTORICO_BOOTSTRAP_20261125.md`: provas históricas, sem força normativa.

A main é estável; o desenvolvimento de reprodução nativa decorre em `vivoapk/native-ffmpeg-20261125`. Não instalar a branch experimental. Usar o Ferrari/REA pela ponte existente; não expor credenciais, origens privadas ou conteúdo de clientes. Não alterar autenticação, catálogo ou DNS para resolver a reprodução.
