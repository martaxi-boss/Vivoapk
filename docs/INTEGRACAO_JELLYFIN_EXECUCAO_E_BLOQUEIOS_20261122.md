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
- Plugin `Enabled=false` por defeito; **não instalado na produção Jellyfin**. A compilação dos assemblies Jellyfin 12.4 e os testes sintéticos passaram em [CI #45085448963](https://github.com/martaxi-boss/Vivoapk/actions/runs/45085448963).
- Ensaio adicional com instância Jellyfin 12.4 descartável via [workflow de smoke](../.github/workflows/vivoapk-jellyfin-real-smoke-ci.yml). Este teste distingue DLL compilada de login realmente confirmado no Jellyfin.

## Bloqueio de autenticação no fornecedor real

Auditorias Ferrari sem expor valores de chaves, URLs individuais ou usernames:

- [#45085572845](https://github.com/martaxi-boss/VPS/actions/runs/45085572845): pedido de metadados `GET /v1/subscriptions/{id}` com token copiado para VivoAPK devolveu 403 numa primeira consulta.
- [#45085706904](https://github.com/martaxi-boss/VPS/actions/runs/45085706904): token do VivoAPK devolveu 401 nos pedidos de listagem e consulta individual de subscrição; parâmetros seguros e resposta resumida.
- [#45085851833](https://github.com/martaxi-boss/VPS/actions/runs/45085851833): token antigo da instalação PINK e token do VivoAPK **diferem**. Na rota de perfil de teste, token do PINK devolveu 403 e o do VivoAPK 401.

**Interpretação:** o código de descoberta de novas subscrições está presente, mas **não há autorização real comprovada** para a conta/credencial de API atualmente configurada. Não atribuir o erro a um único motivo (permissão, token, proteção de acesso ou endpoint) sem diagnóstico oficial. Precisa de token API legítimo e ativo com permissões de consulta, guardado apenas no cofre da VPS, nunca no repositório público ou na conversa. Não contornar restrições.

Os quatro mapeamentos antigos continuam guardados e não foram alterados. Isso não prova login Xtream de uma conta ativa nem reprodução; exemplos expirados não são credenciais de teste válidas.

## Catálogo e streaming: limites não resolvidos

O Jellyfin usa uma indexação e cache próprias para `IChannel`; a existência de `IsEnabledFor(userId)`, `InternalChannelItemQuery.UserId` e `IHasCacheKey.GetCacheKey(userId)` é útil, mas **não certifica isolamento** dos itens indexados no servidor. Não instalar `Jellyfin.Xtream` global com password comum: catálogo de um cliente pode ser visto por outro e as URLs de media podem expor credenciais.

Falta implementar e testar um conector de catálogo por identidade Jellyfin, com namespaces de identificadores por subscrição, cache separada e teste de duas contas cruzadas. A cache/session das credenciais deverá ser curta e nunca incluir password na API/HTML da interface.

A reprodução do Jellyfin pode usar Direct Play, proxy, remux ou transcode. **Não garantir** que stream direto nunca passa pela VPS nem que os domínios/URLs são invisíveis à inspeção de rede antes de medir no Android, iOS e Smart TVs, especialmente com origens Mega HTTP e ligação Jellyfin HTTPS.

## Portas para produção

1. Smoke real Jellyfin descartável com conta fictícia e admin protegido — exigir `PASS`.
2. API Mega autenticada e pesquisa por username provada com credenciais autorizadas — exigir `PASS`.
3. Login Jellyfin real com subscrição autorizada, sem expor conta nem domínio — exigir `PASS`.
4. Catálogo Live/VOD/Séries/EPG dinâmico, isolado entre duas contas, com revogação — exigir `PASS`.
5. Reprodução e consumo de banda da VPS medidos nos clientes suportados — exigir `PASS`.
6. Backup/rollback do Jellyfin, PINK preservado; instalar/ativar apenas após gates de segurança.

Nenhuma etapa com mock pode justificar declarar o sistema completo. O backend VivoAPK já instalado permanece isolado, e o PINK não deve ser desinstalado.
