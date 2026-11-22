# Vivo Player — interoperabilidade Smart TV

Projeto independente para estudar a integração de um **servidor IPTV próprio, com conteúdos autorizados**, com a aplicação comercial **Vivo Player** instalada numa Smart TV Samsung (Tizen) ou LG (webOS), sem criar uma aplicação de substituição.

## Objetivo
- Identificar e documentar a interface de configuração de playlists do Vivo Player (MAC virtual + Device Key, quando disponíveis na aplicação).
- Confirmar o modo de ligação Xtream Codes oferecido oficialmente.
- Estudar apenas comunicações e sessões próprias, autorizadas, para interoperabilidade.
- Preparar um conector **independente** para configurar uma instalação própria, caso os testes o justifiquem.
- Testar numa Smart TV real com conteúdos que temos o direito de disponibilizar.

## Estado em 22-11-2026 (evidencias reais)
- [x] Motor Ferrari e ponte GitHub/VPS executados, sem reinstalacoes: [#45061831368](https://github.com/martaxi-boss/VPS/actions/runs/45061831368).
- [x] Testados ambos os dominios: HTTP raiz responde, mas as falhas de TLS impedem HTTPS validado.
- [x] Investigado o acesso nao autenticado a `/player_api.php`: No-IP devolveu **403**; DuckDNS, **404** ([#45062067969](https://github.com/martaxi-boss/VPS/actions/runs/45062067969)).
- [x] Verificada a rota DNS: o host No-IP mencionado mais recentemente **nao aponta para a VPS Ferrari**; o DuckDNS anterior aponta para ela, mas so exibe o Nginx padrao ([#45062232933](https://github.com/martaxi-boss/VPS/actions/runs/45062232933)).
- [x] Vivo Player `/playlists` abriu em Chromium Ferrari, mas o conteudo ficou protegido por **Cloudflare 403**, sem acesso ao painel autenticado ([#45061953094](https://github.com/martaxi-boss/VPS/actions/runs/45061953094)).
- [x] Codigo real `tools/xtream_diagnostic.py` submetido e **executado no Ferrari**: 8 autoverificacoes passaram e o teste sem credenciais devolveu `not_confirmed_without_credentials` ([#45062365308](https://github.com/martaxi-boss/VPS/actions/runs/45062365308)).
- [ ] Engenharia reversa dos endpoints autenticados do Vivo Player: nao comprovada.
- [ ] Ligacao da playlist a uma TV Samsung/LG e reproducao: nao testadas; exigem instalacao propria, Device Key e configuracao autorizada.

**Relatorio completo:** [docs/RESULTADOS-2026-11-22.md](docs/RESULTADOS-2026-11-22.md).

**Aviso:** a aplicação Vivo Player continua sujeita às suas condições e licença de ativação. Este projeto não contorna pagamentos, DRM, CAPTCHAs ou acessos de terceiros.

## Organização
- `docs/INVESTIGACAO.md`: evidências já obtidas, limites e roteiro de testes.
- `.gitignore`: evita publicar segredos e capturas sensíveis.

## Segurança
**Este repositório é público.** Nunca publicar MAC real da TV, Device Key, URL com credenciais, username, password, tokens de sessão, cookies, CAPTCHA, playlists privadas, capturas HAR com dados pessoais, chaves SSH, conteúdos sujeitos a direitos nem pacotes proprietários não autorizados. Mesmo conteúdo codificado em base64 continua público.

Guardar quaisquer dados de teste em canal privado e trabalhar apenas com dispositivos/serviços autorizados. Os pedidos para a ponte Ferrari no GitHub público devem conter exclusivamente entradas sintéticas ou não confidenciais.

## Arquitetura
```
Nosso servidor Xtream (autorizado)
        |
        | API Xtream Codes
        v
Vivo Player instalado na Samsung/LG
        ^
        |
Painel oficial do Vivo Player (associação MAC + Device Key)
```

A configuração oficial e a eventual automatização do painel são problemas distintos. Um servidor Xtream operacional **não autoriza automaticamente a TV** nem ativa a licença da aplicação.

## Ferramentas
A engenharia reversa e os testes remotos devem utilizar o **Motor Ferrari universal existente** (REA/Playwright), via GitHub Actions para a VPS; não instalar motores duplicados e não modificar outros projetos.
