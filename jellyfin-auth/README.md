# Integração nativa Jellyfin 12.4 → VivoAPK Mega OTT

**Etapa 1 — conector de autenticação, sem exposição de media na VPS.**

Esta pasta contém um plugin C# original que usa o contrato oficial de plugins
Jellyfin 12.4/.NET 10, com `IAuthenticationProvider`, para validar o login
do cliente no backend **já instalado** em
`/srv/projects/Vivoapk/run/backend.sock` (socket Unix privado).

## Regra de funcionamento

- App oficial Jellyfin: configurar uma vez o único servidor
  `https://serveriptv.duckdns.org`.
- Ecrã normal Jellyfin: apenas username/password da subscrição Mega OTT.
- A implementação consulta `POST /v1/session/resolve`, pelo socket privado,
  através do mesmo motor que servia a APK PINK.
- Subscrições não existentes em Jellyfin podem criar **uma identidade interna
  restringida** sem nova palavra-passe local.
- **Nunca sequestra identidades existentes**: `pink-admin` e contas de outros
  fornecedores não são modificadas.
- **Desativado por omissão**: `PluginConfiguration.Enabled=false`, para não
  alterar os logins de produção até os testes de instalação/rollback.
- Não grava credenciais Mega OTT em logs nem em ficheiros do Jellyfin.
- Nenhum DNS Mega é introduzido pelo cliente ou incorporado na aplicação.

## Estado e limites atuais

Este plugin **não apresenta** canais, filmes e séries no Jellyfin ainda.
A obtenção de catálogo por utilizador e a revogação de sessões ativas quando
a assinatura termina exigem uma segunda integração segura. Não é correto usar
uma playlist Xtream global: Jellyfin indexa canais globalmente e isso pode
misturar subscrições.

A reprodução sem retransmissão de media pela VPS, exigida pelo proprietário,
também continua por confirmar em testes reais. Alguns clientes Jellyfin
precisam que a origem seja acessível diretamente; endereços `http://` do
Mega OTT podem falhar em clientes `https://` ou expor URLs diretas.

## Testes e compilação

CI: `.github/workflows/vivoapk-jellyfin-auth-ci.yml` prepara .NET SDK 10,
compila contra `Jellyfin.Controller==12.4.0`, e executa testes sintéticos
com `HttpMessageHandler` artificial sem usar a API Mega ou credenciais.
O plugin não deve ser instalado no Jellyfin produtivo enquanto faltar
uma revisão de segurança, backup, demonstração de login e o catálogo
individual.

As referências públicas de API foram contrastadas com os plugins oficiais
Jellyfin LDAP e o exemplo MIT Jellyfin/Authelia, sem copiar o código
dos plugins, das suas credenciais ou da sua configuração.
