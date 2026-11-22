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

## Alternativas abertas com pacotes de instalação já publicados (consulta 22/11/2026)

São opções independentes **não instaladas**, a considerar apenas se o proprietário aceitar utilizar outro leitor em vez do Vivo Player:

- **Samsung Tizen:** [EN TV Player](https://github.com/Nur-allhi/en-tvplayer), licença MIT; a release [v3.2.0](https://github.com/Nur-allhi/en-tvplayer/releases/tag/v3.2.0) contém `EN-IPTV_Player_v3.2.0.wgt` (479 660 bytes) com entrada Xtream e M3U. A instalação no hardware concreto, assinatura/permissões e compatibilidade com a versão Tizen ainda não foram testadas. Fonte: GitHub API da release e README.
- **LG webOS:** [TiviBro](https://github.com/ohmybahgosh/TiviBro), licença MIT; a release [v0.6.0-alpha.199](https://github.com/ohmybahgosh/TiviBro/releases/tag/v0.6.0-alpha.199) contém `app.tivibro.tv_0.5.10199_all.ipk` (8 036 040 bytes), com ligação Xtream/M3U. A versão é **alpha**, e a instalação/reprodução real permanecem por testar. Fonte: GitHub API da release e README.

**A existência do artefacto publicado não é uma prova de funcionamento na TV do proprietário.** O modelo/versão de sistema operativo da TV não foi confirmado. Não foram fornecidos IP local, modo de desenvolvimento, certificados/dispositivo nem acesso remoto à TV. Não realizar instalações sem esses pré-requisitos e sem consentimento no próprio dispositivo.

## Execuções Ferrari REA desta investigação

1. [#45064744786](https://github.com/martaxi-boss/VPS/actions/runs/45064744786) — pedido `analyze_javascript_application` sobre um módulo TypeScript MIT falhou com `isError=true`; **não** gerou relatório aproveitável. Não confundir esta falha com falta de suporte Xtream no projeto.
2. [#45064863777](https://github.com/martaxi-boss/VPS/actions/runs/45064863777) — análise estrutural através do `capture_process_scenario` (Python privado Ferrari). Foram observados **7/8** campos, mas um teste tinha um erro na expressão regular. O processo terminou com `exit_code=2`, embora o wrapper Ferrari tenha reportado `status=completed`. Esta execução **não** é uma validação funcional.
3. **[#45064996879](https://github.com/martaxi-boss/VPS/actions/runs/45064996879)** — repetição corrigida sobre `web/src/net/xtream.ts` do Ultra TV, upstream MIT. `report.json.status=completed`, `FERRARI_SKILL_END_TO_END=PASS`, `REA process exit.code=0`, **8/8 verificações estáticas verdadeiras**: origem `player_api.php`, query `username`, query `password`, handshake, Live, VOD, Series e EPG. SHA-256 do módulo ensaiado `4877c0037667463cc6a5f197e6ebe2269fc82a7f30558755e46f8c2c985c4054`.

Em `#45064996879`, `quality_gate.status=not_certified`: o teste **não** prova execução de um player completo, instalação, ligação física a uma Samsung/LG, credenciais válidas do Mega, nem análise do binário proprietário Vivo. A prova é exclusivamente uma verificação estrutural do código de uma **alternativa aberta**.
