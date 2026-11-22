# VivoAPK — Jellyfin primeiro, login Mega OTT diretamente na app

## Decisao do proprietario (22/11/2026)

**Interface final obrigatoria: app oficial Jellyfin**, nao uma app Xtream alternativa.
O utilizador deve conseguir:
1. introduzir uma vez o URL `https://serveriptv.duckdns.org` no cliente Jellyfin (DuckDNS temporario; dominio proprio possivel mais tarde);
2. escrever o **mesmo username e password da sua subscricao Mega OTT** no ecra de login normal do Jellyfin;
3. abrir imediatamente o catalogo da sua subscricao: TV, filmes, series e guia, conforme permitido e suportado;
4. reproduzir diretamente do fornecedor quando o cliente/protocolo/codec o suportar, sem armazenar filmes ou ocupar a largura de banda da VPS.

O login `pink-admin` pertence **apenas a administracao Jellyfin** e nao e credencial Mega OTT.
**Nao** prometer que o Jellyfin instalado ja faz isto: a versao 12.4.0 atualmente instalada autentica localmente e esta sem bibliotecas.

## Nao confundir APIs e protocolos

- `serveriptv.duckdns.org`: **ponto de entrada Jellyfin HTTPS**, nao DNS Mega.
- A aplicacao Jellyfin continua a falar o **protocolo Jellyfin** com este servidor.
- O backend independente VivoAPK tem `POST /v1/session/resolve`, criado a partir do PINK:
  recebe username/password e platform, identifica a subscricao e autentica Xtream na origem Mega.
- `dns_link` e `dns_link_for_samsung_lg`: enderecos **originais do fornecedor para cada subscricao**; nao substituir, editar ou reconstruir a partir do dominio DuckDNS.
- Xtream e a **interface de dados do fornecedor nos bastidores**, nao o modo de login que o cliente escolhe no Jellyfin.

## Componentes exigidos para atingir o objetivo

### A. Autenticacao nativa Jellyfin -> Mega OTT

Desenvolver/validar plugin compativel com Jellyfin **12.4.0**, capaz de fornecer autenticacao externa no login de dois campos. O plugin ou um provider associado deve:
- consultar a API interna VivoAPK por HTTPS/loopback privado para validar username/password no Mega OTT;
- criar/associar o registo interno Jellyfin do cliente **sem exigir ao utilizador uma segunda conta ou palavra-passe**;
- manter o administrador do Jellyfin separado e protegido;
- aplicar revogacao/expiracao e limites de tentativas;
- nunca copiar password, token revendedor ou URLs privadas para GitHub/logs; nao guardar a password Mega como senha local Jellyfin.

Ponto de partida de arquitetura: o plugin oficial Jellyfin LDAP demonstra a extensibilidade do
provider de autenticacao e a criacao de utilizadores Jellyfin a partir de um diretorio externo:
https://github.com/jellyfin/jellyfin-plugin-ldapauth
O LDAP plugin existente NAO autentica Mega OTT por si so; e preciso um provider/adaptacao proprio.

### B. Conteudo e isolamento por cliente

O Jellyfin incorpora M3U tuners Live TV, mas nao oferece, sem extensao, o fluxo pedido
de `username/password Mega => catalogo individual de TV+VOD+series`.
Ponto de partida open source:
https://github.com/Kevinjil/Jellyfin.Xtream
Este plugin expõe Live TV, VOD, series e catch-up, mas exige URL/username/password
na configuracao administrativa. A sua documentacao **alerta explicitamente** que
Jellyfin pode publicar caminhos remotos com credenciais no API/UI, o que torna
essa implementacao **inadequada sem adaptacao e testes de isolamento** para um
servidor partilhado com varios clientes.

Uma solucao eventual tem de:
- associar **cada Jellyfin user ID** a uma subscricao Mega OTT especifica;
- devolver ao utilizador apenas itens e origens da sua propria linha;
- impedir que Jellyfin API/caches/catalogo global exponham passwords ou itens de outro cliente;
- respeitar a distincao entre DNS geral e DNS Samsung/LG conforme o **dispositivo e a
  capacidade tecnica da app Jellyfin**; nao presumir que a app Jellyfin instalada em
  Tizen/webOS usa automaticamente a mesma selecao de um leitor Xtream nativo;
- gerir expiração e revogacao de sessoes sem depender de uma importacao de playlists
  global e permanente.

### C. Video direto — prova obrigatoria

O Jellyfin pode oferecer `Direct Play`, mas tambem usa remux, `Direct Stream` e
transcodificacao conforme o cliente/codec e a origem. **Direct Play nao equivale
automaticamente a HTTP streaming sem passagem pela VPS**, nem significa que
todas as apps possam abrir as URLs `http://` que Mega fornece atualmente.

Antes de disponibilizar a clientes:
- testar playback numa linha de teste autorizada com Jellyfin Android;
- repetir com iOS, Android TV, Samsung Tizen e LG webOS sempre que forem
  plataformas de destino suportadas e as apps existirem;
- monitorizar na VPS se o fluxo de media passa pelo host ou usa a URL Mega
  diretamente, sem confundir sinalizacao/metadata com transferencia de video;
- testar acesso HTTPS Jellyfin combinado com URLs Xtream HTTP e restricoes
  de mixed-content, DRM, codecs, CORS e formas de reproduzir no cliente;
- se uma app apenas funciona com relay/transcode pelo servidor, assinalar
  `NAO-CUMPRE-REQUISITO` para essa plataforma; nao fingir reproducao direta.

A documentacao Jellyfin descreve estes modos:
https://jellyfin.org/docs/general/post-install/transcoding/
https://jellyfin.org/docs/general/server/live-tv/

## Estado real na data

- Jellyfin 12.4.0 instalado e com HTTPS em `serveriptv.duckdns.org`; login
  `pink-admin` foi comprovado no Android, sem bibliotecas/TV na instalacao.
- Codigo VivoAPK para autenticar Mega/Xtream e selecionar origem por plataforma
  ja foi publicado e testado com dados artificiais; nao e um plugin Jellyfin.
- Cofre privado na VPS: `/root/project-secrets/VIVOAPK` com API Mega OTT,
  quatro subscricoes e dois DNS por subscricao. Nao publicar valores.
- **Ainda nao implementado**: provider de autenticacao Jellyfin 12.4 -> Mega,
  provisionamento do utilizador Jellyfin, catalogo Xtream individual e
  playback direto end-to-end nos clientes Jellyfin.
- O PINK IPTV, sua VPN e backend permanecem instalados ate termos comprovado
  funcionamento completo do novo fluxo.

## Condicoes para concluir / remover PINK

1. Plugin de auth Jellyfin 12.4 compilado, instalado e verificado sem alterar `pink-admin`.
2. Cliente Android Jellyfin entra usando **apenas username/password Mega real autorizados**.
3. Login de dois clientes diferentes mostra exclusivamente os catalogos proprios;
   expiração/revogacao testadas.
4. Testar TV/VOD/series/EPG, streams diretos e consumos reais de rede/CPU na VPS.
5. Verificar seguranca de credenciais e HTTP do fornecedor.
6. Testes em dispositivos adicionais suportados; backups do cofre e recuperacao.
7. So depois desligar e desinstalar os servicos PINK/VPN sem tocar em Jellyfin,
   VivoAPK, cofre central, DNS, certificados nem outros projetos.

**Regra:** nao instalar apressadamente Jellyfin.Xtream global partilhado e nao
introduzir credenciais de clientes num plugin que as pode expor a outros.
