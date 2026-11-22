# VivoAPK — instalação reproduzível a partir do GitHub

Fonte de verdade única: [martaxi-boss/Vivoapk](https://github.com/martaxi-boss/Vivoapk).
A VPS não pode receber pastas avulsas, código sem revisão ou dependências
globais de outro projeto. Usar exclusivamente o instalador versionado
`infra/deploy.py`, a revisão Git completa verificada e as provas de CI.

## Inventário real antes de qualquer alteração

Ferrari [VPS #45082585052](https://github.com/martaxi-boss/VPS/actions/runs/45082585052)
em 22/11/2026, modo **read-only**, confirmou:

- `/srv/projects/Vivoapk` **ainda não existe** e
  `vivoapk-backend.service` não está instalado;
- `/srv/projects/pink-iptv`, backend PINK e VPN **ativos**;
- `/srv/projects/jellyfin-server`, Jellyfin 12.4 e Nginx **ativos**;
- Jellyfin tem `PrivateNetwork=yes` e `IPAddressDeny=any`; recebe
  HTTPS através de Nginx + `/srv/projects/jellyfin-server/run/jellyfin.sock`;
- PostgreSQL ativo, Python/pip/git e .NET disponíveis;
- cofre `/root/project-secrets/VIVOAPK` com modo `0700` e
  `backend-auth.env`, `subscriber-mappings.csv` e
  `dns-bases-observed.json` existentes;
- ~32.5 GiB de espaço livre (fotografia do instante).

Isto é uma conferência de existência, não um teste de autenticação,
integração Jellyfin ou de reprodução.

## Estrutura de destino gerida

```text
GitHub: martaxi-boss/Vivoapk
    ├── backend/       FastAPI Mega OTT / Xtream autenticado
    ├── infra/         instalador, testes e procedimentos de release
    ├── docs/          contrato Jellyfin / PINK / DNS
    └── .github/       CI

VPS:
    /srv/projects/Vivoapk/
        source/        checkout Git SHA exato deste repositório
        .venv/         Python e dependências exclusivas
        run/           backend.sock (Unix domain socket privado)

    /root/project-secrets/VIVOAPK/
        backend-auth.env          token e chave de sessão já guardados
        subscriber-mappings.csv   quatro mapeamentos copiados do PINK
        dns-bases-observed.json   apenas referência dos sufixos
        backend-runtime.env       gerado no primeiro deploy, root-only

    PostgreSQL local:
        vivoapk (base de dados nova) / vivoapk_svc (role novo)

    systemd:
        vivoapk-backend.service  (serviço da aplicação, sem media)
```

O instalador **recusa** pasta, ficheiro de ambiente, role PostgreSQL
ou unidade systemd já existente, em vez de os sobrescrever. A primeira
falha pode deixar um estado parcial: não remover ou reinstalar às cegas;
auditar o estado e recuperar sem afetar cofre ou serviços.

## Execução e revisão

Primeiro testar com dados sintéticos no GitHub e correr a pré-verificação
de leitura na VPS (com direitos root, mas sem operações mutáveis):

```bash
python3 infra/deploy.py preflight
```

O primeiro deployment requer um commit completo, aceite em CI,
e utilização explícita desse SHA:

```bash
python3 infra/deploy.py install --sha <sha-completo-aprovado>
```

O instalador é uma rotina controlada, **não deve ser executado ao
importar o módulo**, e só pode operar nos paths acima. Se não houver
autorização de execução autenticada, deixar pendente; não inventar PASS.

O backend responde apenas no socket
`/srv/projects/Vivoapk/run/backend.sock` (permissões de grupo
`www-data`), sem criar vhost, porta pública, firewall ou certificados.
O socket permite que uma **futura integração Jellyfin** aceda à API
sem enfraquecer o isolamento de rede atual da aplicação.

## Etapas distintas, sem confundir progresso

1. **Backend VivoAPK**: instalar, provar socket/health, PostgreSQL isolado,
   quatro mapeamentos e rollback. Requisitos de CI e credenciais privadas.
2. **Plugin Jellyfin**: construir para Jellyfin 12.4/.NET, validar login
   com username/password Mega, provisionar identidade Jellyfin sem ações
   manuais e impedir exposição de clientes e credenciais. **Não instalado.**
3. **Catálogo**: usar a identidade de sessão para disponibilizar
   Live/VOD/Series/EPG individualmente, sem biblioteca global comum.
   **Não implementado.**
4. **Reprodução**: testes físicos e de rede. O Jellyfin pode fazer
   remux, proxy ou transcode; não anunciar bypass de media na VPS
   sem prova. URLs diretas podem revelar o hostname ao dispositivo.
5. **Retirar PINK**: só após provas reais, backup/restauro do cofre
   e auditoria de serviços, especialmente do Certbot histórico PINK.

A alteração do domínio DuckDNS para domínio próprio é uma mudança da
origem pública Jellyfin, não das origens Mega OTT dos clientes.

## Resultado do primeiro deployment na VPS (22/11/2026)

O primeiro deployment da revisão `540846e48ca3655acb3b0a8601dea700a082979d` terminou com sucesso via Ferrari: [instalação #45083231609](https://github.com/martaxi-boss/VPS/actions/runs/45083231609). A verificação independente [#45083389323](https://github.com/martaxi-boss/VPS/actions/runs/45083389323) confirmou código instalado na revisão esperada, serviço e socket ativos (HTTP 200), 4 subscrições, 4 DNS Samsung/LG, acesso pelo utilizador Jellyfin e os serviços antigos ativos. O socket é alcançável apenas pelo percurso de diretórios restrito ao grupo `www-data`, mas o próprio inode de socket apareceu com modo `0666`; há margem de endurecimento para `0660` antes de expor uma integração de autenticação Jellyfin. Não confundir esta etapa com um plugin Jellyfin operacional. [Relatório operacional](../docs/DEPLOY_GITHUB_VPS_20261122.md).
