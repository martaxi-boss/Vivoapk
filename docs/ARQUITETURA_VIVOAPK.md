# Vivoapk — arquitetura alvo Mega OTT sem video na VPS

Cliente -> (HTTPS) Backend Vivoapk POST /v1/session/resolve
-> Mega OTT API (meta da linha, dois DNS) -> validacao Xtream no DNS escolhido
-> cliente recebe origem aprovada e reproduz diretamente junto do Mega OTT.

O cliente apresenta somente username e password do revendedor Mega OTT.
O backend nunca retransmite Live/VOD/Series/EPG nem guarda videos.

| Plataforma | DNS da subscricao |
|---|---|
| Android / Android TV / Windows | dns_link |
| iOS | dns_link como hipotese tecnica; exige prova com cliente real |
| Samsung Tizen e LG webOS nativos | dns_link_for_samsung_lg |

DNS em falta para uma Smart TV nativa causa UPSTREAM_ERROR, sem fallback.
Os dominios DuckDNS de controlo nao substituem nenhum dos dois DNS Mega.

- serveriptv.duckdns.org: Jellyfin de teste existente, login independente.
- vivo-team.duckdns.org: hostname de experiencias anteriores no Vivoapk,
  ainda nao publicado como backend Mega OTT.
- Apps Jellyfin oficiais NAO reconhecem usernames Mega OTT so por mudar o DNS.
- Vivo Player comercial NAO e modificado nem ativado por este codigo.
- Clientes Android/iOS/Tizen/webOS precisam de implementacao/instalacao propria
  ou integracao explicitamente suportada com app open source, com testes fisicos.
- O backend PINK anterior nao sera apagado antes de provar migracao completa.
