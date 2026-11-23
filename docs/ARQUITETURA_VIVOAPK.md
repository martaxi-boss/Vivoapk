# Arquitetura vinculativa do VivoAPK — Jellyfin como único cliente

Fonte de verdade funcional: [BOOTSTRAP](../BOOTSTRAP.md). **Este documento substitui a antiga arquitetura direta Backend → app IPTV**, que era da fase Pink e não corresponde ao produto atual.

```text
Jellyfin oficial (Android / Android TV / iOS / Windows / Tizen / webOS)
  URL único: https://serveriptv.duckdns.org
  Login: utilizador e palavra-passe da Mega OTT
      │  protocolo oficial Jellyfin
      ▼
Jellyfin 12.4 na VPS — plugin nativo VivoAPK
      │  API interna via socket Unix; nunca porta pública Xtream
      ▼
Backend VivoAPK / Meta da subscrição Mega OTT
      │  valida entitlement, user/pass e DNS oficial por plataforma
      └─ seleciona dns_link ou dns_link_for_samsung_lg da mesma subscrição
      │
      └─ apresenta metadados próprios no Jellyfin:
         Live TV / Filmes / Séries / Episódios / EPG
      │
      └─ MediaSource direto à origem Mega OTT quando o cliente reproduz sem relay
         (método ainda por provar em cada cliente).
```

O painel Mega OTT é o único painel de gestão de clientes. A infraestrutura Jellyfin pode ter uma identidade administrativa própria para operação do serviço, mas **essa identidade nunca é um passo de cliente**.

Não confundir `serveriptv.duckdns.org` (ponto de entrada Jellyfin HTTPS) com os DNS Xtream completos da Mega OTT. Não pedir esses URLs ao cliente, não construir subdomínios por adivinhação e não publicar credenciais.

**Limite da funcionalidade nativa Jellyfin:** a secção Live TV do Jellyfin aceita tuners M3U ou HDHomeRun e o Jellyfin pode servir/transcodificar media. **Essa funcionalidade, por si só, não cria catálogos por username Mega OTT nem garante streaming sem passar pela VPS.** Exige adaptação nativa segura por subscrição, e os clientes oficiais têm capacidades diferentes. Não adicionar um tuner M3U global com palavras-passe partilhadas só para apresentar mais canais.

O canal piloto `VivoapkLivePreviewChannel` mantém apenas um assinante de cada vez e até cinco itens; não habilitar simultaneidade multiutilizador através de um dicionário estático sem primeiro verificar o isolamento de `ChannelManager` e o `PlaybackInfo` de IDs cruzados. O ramo experimental multiutilizador anterior **não é prova de isolamento**.

O código atualmente acrescenta deteção conservadora de plataforma no login (tizen, webos, android_tv, ios, windows, fallback android) para selecionar o DNS oficial do utilizador, mas só os testes de aparelhos reais permitem certificar a identificação e reprodução em cada aplicação.

Para o histórico de Vivo Player/Pink e testes de interoperabilidade, ver [documento antigo](ESTUDO-APP-DIRETA-2026-11-22.md) e [migração](MIGRACAO_PINK_PARA_VIVOAPK.md).
