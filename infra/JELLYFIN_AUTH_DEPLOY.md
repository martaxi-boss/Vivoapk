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

## Resultado executado na VPS — 23/11/2026

- **Pré-verificação**: [Ferrari #45089297971](https://github.com/martaxi-boss/VPS/actions/runs/45089297971) PASS, serviços e conta administrativa presentes.
- **Fonte Git pinada para a release compilada e instalada:** `f1bb21928dd499b53a40fbfb105ce6018af9fb07`, com revisão de plugin C# própria e checksum da DLL verificado contra o build.
- **Build isolado no worktree VivoAPK:** não alterou o checkout do backend; [auditoria binária #45089887346](https://github.com/martaxi-boss/VPS/actions/runs/45089887346) confirmou DLL, manifesto Jellyfin 12.4 e SHA.
- **Instalação:** [Ferrari #45090003933](https://github.com/martaxi-boss/VPS/actions/runs/45090003933) `installed_disabled_verified` com backup privado integral, Jellyfin operacional, plugin carregado e registo `pink-admin` preservado.
- **Ativação:** [Ferrari #45090113991](https://github.com/martaxi-boss/VPS/actions/runs/45090113991) `plugin_activated_healthy`, `enabled_config=true`, sem alterar o PINK; o plugin passou a aceitar a delegação para VivoAPK.
- **Verificação independente:** [Ferrari #45090435057](https://github.com/martaxi-boss/VPS/actions/runs/45090435057) `installed_verified`, `plugin_enabled=true`, sockets Jellyfin/VivoAPK HTTP 200, registo do admin presente, serviços protegidos ativos. `live_customer_login_proved=false`, `catalogue_not_implemented=true`.

**Conclusão do ponto 2:** instalação e ativação técnicas concluídas. Ainda não está certificado o login de uma subscrição real no Jellyfin, a playlist individual, nem a reprodução sem encaminhar vídeo pela VPS. Não retirar o PINK. O teste administrativo com credencial real também não foi executado; preservação do registo não substitui esse teste.
