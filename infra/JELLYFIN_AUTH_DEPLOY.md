# Instalação controlada: Jellyfin ↔ VivoAPK (ponto 2)

Ferrari usa apenas o repositório [martaxi-boss/Vivoapk](https://github.com/martaxi-boss/Vivoapk)
e a ponte GitHub Actions já existente, **sem criar projetos nem coordenadores novos**.

O script \`infra/deploy_jellyfin_auth.py\` fornece seis fases explícitas:
\`preflight\`, \`stage\`, \`install\`, \`activate\`, \`verify\` e \`rollback\`.
Cada fase exige \`--sha\` com o SHA Git completo e é executada pela Ferrari
diretamente na VPS através de código publicado e verificado por hash.
Nunca colocar segredos na requisição GitHub.

- **preflight:** verifica serviços ativos, sockets Unix isolados, permissões,
  existência de administrador no banco Jellyfin e no mínimo 5 GiB livres.
- **stage:** preserva integralmente o checkout ativo do backend, descarrega
  a revisão pinada para um worktree Git próprio em \`/srv/projects/Vivoapk/jellyfin-auth/checkouts/<SHA>\`, permite apenas alterações documentais em \`backend/README.md\` e compila o plugin
  C# para Jellyfin 12.4 com .NET 10. Usa apenas cache local
  \`/srv/projects/Vivoapk/jellyfin-auth/build-cache/\`; executa os testes
  sintéticos, cria release versionada dentro do VivoAPK. Não toca Jellyfin.
- **install:** pára brevemente **só** \`jellyfin-pink.service\`, faz backup
  completo, privado e root-only dos diretórios \`data/\` e \`config/\` de
  Jellyfin, e instala DLL+manifest na pasta normal
  \`/srv/projects/jellyfin-server/data/plugins/VivoAPK Mega OTT Auth_0.1.0.0/\`.
  Arranca Jellyfin com autenticação Mega **desativada por defeito**, prova
  resposta do socket, carregamento do plugin e integridade do registo de
  administrador. Em falha restaura dados e configuração a partir do backup.
- **activate:** verifica release e backup, pára brevemente só Jellyfin,
  grava \`Jellyfin.Plugin.VivoapkMegaAuth.xml\` com \`Enabled=true\`, reinicia
  Jellyfin e prova carregamento, socket e preservação do registo admin.
  Em falha repõe o backup integral do Jellyfin.
- **verify:** inspeção sem escritas: serviços, DLL, configuração e sockets.
- **rollback:** pára Jellyfin, restaura o backup do seu estado e reinicia.
  PINK IPTV, VPN, Nginx, certificados, DNS e backend VivoAPK não são
  alterados por nenhuma destas fases.

A existência do administrador no banco é confirmada sem revelar passwords
nem hashes. Sem a palavra-passe administrativa original disponível no cofre,
não se declara um teste de login admin **real** apenas por conferir o seu
registo. A autenticação foi validada separadamente num Jellyfin 12.4
descartável com duas identidades fictícias no
[CI #45086626328](https://github.com/martaxi-boss/Vivoapk/actions/runs/45086626328).

**A ativação do plugin não cria automaticamente bibliotecas privadas**.
O ponto 3 — catálogo individual Live/VOD/Séries/EPG — permanece por
implementar e provar. O teste de login com uma subscrição Mega OTT nova
e ativa também requer uma linha de teste autorizada, não credenciais
sintéticas.
