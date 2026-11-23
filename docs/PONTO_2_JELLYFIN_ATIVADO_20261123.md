# Ponto 2 — plugin VivoAPK de autenticação no Jellyfin (23/11/2026)

**Resultado confirmado: INSTALADO E ATIVADO TECNICAMENTE; SERVIÇO IPTV AINDA INCOMPLETO.**

Repositório fonte: `martaxi-boss/Vivoapk`. Revisão do código C# instalada: `f1bb21928dd499b53a40fbfb105ce6018af9fb07`. Pastas segregadas dentro de `/srv/projects/Vivoapk/`; instalação do plugin na pasta padrão `/srv/projects/jellyfin-server/data/plugins/VivoAPK Mega OTT Auth_0.1.0.0/`.

| Verificação | Prova | Estado |
|---|---|---|
| Compilação .NET 10/Jellyfin 12.4 e testes simulados | [CI #45086818441](https://github.com/martaxi-boss/Vivoapk/actions/runs/45086818441) | PASS |
| Teste com servidor Jellyfin 12.4 descartável e Mega fictícia | [CI #45086818422](https://github.com/martaxi-boss/Vivoapk/actions/runs/45086818422) | PASS |
| Auditoria da release binária na VPS | [Ferrari #45089887346](https://github.com/martaxi-boss/VPS/actions/runs/45089887346) | PASS |
| Instalação desativada, backup Jellyfin e proteção do admin | [Ferrari #45090003933](https://github.com/martaxi-boss/VPS/actions/runs/45090003933) | PASS |
| Ativação e reinício do Jellyfin | [Ferrari #45090113991](https://github.com/martaxi-boss/VPS/actions/runs/45090113991) | PASS |
| Verificação independente de serviço, socket e configuração | [Ferrari #45090435057](https://github.com/martaxi-boss/VPS/actions/runs/45090435057) | PASS |
| Login com **subscrição Mega OTT real e ativa** | Ainda sem credencial de teste autorizada verificada | **Não demonstrado** |
| Playlist individual privada no Jellyfin | Conector de catálogo ainda por implementar | **Não implementado** |
| Reprodução e consumo de tráfego VPS | A medir com dispositivos reais após catálogo | **Não testado** |

## O que o plugin faz

O utilizador introduz o servidor Jellyfin `https://serveriptv.duckdns.org`, username e password Mega OTT no login normal do Jellyfin. O servidor usa o plugin `IAuthenticationProvider` para delegar a validação ao endpoint interno VivoAPK `POST /v1/session/resolve` via socket Unix privado. Este mecanismo já passou num Jellyfin real **com backend fictício**; a autorização à API Mega OTT para listagem de subscrições foi corrigida separadamente a partir da chave existente do PINK ([Ferrari #45087866003](https://github.com/martaxi-boss/VPS/actions/runs/45087866003)).

O código protege identidades Jellyfin locais existentes (incluindo `pink-admin`) e só cria identidade restrita quando o fornecedor aceita a autenticação. O instalador fez snapshot privado dos diretórios `data/` e `config/`, tem função `rollback` e preservou PINK, VPN, Nginx, certificados e backend VivoAPK.

## Limites que não se podem confundir

- `plugin_enabled=true`, `admin_identity_present=true` e os dois sockets HTTP 200 estão **verificados na VPS**.
- Isto **não prova** que uma subscrição Mega OTT real já consiga fazer login; esse ensaio ainda não foi feito.
- A preservação do registo de `pink-admin` não substitui um teste positivo da sua password após instalação. Esse teste não foi executado nesta auditoria.
- A plataforma **não** tem bibliotecas Live/VOD/Séries/EPG por cliente. Não ligar um tuner ou biblioteca Xtream global, porque poderia misturar os conteúdos das subscrições.
- Reprodução direta para o dispositivo e eventual ocultação de URLs de fornecedor ainda não foram validadas.
- Não apagar PINK nem declarar a migração concluída.

**Passo seguinte:** estudar/implementar o catálogo privado por utilizador (ponto 3) e, antes de uso com clientes reais, provar login com uma subscrição Mega OTT ativa e autorização correta. O ponto 2, enquanto instalação e ativação técnica, está concluído.

Relatório detalhado e histórico: [`docs/INTEGRACAO_JELLYFIN_EXECUCAO_E_BLOQUEIOS_20261122.md`](INTEGRACAO_JELLYFIN_EXECUCAO_E_BLOQUEIOS_20261122.md).
