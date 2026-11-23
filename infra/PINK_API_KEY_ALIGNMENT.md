# Chave API Mega OTT — migração histórica concluída (arquivo)

**AVISO 23/11/2026:** o backend e a VPN antigos Pink já não estão instalados na VPS. O script de migração de token dependia da pasta antiga e foi retirado da árvore ativa. Foi preservado no [histórico Git anterior à remoção](https://github.com/martaxi-boss/Vivoapk/blob/578f0454f8a4ef2e6dbeefb11eb0089afe5dd5d0/infra/sync_pink_api_token.py). Não tentar voltar a executá-lo, nem recuperar a instalação Pink para esta finalidade. Este documento serve apenas como prova histórica.

**Estado: aplicado com sucesso na VPS em 23/11/2026.**

O procedimento original `sync_pink_api_token.py` (retirado do código ativo)
aproveita a credencial original **já em uso** pelo serviço PINK IPTV,
sem pedir uma chave nova, sem imprimir valores nem alterar o PINK.

Origem privada, **apenas leitura**:
`/srv/projects/pink-iptv/private/systemd-env/secrets.env`.
O endereço base está no `backend.env` original. Os dois ficheiros são
root-owned, modo `0640`, dentro de diretório privado `0700`.

Destinos privados e independentes no VivoAPK, ambos modo `0600`:

- `/root/project-secrets/VIVOAPK/backend-auth.env`
- `/root/project-secrets/VIVOAPK/backend-runtime.env`

O modo `check` exige que o token do ficheiro PINK corresponda ao
**token realmente carregado no processo PINK**, que as origens API
coincidam e que `GET /v1/subscriptions` devolva HTTP 200.
Foi confirmado na [pré-verificação Ferrari #45087750560](https://github.com/martaxi-boss/VPS/actions/runs/45087750560).

O modo `apply` cria duas cópias privadas em
`/root/project-secrets/VIVOAPK/credential-backups/`,
substitui apenas os tokens dos ficheiros VivoAPK de forma atómica e
reinicia **só** `vivoapk-backend.service`.
Valida a chave em memória, a resposta HTTP 200 da API, o socket privado
e os serviços protegidos. Reverte os ficheiros VivoAPK em caso de falha;
não tenta desligar nem reiniciar PINK, Jellyfin, VPN ou Nginx.

**Execução certificada:** [Ferrari #45087866003](https://github.com/martaxi-boss/VPS/actions/runs/45087866003) —
`applied_verified`, chave efetiva coincidente, GET Mega OTT autorizado,
socket saudável, backups criados e PINK inalterado.

O script foi carregado no Ferrari a partir de um commit GitHub
pinado, verificado por hash de blob. [CI do instalador #45087701904](https://github.com/martaxi-boss/Vivoapk/actions/runs/45087701904)
testou a sintaxe e as salvaguardas. **Não** publicar os ficheiros
de ambiente, cópias ou chaves no GitHub.

A autorização da API **não prova** a entrada de um utilizador novo
nem o aparecimento automático de canais/filmes/séries no Jellyfin.
Essas validações permanecem separadas e são descritas em
[`docs/INTEGRACAO_JELLYFIN_EXECUCAO_E_BLOQUEIOS_20261122.md`](../docs/INTEGRACAO_JELLYFIN_EXECUCAO_E_BLOQUEIOS_20261122.md).
