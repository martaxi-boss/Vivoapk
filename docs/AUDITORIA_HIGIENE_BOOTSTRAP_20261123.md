# Auditoria e higienização VivoAPK — 23/11/2026

## Contrato auditado

[BOOTSTRAP.md](../BOOTSTRAP.md): Jellyfin oficial, URL único, credenciais Mega OTT, gestão exclusiva no painel Mega e catálogo individual com media direto do fornecedor, sem retransmissão na VPS.

## Auditoria física read-only pelo Ferrari e REA

- Ferrari/Ruflo início: [#45231538174](https://github.com/martaxi-boss/VPS/actions/runs/45231538174); execução REA `capture_process_scenario`: [#45231870136](https://github.com/martaxi-boss/VPS/actions/runs/45231870136).
- Saída observada: `pink-iptv-backend.service` e `pink-vpn.service` **not-found/inactive**; `/srv/projects/pink-iptv` **absent**.
- `jellyfin-pink.service`, `vivoapk-backend.service`, `nginx.service` e `postgresql.service` ativos; `jellyfin.service` genérico inexistente.
- Espaço livre arredondado 32,5 GiB; `/srv/projects/Vivoapk` cerca de 268 MiB e `/srv/projects/jellyfin-server` cerca de 295 MiB. Cofre root inacessível ao processo não privilegiado e intocado; Git HEAD local não pôde ser lido por este processo.
- Nenhum ficheiro de segredo foi aberto, nenhuma unidade modificada, nenhum serviço reiniciado; zero ações destrutivas.

## Falhas de arquitetura e código demonstradas

1. A antiga UI Pink (Android em `reference/pink-android-core`) não é usada pelo produto Jellyfin, nem compilada por qualquer pipeline ativo. Retirada da árvore HEAD; Git mantém histórico.
2. O script `infra/sync_pink_api_token.py` era exclusivamente para migrar uma vez o token a partir de `/srv/projects/pink-iptv`, que agora está ausente. Retirado com o teste específico, mantendo provas históricas em `infra/PINK_API_KEY_ALIGNMENT.md`.
3. Os scripts `infra/deploy.py` e `infra/deploy_jellyfin_auth.py` exigiam, indevidamente, serviços PINK antigos como dependências de saúde. Atualizados para verificar apenas os serviços atuais e rejeitar o inesperado regresso de unidades PINK.
4. O plugin `MegaResolverClient` fixava a plataforma `android` para todos os logins e consultas do catálogo piloto. Passa a ler hints dos clientes Jellyfin, com fallback conservador e teste de cliente Tizen em servidor descartável.
5. README e descrição histórica da arquitetura estavam desalinhados com o Bootstrap. Reescritos para documentar um único login e a diferença entre catálogo e reprodução efetiva.
6. **Por resolver:** plugin nativo com catálogo completo por subscrição, isolamento multicliente de IDs/indexação, filmes/séries/EPG visíveis e Direct Play efetivo de todas as plataformas. O contrato não é concluído pela higiene.

## Regras de eliminação

Não apagar `jellyfin-pink.service`, dados Jellyfin, `/root/project-secrets/VIVOAPK`, `/srv/projects/Vivoapk`, backups de rollback ou fontes de credenciais atuais. Não `git clean`, não `apt purge`, não desativar serviço ativo. O PINK antigo já foi removido; não inventar ganhos de espaço com nova desinstalação.

**Qualidade:** execução REA confirma apenas os pontos observados, com `quality_gate=not_certified`. Os testes de código e CI valem apenas para o código no respetivo commit. Sem deploy de alteração experimental ou alegação de reprodução real na app.

## Validação de código após correções — 23/11/2026

- [Infraestrutura #45233088921](https://github.com/martaxi-boss/Vivoapk/actions/runs/45233088921): **PASS**, incluindo remoção dos gates de serviços Pink antigos.
- [Plugin nativo .NET 10 #45233170001](https://github.com/martaxi-boss/Vivoapk/actions/runs/45233170001): **PASS** de compilação e testes sintéticos das plataformas / resolver.
- [Servidor Jellyfin 12.4 descartável #45233170039](https://github.com/martaxi-boss/Vivoapk/actions/runs/45233170039): **PASS** de login com cliente Tizen fictício, catálogo piloto, isolamento B no modo de piloto, HLS direto sintético (vídeo+áudio) e suspensão/renovação da mesma identidade.
- A ronda inicial [#45232819174](https://github.com/martaxi-boss/Vivoapk/actions/runs/45232819174) falhou porque o teste de renovação voltou a identificar a sessão fictícia Tizen como Android; corrigida a chamada no próprio fixture, não alterada a política de produção para esconder o erro. A ronda final acima concluiu PASS.
- Alterações submetidas no ramo [audit/bootstrap-hygiene-20261123](https://github.com/martaxi-boss/Vivoapk/tree/audit/bootstrap-hygiene-20261123) e em [PR #3 (draft)](https://github.com/martaxi-boss/Vivoapk/pull/3). **Não instaladas em produção.** A experiência anterior de múltiplos utilizadores não é integrada porque ainda não prova isolamento de todos os IDs de media.
