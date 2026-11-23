# Jellyfin 12.4 + VivoAPK — execução verificável e bloqueios

Estado em 22–23 de novembro de 2026. Fonte de verdade: `martaxi-boss/Vivoapk` (GitHub) e Ferrari no Ubuntu/VPS.

## Objetivo aprovado

Jellyfin oficial → `https://serveriptv.duckdns.org` → username/password de uma subscrição Mega OTT M3U → identificação automática pelo backend VivoAPK → catálogo autorizado daquela subscrição dentro do Jellyfin, sem o cliente preencher M3U, DNS de fornecedores ou portas alternativas.

O DNS público DuckDNS pertence ao servidor Jellyfin e não se confunde com os URLs de streaming `dns_link` e `dns_link_for_samsung_lg`. Não há whitelist fixa de domínios; os endereços completos provêm da API oficial Mega OTT. O subdomínio nunca é adivinhado.

## Implementado no backend da VPS

- `/srv/projects/Vivoapk/source`, backend privado `vivoapk-backend.service`, socket `/srv/projects/Vivoapk/run/backend.sock`.
- PostgreSQL novo `vivoapk`, 4 mapeamentos importados sem passwords de clientes, 4 pares de origens gerais/Samsung-LG.
- O backend responde no socket; o utilizador técnico do Jellyfin consegue chegar ao socket. Instalação provada [Ferrari #45083231609](https://github.com/martaxi-boss/VPS/actions/runs/45083231609); pós-teste [#45083389323](https://github.com/martaxi-boss/VPS/actions/runs/45083389323).
- A instalação não abriu porta pública nem alterou Jellyfin, VPN, PINK, certificados ou Nginx.

## Novo conector de autenticação Jellyfin

- Fonte original C# e .NET 10 em [`jellyfin-auth/`](../jellyfin-auth/).
- Plugin Jellyfin 12.4 com `IAuthenticationProvider`, chamada `POST /v1/session/resolve` por socket Unix privado, autenticação delegada ao MegaOTT/Xtream.
- O cliente só vê o login normal do Jellyfin. Quando autorizado, está preparada a criação de uma identidade Jellyfin não administrativa com password local aleatória, sem guardar password Mega como password Jellyfin.
- Recusa substituir qualquer conta já existente gerida por outro autenticador, protegendo `pink-admin` e contas locais.
- Plugin `Enabled=false` por defeito na fonte, mas **instalado e ativado na produção Jellyfin em 23/11/2026** com backup privado. [Instalação #45090003933](https://github.com/martaxi-boss/VPS/actions/runs/45090003933), [ativação #45090113991](https://github.com/martaxi-boss/VPS/actions/runs/45090113991), [verificação independente #45090435057](https://github.com/martaxi-boss/VPS/actions/runs/45090435057). A compilação dos assemblies Jellyfin 12.4 e os testes sintéticos passaram em [CI #45085448963](https://github.com/martaxi-boss/Vivoapk/actions/runs/45085448963).
- **Teste real em instância Jellyfin 12.4 descartável PASS**: [CI #45086626328](https://github.com/martaxi-boss/Vivoapk/actions/runs/45086626328). Confirmou: plugin listado e ativo no Jellyfin de teste, login do administrador, autenticação do cliente fictício através de Mega fictícia (Unix socket), criação automática da identidade, recusa de password errada e preservação do administrador após a autenticação. Um aviso `GUID_MATCH=FAIL` no relatório foi apenas diferença de representação do GUID com/sem hífen, verificada pelo identificador observado, normalização corrigida na revisão seguinte. Não envolveu contas verdadeiras nem instalações na VPS.

## Credencial API do PINK recuperada para o VivoAPK (23/11/2026)

**Estado atualizado: problema de chave incorreta no VivoAPK RESOLVIDO.**
Não pedir nova chave ao revendedor.

A auditoria real [Ferrari #45087046545](https://github.com/martaxi-boss/VPS/actions/runs/45087046545) confirmou que a chave de
`/srv/projects/pink-iptv/private/systemd-env/secrets.env` corresponde
exatamente à variável Mega OTT já carregada em memória pelo serviço PINK,
mas era **diferente** da chave em
`/root/project-secrets/VIVOAPK/backend-auth.env` e
`backend-runtime.env`. As origens base API dos dois serviços coincidem.

A consulta oficial usando `httpx` e `trust_env=False`, tal como no
código PINK, provou [Ferrari #45087196977](https://github.com/martaxi-boss/VPS/actions/runs/45087196977):
- `GET /v1/subscriptions?page=1&per_page=1` com a chave original: **HTTP 200**, resposta JSON, acesso de listagem autorizado.
- `GET /v1/subscriptions/{id}` usando **um ID antigo** da base migrada: HTTP 404. O registo escolhido não foi encontrado pela API; isto não invalida a chave nem prova que uma subscrição nova falhe.

Uma inspeção read-only [#45087559930](https://github.com/martaxi-boss/VPS/actions/runs/45087559930) confirmou modo
`0640` nas configurações PINK sob diretório `0700`, e `0600` nas configurações VivoAPK. A versão do
instalador foi ajustada para respeitar ambos, preservando o controlo
de root e sem publicar valores. [CI do instalador #45087701904](https://github.com/martaxi-boss/Vivoapk/actions/runs/45087701904) PASS.
[Pré-verificação #45087750560](https://github.com/martaxi-boss/VPS/actions/runs/45087750560) devolveu `ready`, sem mudanças.

**Aplicação concluída e verificada no Ferrari
[#45087866003](https://github.com/martaxi-boss/VPS/actions/runs/45087866003):**
`status=applied_verified`; a chave original foi **copiada separadamente**
para os dois ficheiros privados VivoAPK (auth e runtime), com backups
privados, sem alterar qualquer ficheiro ou serviço PINK. Foi reiniciado
**só o serviço VivoAPK**. A chave efetiva do processo VivoAPK coincide
agora com a do PINK, e o GET de listagem Mega OTT devolve 200,
com o socket VivoAPK saudável e os serviços protegidos ativos.

A chave nunca foi enviada para o GitHub, para artefactos públicos ou
para esta documentação. A alteração de configuração permanece no cofre
da VPS; o procedimento está versionado em
[`infra/sync_pink_api_token.py`](../infra/sync_pink_api_token.py).

**Pendências distintas:** demonstrar descoberta e login de uma
subscrição nova realmente ativa, validar o conteúdo do catálogo,
validar login de subscrição real na integração Jellyfin já instalada,
validar separação entre clientes e medir banda de reprodução.
O sucesso da listagem por API não implica playlist automática no Jellyfin.

### Histórico dos bloqueios anteriores, já corrigidos para a listagem

- [#45085572845](https://github.com/martaxi-boss/VPS/actions/runs/45085572845): consulta de ID antigo retornou 403 com uma implementação de transporte distinta.
- [#45085706904](https://github.com/martaxi-boss/VPS/actions/runs/45085706904): chave incorreta antiga do VivoAPK retornou 401.
- [#45085851833](https://github.com/martaxi-boss/VPS/actions/runs/45085851833): as chaves eram diferentes; a rota adicional de perfil retornou 403 ao token PINK, o que não substitui o teste correto do endpoint de listagem.

## Catálogo e streaming: limites não resolvidos

O Jellyfin usa uma indexação e cache próprias para `IChannel`; a existência de `IsEnabledFor(userId)`, `InternalChannelItemQuery.UserId` e `IHasCacheKey.GetCacheKey(userId)` é útil, mas **não certifica isolamento** dos itens indexados no servidor. Não instalar `Jellyfin.Xtream` global com password comum: catálogo de um cliente pode ser visto por outro e as URLs de media podem expor credenciais.

Falta implementar e testar um conector de catálogo por identidade Jellyfin, com namespaces de identificadores por subscrição, cache separada e teste de duas contas cruzadas. A cache/session das credenciais deverá ser curta e nunca incluir password na API/HTML da interface.

A reprodução do Jellyfin pode usar Direct Play, proxy, remux ou transcode. **Não garantir** que stream direto nunca passa pela VPS nem que os domínios/URLs são invisíveis à inspeção de rede antes de medir no Android, iOS e Smart TVs, especialmente com origens Mega HTTP e ligação Jellyfin HTTPS.

## Portas para produção

1. **Cumprido em ambiente descartável:** smoke real Jellyfin 12.4 com conta fictícia e administrador preservado ([#45086626328](https://github.com/martaxi-boss/Vivoapk/actions/runs/45086626328)); revisão do formato do GUID em CI pendente.
2. API Mega autenticada e pesquisa por username provada com credenciais autorizadas — exigir `PASS`.
3. Login Jellyfin real com subscrição autorizada, sem expor conta nem domínio — exigir `PASS`.
4. Catálogo Live/VOD/Séries/EPG dinâmico, isolado entre duas contas, com revogação — exigir `PASS`.
5. Reprodução e consumo de banda da VPS medidos nos clientes suportados — exigir `PASS`.
6. **Instalação/ativação técnica concluída:** backup e rollback preparados; plugin ativo, PINK preservado ([#45090003933](https://github.com/martaxi-boss/VPS/actions/runs/45090003933), [#45090113991](https://github.com/martaxi-boss/VPS/actions/runs/45090113991), [#45090435057](https://github.com/martaxi-boss/VPS/actions/runs/45090435057)). A conta administrativa está presente, mas o login positivo com a sua password original não foi testado.

Nenhuma etapa com mock pode justificar declarar o sistema completo. O backend VivoAPK já instalado permanece isolado, e o PINK não deve ser desinstalado.
