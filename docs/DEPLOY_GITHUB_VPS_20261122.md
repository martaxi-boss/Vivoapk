# VivoAPK — implantação controlada GitHub → VPS (22/11/2026)

## Entrega confirmada

O proprietário pediu o sistema de controlo Mega OTT independente e instalado na VPS, não apenas uma pasta criada manualmente. A implantação foi executada com o instalador versionado [infra/deploy.py](../infra/deploy.py), a partir da revisão fixa do GitHub:

- Repositório: [martaxi-boss/Vivoapk](https://github.com/martaxi-boss/Vivoapk)
- Revisão efetivamente instalada: `540846e48ca3655acb3b0a8601dea700a082979d`.
- Testes de sintaxe e 7 verificações sintéticas do instalador: [CI #45082988561](https://github.com/martaxi-boss/Vivoapk/actions/runs/45082988561) — PASS.
- Auditoria inicial real: [Ferrari #45082585052](https://github.com/martaxi-boss/VPS/actions/runs/45082585052).
- Pré-instalação sem mutações, com código Git verificado por blob SHA: [Ferrari #45083123449](https://github.com/martaxi-boss/VPS/actions/runs/45083123449) — `preflight_pass`.
- Instalação real na VPS: [Ferrari #45083231609](https://github.com/martaxi-boss/VPS/actions/runs/45083231609) — `installed_private_backend`.
- Verificação posterior independente na VPS: [Ferrari #45083389323](https://github.com/martaxi-boss/VPS/actions/runs/45083389323) — serviço e base de dados confirmados.

Todos os relatórios Ferrari acima usam resposta ZIP e `report.json`. A verificação de qualidade automática marca `not_certified` quando não há critérios de aceitação adicionais: sucesso da execução **não implica** login ou reprodução Jellyfin completos.

## Estrutura real verificada

| Recurso | Resultado |
|---|---|
| Código | `/srv/projects/Vivoapk/source`, HEAD = revisão instalada |
| Dependências | `/srv/projects/Vivoapk/.venv` |
| Serviço isolado | `vivoapk-backend.service` ativo |
| API interna | `/srv/projects/Vivoapk/run/backend.sock` (Unix socket) |
| Resposta do endpoint | `/openapi.json`: HTTP 200, verificado |
| Base de dados | PostgreSQL `vivoapk`, role `vivoapk_svc` |
| Linhas importadas | 4 subscrições |
| Links específicos Samsung/LG | 4 presentes |
| Cofre | `/root/project-secrets/VIVOAPK/backend-runtime.env`, modo `0600` |
| Acesso técnico do Jellyfin | Utilizador `jellyfinpink` com grupo `www-data` comunica com o socket |
| Portas externas criadas | Nenhuma |

**Observação de permissões:** o socket aparece com modo `0666`, mas está dentro de um diretório `run/` criado com modo `2770` e grupo `www-data`; essa barreira de diretório impede acesso de outros grupos. A permissão do próprio socket deve ser reduzida para `0660` com alteração versionada e verificada antes da implantação do plugin Jellyfin.

## Sistemas protegidos

A verificação posterior confirmou ativos:

- `pink-iptv-backend.service` e `pink-vpn.service` — PINK antigo preservado para rollback;
- `jellyfin-pink.service` — Jellyfin oficial instalado e isolado;
- `nginx.service` e `postgresql.service`.

O Nginx continua a servir o Jellyfin no DuckDNS existente. A API VivoAPK **não foi ligada ao Nginx** nem exposta na Internet. O cofre central de produção não foi copiado para GitHub e não existem ficheiros de media armazenados na VPS por esta instalação.

## Próxima fase obrigatória — não concluída

O backend instalado **não altera automaticamente o ecrã de login do Jellyfin**. Para cumprir o resultado pedido pelo proprietário, ainda é necessário construir e certificar um plugin Jellyfin 12.4 que autentique o username/password Mega OTT através do socket, crie a identidade técnica invisível e disponibilize a playlist individual de canais/filmes/séries/EPG.

Não instalar um plugin Xtream genérico com credenciais partilhadas; é necessário testar isolamento entre subscrições e evitar a exposição de passwords em URLs de media.

**Só desinstalar o PINK** após login real a funcionar no Jellyfin, catálogo separado por conta, playback testado sem relay pela VPS (quando tecnicamente possível), backup recuperável e auditoria de dependências das unidades antigas e Certbot.
