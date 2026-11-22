# PINK IPTV -> Vivoapk — transferencia e descontinuacao

Decisao do proprietario (22/11/2026): o PINK IPTV e descontinuado apos
aproveitar o que funciona; Vivoapk e a fonte de verdade do novo trabalho.

**Origem auditada:** martaxi-boss/pink-iptv, main no commit
32f3638d3e02f01558ef86127d865956f5c52e64.
**Destino:** martaxi-boss/Vivoapk, pasta backend/.

## Ja migrado (codigo, nao dados reais)
- cliente Mega OTT Bearer, importacao por subscricao, pesquisa paginada;
- autenticacao Xtream, seguranca de resolucao DNS e transporte IP pinado;
- armazenamento sem password da subscricao, rate limiting e tokens curtos;
- selecao do destino atraves da plataforma: Android/Android TV/Windows/ios
  recebem dns_link; Tizen/webOS recebem dns_link_for_samsung_lg;
- testes artificiais do cliente Mega, Xtream, origem por plataforma e API;
- esquema inicial das tabelas no NOVO banco, nao no PINK.

## Excluido propositadamente
- VPN WireGuard, gateway e instalacoes da antiga app;
- APK e overlays PINK, marca, artes e codigo Extreme InfiniTV GPL;
- passwords, tokens, chaves, MAC de televisores, playlists e dados privados;
- database antiga, estado da VPS PINK e conteudos media;
- video relay pelo Jellyfin.

## Limites e estado
Este commit **nao** liga o Jellyfin oficial ao Mega OTT: a app Jellyfin
continua com utilizador e password Jellyfin. O backend Vivoapk e Xtream
e exige app cliente compatível para login com credenciais Mega OTT.

Nenhuma API Vivoapk nova esta em producao, nem ha aplicacao Tizen/webOS
ou iOS criada nesta migracao. Testes de CI sao sinteticos.

## So depois, eliminar o PINK
1. Aprovar CI e testes de API no novo repo.
2. Instalar backend Vivoapk na propria pasta, isolado dos outros projetos.
3. Configurar HTTPS e dominio de controlo depois de verificar infraestrutura.
4. Validar login e reprodução DIRETA com linha autorizada Mega OTT em aparelhos.
5. Confirmar que todas as dependencias, servicos, certificados, bases e
   backups PINK foram migrados ou descontinuados com seguranca.
6. Desativar servicos antigos; so entao arquivar/apagar o repositorio PINK.

A opcao do proprietario de eliminar o projeto NAO significa que seja
seguro destruir os dados de quem ainda depende dele. Nenhuma remocao foi feita.

## Execucao real Ferrari — 22/11/2026 (cofre privado)

- Inventario privado read-only: [Ferrari #45078381398](https://github.com/martaxi-boss/VPS/actions/runs/45078381398). A instalacao PINK em `/srv/projects/pink-iptv` ocupa aproximadamente **337 MiB** (backend 131 MiB, Android 112 MiB, testes/device-proof 88 MiB). Os servicos `pink-iptv-backend.service` e `pink-vpn.service` continuam **ativos**; PostgreSQL tambem esta ativo. Cofre raiz `/root/project-secrets` com permissao 0700.
- Confirmacao de origem privada (nomes de chaves, nunca valores): [Ferrari #45078495214](https://github.com/martaxi-boss/VPS/actions/runs/45078495214). `backend.env` contem APP_ENV, DATABASE_URL, SESSION_SIGNING_KEY, MEGA_OTT_API_BASE, SESSION_TTL_SECONDS. `secrets.env` contem MEGA_OTT_API_TOKEN. A VPN tem ficheiro separado e NAO foi copiada para o novo projeto.
- **Cofre VivoAPK criado e validado:** [Ferrari #45078670731](https://github.com/martaxi-boss/VPS/actions/runs/45078670731). Foi criada a pasta privada **`/root/project-secrets/VIVOAPK`** (0700), contendo `backend-auth.env`, `subscriber-mappings.csv` e `migration-manifest.json`, todos 0600. O token Mega OTT e a origem da API foram transferidos apenas dentro da propria VPS. A chave de assinatura de sessoes VivoAPK foi gerada de novo. Foram preservadas **4 subscricoes**, incluindo **4 enderecos Samsung/LG** e os DNS genericos. Nenhum valor de credencial, username ou URL de subscricao foi publicado no GitHub ou nos logs de resultado.
- A fonte PINK e o seu banco de dados ficaram inalterados. O codigo do backend VivoAPK e os testes CI continuam no proprio repositorio e independentes da origem PINK.
- **Pendente:** a instalacao de um novo role/banco PostgreSQL, runtime Python independente, unit systemd, endpoint HTTPS VivoAPK e teste real de login/reproducao. Uma tentativa de iniciar esta etapa atraves da ferramenta foi bloqueada pela respetiva verificacao de seguranca **antes da execucao**. Nao ha prova de backend VivoAPK instalado na VPS, nem apps para Samsung/LG/iOS validadas.
- **Sem desinstalacao PINK nesta fase**: so desativar ou remover os servicos/dados do projeto depois de o VivoAPK responder com autenticacao real autorizada, mapeamento dos dois DNS, cliente com playback direto, comprovacao dos consumidores antigos e backup recuperavel. A disponibilizacao de um cofre nao prova continuidade de servico.
- O timer `project-secrets-offsite-backup.timer` foi identificado, mas a sua execucao, cobertura da nova pasta e restaurabilidade ainda precisam de verificacao; nao marcar o cofre como copiado offsite sem prova.

## Conferência de DNS base (Ferrari 22/11/2026)

Com a leitura do cofre privado [#45079375660](https://github.com/martaxi-boss/VPS/actions/runs/45079375660), foram observadas as bases comuns zvpnm.com (geral) e daizsmart.com (Samsung/LG), com 4 subdomínios distintos em cada campo, sem qualquer alteração dos URLs completos dos clientes. Ver [relatório DNS real](PINK_DNS_BASE_VERIFICADO_20261122.md) e as fontes Android em reference/pink-android-core/.
