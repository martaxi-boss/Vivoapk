# Reutilização segura da integração Mega OTT da PinkIPTV

Data: 2026-11-22. Projeto destino: **Vivoapk**. Origem de referência: projeto **privado** `martaxi-boss/pink-iptv`. Nenhuma credencial foi copiada.

## Descobertas verificadas no código privado

- `backend/app/clients/mega.py`: utiliza a API Mega OTT com autenticação Bearer no backend e `GET /v1/subscriptions/{id}`; a resposta modelada contém `dns_link`, `dns_link_for_samsung_lg`, `username` e metadados da subscrição.
- `backend/app/clients/xtream.py`: valida autenticação de assinantes na API Xtream `/player_api.php` usando nome de utilizador e palavra-passe autorizados, com User-Agent estável `PINK-IPTV/0.1`.
- `backend/app/services/mappings.py` + `backend/app/models.py`: preservam o campo `dns_link_samsung_lg` numa tabela PostgreSQL. Essa origem é distinta de `dns_link`.
- `backend/app/services/session.py`: o login atual Android devolve `xtream_base_url=mapping.dns_link`; **não** devolve automaticamente `dns_link_samsung_lg`.
- `clients/extreme/overlay/PinkVault.kt`: armazenamento AES-GCM no Android Keystore de uma instalação da aplicação. **Não** é cofre central de API da Mega OTT.
- `.env.example` e `backend/app/config.py`: o token real Mega é lido em runtime através de `MEGA_OTT_API_TOKEN`. O repositório privado não publica esse token.
- `docs/MEGA_OTT_INTEGRATION.md` e `PROJECT_STATE.md`: documentam prova anterior **real** de autenticação Mega/Xtream com credenciais legítimas; isso não prova a situação atual de uma determinada linha, nem a compatibilidade com Vivo Player Samsung/LG.

## Implicação para Vivo Player Samsung / LG

1. Primeiro consultar a **própria subscrição Mega** com o token de backend autorizado e obter **o valor exato** `dns_link_for_samsung_lg`, sem deduzir subdomínios nem substituir pelo DuckDNS.
2. Validar que a subscrição está ativa e que o endereço nativo serve o protocolo Xtream esperado. Não confundir `dns_link_for_samsung_lg` com um portal MAC/Stalker.
3. Na página **oficial** do Vivo Player, associar à nossa instalação licenciada a URL Xtream nativa, username e password, usando o MAC virtual/Device Key obtidos diretamente da nossa TV.
4. Testar reprodução de material autorizado, categorias e EPG na TV. Uma ligação de playlist não remove a licença/ativação paga do Vivo Player.
5. Só para futura automatização de painel, estudar uma sessão própria autorizada; nenhuma API privada Vivo foi confirmada nesta auditoria.

**Ponto crítico:** `vivo-team.duckdns.org` é um domínio controlado pelo proprietário. É útil para uma futura interface/gateway próprio, mas não é automaticamente um servidor Xtream nem pode substituir os links oficiais por subscrição que a Mega devolve. Não reescrever hosts, certificados nem pedidos de vídeo para forçar uma equivalência.

## Ferramenta preparada

`tools/mega_samsung_lg_lookup.py` é um cliente independente, **read-only** e sem dependências externas. Utiliza:
- `MEGA_OTT_API_TOKEN`, exclusivamente a partir do ambiente privado;
- `MEGA_OTT_API_BASE` (opcional; base HTTPS Mega);
- o ID de subscrição autorizado no momento da execução.

Valida exatamente `type=M3U`, identificador da subscrição, username e origem `dns_link_for_samsung_lg`. **Falha explicitamente** quando não há campo nativo Samsung/LG: não usa automaticamente o host Android/Windows. Rejeita URLs com credenciais embutidas, redirecionamentos da API, esquemas inválidos, respostas demasiado grandes e escrita sobre ficheiros existentes.

Exemplo **apenas num ambiente privado** onde o token já está injetado de forma segura e não aparece no terminal:

```sh
python3 tools/mega_samsung_lg_lookup.py --self-test
python3 tools/mega_samsung_lg_lookup.py --subscription-id ID_DA_LINHA_AUTORIZADA \
  --out-private /caminho/privado/vivo-subscription.json
```

`ID_DA_LINHA_AUTORIZADA` representa um ID numérico real conhecido apenas no ambiente privado. O ficheiro de saída é criado com permissões **0600** e contém origem nativa e username, **não** o token API nem a password do cliente.

Nunca executar com segredos num pedido GitHub Actions de repositório público, nem publicar o ficheiro JSON gerado, MAC, Device Key ou captura HAR.

## Limites verificados

- Não foi disponibilizado a esta execução o valor do token Mega OTT, a palavra-passe de subscrição, nem a MAC virtual/Device Key da instalação Vivo Player.
- O GitHub não permite ler em claro o conteúdo de GitHub Actions Secrets; um repositório e o `PinkVault.kt` por si só não desbloqueiam estas credenciais.
- Não existe evidência de que o Vivo Player já esteja associado; a aplicação comercial e o respetivo painel continuam independentes do nosso backend.
- A API/porta Xtream no endereço `vivo-team.duckdns.org` permanece não confirmada (responde com Nginx padrão/404 nos testes anteriores à porta 80).
- Portanto, o trabalho entrega **código de interoperabilidade verificável**, não uma sessão autenticada real nem reprodução na TV.
