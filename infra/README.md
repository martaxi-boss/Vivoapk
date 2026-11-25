# VivoAPK — operação e versões na VPS

Contrato atual: [BOOTSTRAP](../BOOTSTRAP.md). A instalação já existe; não repetir o primeiro deploy de 22/11 nem substituir serviços por cópias improvisadas.

- `/srv/projects/Vivoapk/source`: checkout do backend ativo fixado a SHA verificado, não à main móvel.
- `/srv/projects/Vivoapk/.venv` e `run/backend.sock`: dependências e API privada do backend.
- `/srv/projects/Vivoapk/jellyfin-auth/`: estágios e worktrees de compilação do plugin; `backups/`: rollback protegido.
- `/srv/projects/jellyfin-server`: servidor, configuração, dados, cache, logs e plugin Jellyfin; preservar dados, segredos e permissões.
- Serviços ativos: `vivoapk-backend.service`, `jellyfin-pink.service`, Nginx e PostgreSQL. Pink backend/VPN antigos já ausentes segundo [r66](https://github.com/martaxi-boss/VPS/actions/runs/45629560192).

## Alterações controladas

Ferrari/REA pela ponte GitHub Actions → SSH existente. Antes de qualquer mudança: conferir SHA do componente, saúde dos serviços, versão e permissões, backup/rollback e testes. Para atualização do plugin utilizar o procedimento versionado de estágio, ativação protegida, verificação e rollback em `infra/upgrade_jellyfin_multimedia.py` quando a revisão estiver aprovada. Não executar o instalador inicial `infra/deploy.py install` por cima da instalação existente. A main e o backend em execução não têm de ter o mesmo SHA.

A nova reprodução Jellyfin + FFmpeg está autorizada mas ainda não está pronta para deploy. Não enfraquecer isolamento de rede ou autorização para a fazer funcionar. Não alterar backend, catálogo, DNS ou login para resolver o leitor. Não instalar programas globais ou nova ponte. Manter segredos no cofre privado existente.

Os procedimentos do primeiro deploy e as referências anteriores estão no [histórico Git](https://github.com/martaxi-boss/Vivoapk/blob/c66889abdb25d2d81baadcb62b44b34d696aed03/infra/README.md) e no [relatório de implantação](../docs/DEPLOY_GITHUB_VPS_20261122.md); são evidência, não uma ordem de reinstalação.
