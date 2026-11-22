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

## Contrato operativo definitivo: DNS unico visivel e catalogo dinamico

**Cliente (Jellyfin oficial, nao APK PINK):**
- O cliente configura apenas `https://serveriptv.duckdns.org` como servidor.
- No ecrã normal Jellyfin insere o **username** e a **password** da sua subscricao Mega OTT.
- Sem formulario M3U, sem segundo DNS, sem MAC, sem escolha de operador,
  sem painel de playlists e sem necessidade de o revendedor criar manualmente
  uma conta Jellyfin por pessoa.
- O acesso validado deve fazer aparecer **automaticamente e apenas**
  os canais/filmes/series/EPG permitidos pela subscricao autenticada.

**Servidor privado e Mega OTT:**
- `serveriptv.duckdns.org` e um endereco fixo da instalacao Jellyfin do
  revendedor, nao um proxy DNS, uma playlist, nem um DNS Xtream.
- O dominio nao identifica magicamente contas: o servidor Jellyfin recebe
  o login e uma integracao propria no servidor tem de chamar o resolver
  VivoAPK/Mega OTT e reconhecer o ID de subscricao.
- O cofre privado tem os acessos de revendedor e o mapeamento por
  subscricao. A gama de bases observadas na captura inclui
  `zvpnm.com`, `fqvpnw.com`, `fhvpnw.com`, `beuaya.com`;
  os URLs nativos Samsung/LG observados usam `daizsmart.com`.
  Esses dominios existem **apenas na configuracao backend**, nunca
  como campos que o utilizador preenche.
- O codigo deve selecionar o **URL completo atribuido pelo Mega OTT**
  (`dns_link` ou `dns_link_for_samsung_lg`) na plataforma adequada,
  nao inventar subdominios ou formar URLs por concatenacao de bases fixas.
  A lista do menu e uma referencia observada, nao uma whitelist imutavel.
- Uma conta PINK conhecida podia ser reutilizada pelo mapeamento local;
  novas contas eram descobertas pelo username na API Mega, verificadas
  por ID, depois autenticadas por Xtream. Esse fluxo e a base do VivoAPK.
- O carregamento de conteudo tem de ser **por identidade Jellyfin**,
  automaticamente, nunca uma playlist global partilhada entre clientes.
  Cada utilizador ve apenas a propria subscricao, com expiracao e revogacao.

**Limite de privacidade e video:**
Nao mostrar DNS Mega na interface nem os pedir ao utilizador e viavel
como objetivo de UX; mas **nao se pode garantir que uma origem de
streaming direto seja tecnicamente invisivel ao dispositivo/cliente**.
URLs de media podem ser observados na rede ou na API do player.
Oculta-los absolutamente exigiria intermediacao/proxy de media ou
outro protocolo que a app Jellyfin suportasse, contrariando a
condicao de nao usar largura de banda da VPS. Antes de afirmar
segredo de endpoints ou reproducao direta com Jellyfin, testar o
trafego de rede e os metadados transmitidos.

**Estado atual (nao confundir com operacao):**
O Jellyfin foi instalado e o backend Mega do PINK foi copiado para
VivoAPK, mas **nao ha plugin de autenticacao Mega e catalogo dinamico
por Jellyfin user a trabalhar na VPS**. O Jellyfin instalado continua
com login proprio. O requisito so e entregue apos prova real do
ecrã de login Jellyfin + importacao isolada de catalogo + playback
no aparelho, sem configuracao individual no painel.

## Contrato corrigido: o cliente nao configura playlist nem DNS Mega

**Esta e a sequencia exata exigida pelo proprietario:**

```text
APP OFICIAL JELLYFIN (sem modificacao da app)
  1. Servidor = https://serveriptv.duckdns.org
  2. Username = username da subscricao Mega OTT
  3. Password = password da MESMA subscricao Mega OTT
        |
        | login normal do protocolo Jellyfin
        v
JELLYFIN 12.4.0 NA VPS
        |
        | extensao privada de login/catalago VivoAPK
        v
MEGA OTT: identificar subscricao por username no momento do login,
validar password e obter DNS oficiais completos por subscricao
        |
        | criar identidade Jellyfin interna automaticamente, nunca
        | pedir ao revendedor para adicionar contas em Jellyfin
        v
CATALOGO SOMENTE DESSA SUBSCRICAO
  Live TV / Filmes / Series / EPG (conforme suportado)
        |
        v
Reproducao ao Mega OTT pelo cliente, se o Jellyfin e o formato
permitirem Direct Play efetivo sem proxy/relay pela VPS
```

**Nao ha lista de clientes, playlist ou DNS Mega para configurar previamente
no painel Jellyfin.** A integracao descobre/subscreve automaticamente
quando recebe um login valido. Se o Jellyfin precisar de um ID de utilizador
interno, o plugin cria-o e associa-o invisivelmente, de forma segura:
nao se trata de uma segunda conta que o cliente tenha de conhecer.

O DuckDNS e **unico para TODOS os utilizadores**, e sera substituivel
por dominio proprio sem mudar os URLs Mega guardados. O utilizador nunca
introduz `zvpnm.com`, `beuaya.com`, `daizsmart.com`, `fqvpnw.com`,
subdominios, URLs de M3U ou qualquer link do fornecedor.
A unica escolha de DNS, feita internamente, e entre os dois campos
da subscricao recuperados na API. O sistema deve ser multissubscricao
sem configurar uma playlist global com credenciais de um cliente.

**Criterio de aceitacao funcional imediato:** numa instalacao Jellyfin
nova e SEM tuners/manuais/contas de cliente preconfiguradas, autenticar
uma subscricao Mega OTT valida usando somente os dois campos Jellyfin e
ver automaticamente o catalogo desse utilizador. Repetir com outra
subscricao, provar isolamento, renovacao/expiracao e login recusado quando
a password nao corresponde. Nao basta que o endpoint Xtream devolva
sucesso ou que haja biblioteca Jellyfin vazia. A reprodução sem relay
e outro criterio obrigatorio, confirmado em rede e nos dispositivos.

**Estado:** requisito documentado; NAO esta implementado no servidor Jellyfin,
e nao foi testado com login Mega real atraves da app oficial.

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
