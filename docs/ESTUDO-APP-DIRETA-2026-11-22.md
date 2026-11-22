# Vivo Player em Smart TV — limite técnico e alternativa aberta

Data: 22/11/2026. Repositório: `martaxi-boss/Vivoapk`.

## Objetivo autorizado do proprietário

Utilizar o **seu** serviço Mega OTT com URL Xtream específica para Samsung/LG e, sempre que possível, o domínio próprio de teste `vivo-team.duckdns.org`, sem depender do portal comercial do Vivo Player. Trabalhar apenas com a própria televisão, contas e conteúdos autorizados. Não publicar credenciais, MAC, Device Key, URLs privadas de subscrição ou binários proprietários.

## Constatações verificadas

1. A televisão mostra **«A conta expirou»** na aplicação Vivo Player, com ecrã de MAC/Device Key e QR de associação. Isto revela um estado de ativação da aplicação comercial, **distinto** da autenticação Xtream do Mega OTT.
2. A documentação pública do Vivo Player exige associação pelo respetivo site e ativação por site ou compra integrada. Referência: https://apps.apple.com/pt/app/vivo-player-smart-iptv-player/id6479256394.
3. Uma pesquisa da árvore atual do nosso repositório `Vivoapk` confirma que **não há pacote real** `.wgt` Samsung, `.ipk` LG, APK Vivo nem captura HAR autorizada. O repositório contém apenas diagnósticos e documentação.
4. O próprio fabricante não publicou, nas fontes verificadas, um contrato de configuração que permita substituir a origem de ativação da aplicação Vivo instalada por um hostname próprio. A alteração do DNS de um servidor nosso **não obriga** uma app instalada a consultar esse servidor.
5. A tentativa anterior de captura pública do portal Vivo via Ferrari encontrou um desafio Cloudflare HTTP 403 (execução [#45061953094](https://github.com/martaxi-boss/VPS/actions/runs/45061953094)). Não há análise válida de endpoints autenticados.
6. O GitHub privado PinkIPTV contém um cliente Mega OTT e um cliente Xtream funcionalmente validados na sua própria arquitetura; isso **não cria** licença para o Vivo Player nem instala um player numa TV Samsung/LG.

## Solução prática sem licença Vivo — player independente livre

Foi identificado o projeto **Ultra TV**: https://github.com/vantaflow/ultra-tv, licença **MIT** (ver `LICENSE` no upstream). Inclui código para **Samsung Tizen 5.5+ (aproximadamente 2020+)** e **LG webOS 6+ (aproximadamente 2021+)**. Referências upstream: `tizen/README.md`, `webos/README.md`, `web/src/net/xtream.ts`.

No módulo `web/src/net/xtream.ts`, as operações são dirigidas a **`player_api.php` na origem introduzida pelo próprio utilizador**, com username e password, sem dependência do MAC/Device Key do Vivo Player. Existem construtores de rotas para Live, VOD e séries. Isto é **verificação de código**, não reprodução física com o serviço do proprietário.

A página GitHub Releases disponível na consulta de 22/11/2026 apresentava APKs Android para a release estável `v1.3.72`, mas **não foram encontrados ficheiros `.wgt` nem `.ipk` anexados às 8 primeiras releases consultadas**. Para Samsung/LG seria necessário criar o respetivo pacote e instalar em modo de desenvolvimento. Não afirmar instalação imediata a um clique.

### Samsung (Tizen)

Construir widget `.wgt` a partir de `tizen/scripts/build.mjs`, assinar com certificado Samsung do **DUID da TV**, ativar Developer Mode e instalar com Tizen Studio. Requer identificação do modelo e acesso à TV na rede local. Não há execução nem assinatura certificada nesta análise.

### LG (webOS)

Construir pacote `.ipk` com `webos/scripts/build.mjs`, ativar a aplicação Developer Mode na TV e instalar através de `ares-install`. A sessão de desenvolvimento tem expiração e exige renovação. Não há execução nem instalação certificada nesta análise.

### Ligação de servidor

Se se adotar uma app independente, a URL de Xtream a usar na Samsung/LG é o **valor Samsung/LG fornecido pelo proprietário e atribuído à subscrição Mega**, com o **username/password da própria linha**, preferencialmente com HTTPS válido quando disponível. Nunca copiar essas credenciais para um repositório público nem adicioná-las a jobs Ferrari públicos. O próprio serviço Mega tem de permitir reprodução pelo dispositivo.

## O que não está feito

- Não se modificou nem desbloqueou a licença comercial do Vivo Player.
- Não se extraiu nem analisou um binário comercial Vivo da televisão.
- Não se instalou nem configurou uma aplicação nova na TV.
- Não se testou reprodução do Mega OTT na TV; nem foi introduzida qualquer credencial na Ferrari pública.
- O domínio próprio `vivo-team.duckdns.org` não está validado como API Xtream no porto/caminho padrão; foi anteriormente observado HTTP 404 em `/player_api.php`.

## Resultado e decisão técnica

**Dentro do Vivo Player instalado:** não é possível comprovar a substituição do servidor/ativação com os materiais atualmente acessíveis. Continuar a tentar por DNS sem pacote ou tráfego real não é engenharia reversa.

**Para ter controlo independente do servidor e do leitor:** a via demonstrada por código público é instalar uma aplicação aberta compatível, sem alterar a licença do Vivo Player. Esta é uma alternativa separada e não deve ser representada como desbloqueio do Vivo existente. Exige TV Samsung/LG identificada, pacote instalável e teste físico.
