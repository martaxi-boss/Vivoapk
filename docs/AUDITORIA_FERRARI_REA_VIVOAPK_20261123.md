# VivoAPK — auditoria profunda Ferrari / REA / Ruflo, fecho de infraestrutura

**Data:** 23/11/2026  
**Versão efetivamente instalada e testada:** `578f0454f8a4ef2e6dbeefb11eb0089afe5dd5d0`  
**Âmbito:** backend VivoAPK, plugin de autenticação Jellyfin, piloto HLS e metadados, permissões Unix, HTTPS, CI, Nginx legado e backups. **Não é certificação de reprodução Android/iOS/TV ou de catálogo multiutilizador.**

## Provas cronológicas verificadas

| Verificação | Evidência | Resultado |
|---|---|---|
| Auditoria inicial REA na VPS | [VPS #45190935917](https://github.com/martaxi-boss/VPS/actions/runs/45190935917) | Identificou socket `0666`, capa ausente, HLS piloto experimental e serviços principais ativos |
| Identidade real do socket e dependências | [VPS #45191441518](https://github.com/martaxi-boss/VPS/actions/runs/45191441518) | Proprietário `vivoapksvc`, grupo `www-data`, pasta setgid `2770`, Jellyfin com grupo `www-data` |
| Correção imediata do socket | [VPS #45192554293](https://github.com/martaxi-boss/VPS/actions/runs/45192554293) | `0666` → `0660`, acesso HTTP 200 como utilizador Jellyfin |
| CI do código de endurecimento e imagem | [build](https://github.com/martaxi-boss/Vivoapk/actions/runs/45193412852), [infra](https://github.com/martaxi-boss/Vivoapk/actions/runs/45193412984), [Jellyfin real descartável](https://github.com/martaxi-boss/Vivoapk/actions/runs/45193412894) | PASS para testes criados, incluindo imagem PNG nativa e recusa de acesso cruzado entre duas contas sintéticas |
| Verificação do código realmente implantado | [VPS #45195012849](https://github.com/martaxi-boss/VPS/actions/runs/45195012849) | Checkout `578f045`, binário compilado = instalado, imagem incorporada e plugins ativos |
| Endurecimento persistente no MESMO systemd | [VPS #45195426400](https://github.com/martaxi-boss/VPS/actions/runs/45195426400) | PASS. Adicionado `ExecStartPost`, reiniciado só `vivoapk-backend.service`, socket `0660` e acesso Jellyfin testados; backup e rollback guardados |
| Auditoria cruzada assistida Ruflo/REA | [Teste #45196155845](https://github.com/martaxi-boss/VPS/actions/runs/45196155845), [revisão #45196352770](https://github.com/martaxi-boss/VPS/actions/runs/45196352770), [fecho #45196549188](https://github.com/martaxi-boss/VPS/actions/runs/45196549188) | 14/14 verificações de INFRAESTRUTURA, um executor REA real, um revisor de papel distinto, hash SHA-256 `609b8b389f53a443bb9ed1066aeeffcb4b0ae807890d76096774431e16f3e087`, estado `completed` |
| Confirmação do resíduo Nginx PINK | [VPS #45197101079](https://github.com/martaxi-boss/VPS/actions/runs/45197101079) | Vhost legado tinha apenas nomes Pinky, não referia VivoAPK nem `serveriptv.duckdns.org`; Nginx válido |
| Desativação reversível do vhost antigo | [VPS #45197504440](https://github.com/martaxi-boss/VPS/actions/runs/45197504440) | Removido apenas symlink de `sites-enabled`, alvo/certificados preservados, `nginx -t` e HTTPS Jellyfin HTTP 200 PASS |
| Código GitHub principal alinhado com implantação | [Commit de produção](https://github.com/martaxi-boss/Vivoapk/commit/578f0454f8a4ef2e6dbeefb11eb0089afe5dd5d0) | `main` avançou em fast-forward sem perder commits, antes deste ajuste documental |
| Quatro pipelines pós-alinhamento | [Backend](https://github.com/martaxi-boss/Vivoapk/actions/runs/45196903854), [infra](https://github.com/martaxi-boss/Vivoapk/actions/runs/45196904034), [Jellyfin real](https://github.com/martaxi-boss/Vivoapk/actions/runs/45196904065), [plugin .NET](https://github.com/martaxi-boss/Vivoapk/actions/runs/45196905034) | PASS |

## Estado final específico na VPS

- `vivoapk-backend.service`, `jellyfin-pink.service`, `nginx.service` e `postgresql.service`: ativos. Socket privado `/srv/projects/Vivoapk/run/backend.sock` modo `0660`, proprietário `vivoapksvc`, grupo `www-data`; Jellyfin HTTP 200 via conta de serviço. Certificado público `serveriptv.duckdns.org` válido.
- Instalação em `/srv/projects/Vivoapk`; sem novos serviços, proxy/relay de vídeo, modelo local pago, APKS ou instalação global. `ExecStartPost` do systemd existente invoca `infra/secure_runtime_socket.py` após bind, com verificações de proprietário, grupo e symlink.
- A revisão `578f045` está na VPS; o DLL do Jellyfin coincide em hash com o compilado no próprio projeto. Plugin Enabled=true, PilotLiveChannels=true, ProvisionPrivateLibraries=false, MonitorEntitlements=false.
- O piloto limita-se a **uma subscrição de cada vez**, snapshot efémero até cinco canais, playlist HLS direta e **capa PNG genérica**; não contém logos reais por canal.
- `pink-iptv-backend.service`, `pink-vpn.service` e `/srv/projects/pink-iptv` estão ausentes. O serviço `jellyfin-pink.service` pertence ao Jellyfin ativo e não foi removido. Symlink Nginx PINK antigo foi desativado e guardado em `/srv/projects/Vivoapk/backups/legacy-nginx-20261123`, sem apagar certificados ou alvo.
- Backup para rollback da unidade systemd em `/srv/projects/Vivoapk/backups/socket-hardening-578f045`; backups privados anteriores do instalador Jellyfin preservados.
- **Atenção à tentativa de atualização que reportou falha:** o pós-teste constatou que a revisão alvo já estava de facto instalada, serviços e backup saudáveis. Não repetir instalação só para obter um job verde, nem deduzir avaria a partir do invólucro Ferrari.

## Lacunas que continuam abertas (não declarar 100%)

1. **Reprodução no dispositivo**: sem prova de arranque de RTP1/canais portugueses numa aplicação Android física após atualização; `DirectPlay=true` nos metadados não comprova stream de vídeo efetivo.
2. **Não encaminhar vídeo pela VPS**: requer observação de rede durante playback real no cliente; ainda não houve medição certificada.
3. **Catálogo completo e privado por subscrição**: endpoints backend Live/VOD/Séries/EPG e bibliotecas fictícias testados, mas não há integração final nativa multiutilizador com todos os conteúdos e revogações reais.
4. **Logótipos reais por canal**: o plugin fornece apenas imagem genérica local à secção do piloto, não o logótipo de cada canal; qualquer URL do fornecedor precisará de validação SSRF/segredos/SSO.
5. **Compatibilidade Android/iOS/Samsung/LG**: não há testes físicos de reprodução nesses quatro ambientes com origem autorizada do fornecedor.
6. **Ramo de P3**: `feature/p3-private-catalog-20261123` preserva commits próprios e divergentes; reconciliar/rever sem merge cego. O `main` agora inclui a versão de teste implantada e o hardening aprovado.
7. **Entitlement em produção**: monitor `MonitorEntitlements=false`; verificar renovações reais e propriedade da desativação antes de ativar.

**Princípio:** corrigir no código e na instalação já existentes, preservar provas, isolamento e rollback, e não confundir teste sintético, compilação, resposta HTTP ou relatório `completed` com funcionalidades IPTV realmente disponíveis aos utilizadores.
