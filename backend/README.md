# Vivoapk — backend Mega OTT/Xtream

Codigo migrado seletivamente do projeto do proprietario martaxi-boss/pink-iptv,
main 32f3638d3e02f01558ef86127d865956f5c52e64 (22/11/2026).
Esta copia e independente e NAO depende do repositorio antigo em runtime.

## O que faz
- POST /v1/session/resolve recebe username, password e platform.
- Importa por id (CLI) ou descobre por username na API Mega OTT com token privado.
- Confirma a conta no player_api.php do fornecedor, sem retransmitir conteudos.
- Seleciona dns_link para android, android_tv, windows, ios; seleciona
  dns_link_for_samsung_lg para tizen e webos, SEM fallback entre origens.
- Conserva protecao contra SSRF, DNS pinning, limites de autenticacao e
  tokens temporarios. Nunca guarda passwords Mega de clientes.

## Selecao de DNS com o menu do fornecedor

Os domínios exibidos ao criar M3U no painel Mega OTT (por exemplo
zvpnm.com, fqvpnw.com, fhvpnw.com ou beuaya.com) nao sao uma
whitelist de origens permitidas no login. A API retorna o URL inteiro.
O backend valida a forma/seguranca do URL, preserva-o exactamente,
escolhe genericamente dns_link para Android/Windows/iOS (iOS por testar)
e dns_link_for_samsung_lg para Smart TVs Tizen/webOS nativas.
A plataforma e configuracao interna da app, nao um terceiro campo
visivel para o cliente. O menu de disponibilidade nao e servido pelo
backend: consultar sempre o painel Mega para criar novas linhas.
Consultar docs/MEGA_MENU_PLATAFORMAS_LOGIN.md para evidencias e limites.

## Testes artificiais
Com Python 3.12 no ambiente de desenvolvimento:

    python -m pip install ".[dev]"
    python -m pytest -q

Nenhum teste da CI utiliza contas reais ou comunica com o Mega OTT.

## Configuracao privada (já instalada na VPS)
Valores de ambiente necessarios:

    APP_ENV=production
    DATABASE_URL=postgresql+psycopg://...
    SESSION_SIGNING_KEY=<segredo privado com pelo menos 32 bytes>
    MEGA_OTT_API_TOKEN=<token privado do revendedor>
    MEGA_OTT_API_BASE=https://megaott.net/api

Os dados e segredos nunca devem ser publicados no GitHub ou em logs.
Executar migracoes no banco PRIVADO do novo projeto apenas depois de auditoria:

    alembic upgrade head

Servico real instalado a partir do GitHub, com ambiente Python proprio e base PostgreSQL independente:

    /srv/projects/Vivoapk/source
    /srv/projects/Vivoapk/.venv
    systemctl status vivoapk-backend.service
    /srv/projects/Vivoapk/run/backend.sock  (Unix socket interno)

Nao foi criada uma porta HTTP publica para esta API. A prova e revisao instalada constam de docs/DEPLOY_GITHUB_VPS_20261122.md.

O hostname vivo-team.duckdns.org ainda nao esta provado como backend Vivoapk.
O hostname serveriptv.duckdns.org hospeda Jellyfin SEPARADO.
Quatro mapeamentos de subscricoes reais foram copiados da base PINK e verificados no PostgreSQL Vivoapk, sem passwords de cliente. Ainda nao ha login Jellyfin, catalogo por utilizador nem playback real provados. Nao foram importados media para a VPS.

## Exemplo da API (dados totalmente ficticios)

    {"username":"cliente-exemplo","password":"palavra-ficticia","platform":"webos"}

A resposta de sucesso contem xtream_base_url apenas depois da validacao
Xtream e um session_token curto. A aplicacao reproduz junto do Mega OTT,
nao pela VPS. iOS permanece sujeito a teste real com um cliente concreto.
