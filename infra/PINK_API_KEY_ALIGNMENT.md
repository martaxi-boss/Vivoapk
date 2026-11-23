## Corrigir chave Mega OTT a partir da fonte PINK sem alterar o PINK

[\`infra/sync_pink_api_token.py\`](sync_pink_api_token.py) faz a recuperação
de **uma credencial já existente e autorizada** no cofre privado do PINK,
sem revelar valores ao GitHub ou ao relatório.

É obrigatório executar primeiro \`check\`, que confirma a chave efetiva
do processo PINK e faz GET limitado a uma página de subscrições, com
\`httpx\`, \`trust_env=False\`, HTTP 200 e HTTPS.

\`apply\` atualiza apenas
\`/root/project-secrets/VIVOAPK/backend-auth.env\` e
\`/root/project-secrets/VIVOAPK/backend-runtime.env\`, com duas cópias
privadas no subdiretório \ \`credential-backups\`, e reinicia
**apenas** \`vivoapk-backend.service\`.

Revalida socket HTTP 200, chave realmente carregada, acesso Mega OTT e
serviços protegidos. Se falhar reverte os dois ficheiros e reinicia
novamente apenas VivoAPK. Nunca altera token do PINK nem o seu serviço,
nem expõe uma porta pública, nem instala plugin Jellyfin. Executar
somente com o código GitHub pinado, via Ferrari já existente.
