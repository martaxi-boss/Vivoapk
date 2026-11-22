# Vivo Player - resultados reais do Ferrari (22/11/2026)

Este documento distingue **provas de rede**, **inspecao de interface** e **ligacao efetiva a uma Smart TV**. O codigo publicado nao contem MAC, Device Key, username nem password reais.

## Evidencia de rede

- [Ferrari #45061831368](https://github.com/martaxi-boss/VPS/actions/runs/45061831368) - dois dominios de teste resolvem por DNS; HTTP na raiz responde 200; HTTPS apresenta falhas distintas de TLS/certificado. Chromium privado Ferrari identificado como instalado e executavel.
- [Ferrari #45062067969](https://github.com/martaxi-boss/VPS/actions/runs/45062067969) - no dominio **No-IP** mencionado por ultimo, a raiz devolveu 200 sem titulo, enquanto `/player_api.php` sem credenciais devolveu 403. No dominio **DuckDNS** anterior, a raiz mostrou apenas o Nginx padrao e `/player_api.php` devolveu 404.
- **Interpretacao:** nao foi confirmada uma API Xtream pronta em nenhuma das duas origens. Um 403 sem credenciais pode ser rejeicao por autenticacao ou por outras politicas. A porta real do servico IPTV nao foi fornecida; nao se fez varrimento de portas.

## Evidencia de engenharia reversa do painel

- [Ferrari #45061953094](https://github.com/martaxi-boss/VPS/actions/runs/45061953094) - Playwright real abriu `https://www.vivo-player.com/playlists`. A resposta da aplicacao foi **403**, apresentando uma pagina de desafio Cloudflare. Portanto, nao se observou o painel autenticado nem qualquer API privada de gestao de playlists.
- A protecao foi respeitada; nao se tentou contornar desafios, ativacao paga ou autorizacoes.
- Evidencia externa de que o painel recebe MAC e Device Key: https://www.vivo-player.com/playlists (formulario indexado publicamente).
- **Nao reutilizar** endpoints nao documentados de IBO/IBOSOL como se fossem do Vivo Player.

## Ferramenta de diagnostico

`tools/xtream_diagnostic.py` verifica DNS, raiz HTTP(S) e a rota convencional `/player_api.php`. Funciona **sem credenciais** e nao extrai listas ou media. A conclusao `not_confirmed_without_credentials` nao significa que o servico esteja necessariamente avariado; significa que falta prova.

Exemplos:

```sh
python3 tools/xtream_diagnostic.py --self-test
python3 tools/xtream_diagnostic.py --url http://servidor-de-teste.example
```

Nao usar URLs com passwords. Para o teste autenticado real, so com credenciais autorizadas num canal privado e preferencialmente TLS valido. Nao enviar segredos para o GitHub publico nem para logs Ferrari publicos.

## O que falta para ligar a Smart TV

1. Confirmar **qual e o servidor Xtream**, o protocolo e a porta corretos; um apontamento DNS sozinho nao instala uma API.
2. Verificar TLS adequado antes de usar credenciais num servidor acessivel pela Internet.
3. Na propria aplicacao Vivo Player, obter a MAC virtual e a Device Key da instalacao do proprietario e, no portal oficial, associar a playlist via os mecanismos normais. Nao publicar estes dados.
4. Com conteudos autorizados, testar login, categorias, EPG e reproducao em Samsung ou LG e registar evidencia.
5. Somente apos capturar, numa sessao propria, as chamadas reais do painel se deve programar uma integracao automatica com endpoints exatos e tests correspondentes.

**Estado final da data:** testes reais de rede e navegador efetuados; engenharia reversa do painel **bloqueada por Cloudflare**; TV **nao ligada**; nenhuma alteracao feita na TV, nos dominios ou na VPS fora dos trabalhos efemeros Ferrari.

## Verificacao adicional de encaminhamento DNS

[Ferrari #45062232933](https://github.com/martaxi-boss/VPS/actions/runs/45062232933):
- O hostname No-IP mais recente **nao aponta para a VPS existente** do Ferrari (comparacao exata, sem publicar os enderecos IP).
- O hostname DuckDNS anterior **aponta para a VPS**, mas mostra a pagina padrao Nginx em HTTP 80.
- Ambos os pedidos `/player_api.php` foram efetuados **sem credenciais**. Um HTTP 403 sem `WWW-Authenticate` nao e prova de Xtream; um 404 na raiz habitual indica que a API nao foi identificada nesse caminho/porta.

## Codigo verificado com o proprio Ferrari

[Ferrari #45062365308](https://github.com/martaxi-boss/VPS/actions/runs/45062365308):
- `tools/xtream_diagnostic.py --self-test`: 8/8 verificacoes locais PASS, processo com codigo de saida zero.
- `tools/xtream_diagnostic.py --url http://<servidor-de-teste>`: no host No-IP mais recente, DNS valido, raiz HTTP 200, `/player_api.php` HTTP 403, classificacao **not_confirmed_without_credentials**, sem envio de password ou Device Key, processo com codigo de saida zero.
- `report.json.status=completed`, mas `quality_gate.status=not_certified`: prova a execucao da ferramenta, nao a ligacao a Smart TV.

**Bloqueio operacional:** nao ha evidencia de um backend Xtream funcional acessivel na origem e porta testadas, nao foi capturada a sessao autenticada do painel Vivo Player e o hostname No-IP nao esta sob o encaminhamento da VPS Ferrari. Nenhuma configuracao de um fabricante terceiro foi modificada.
