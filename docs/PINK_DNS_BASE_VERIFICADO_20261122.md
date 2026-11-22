# DNS base real e estrutura PINK preservada — VivoAPK

Leitura real Ferrari em 22/11/2026: https://github.com/martaxi-boss/VPS/actions/runs/45079375660
Fonte privada: /root/project-secrets/VIVOAPK/subscriber-mappings.csv (copia da BD PINK, sem modificar os clientes).

## Padrão comprovado nos 4 registos

| Campo da API | Sufixo observado | Subdomínios distintos |
|---|---|---|
| dns_link (Android / Android TV) | zvpnm.com | 4, cada um com 1 prefixo |
| dns_link_for_samsung_lg (Samsung Tizen / LG webOS) | daizsmart.com | 4, cada um com 1 prefixo |

Todos os 8 URLs usam atualmente HTTP e não têm porta explícita.
Estes sufixos são um padrão real da amostra, NÃO um contrato garantido de base permanente do Mega OTT.

## Regra operativa — preservar sempre a origem inteira

Para cada subscrição o fornecedor atribui os dois URLs completos, incluindo esquema e subdomínio.
Não inventar o prefixo, não concatenar uma base com um subdomínio escolhido manualmente,
não alterar o DNS de um cliente, não fazer upgrade automático de HTTP para HTTPS e
não substituir a origem Samsung/LG pela origem geral quando faltar algum valor.
Actualizar apenas com os campos oficiais da API Mega OTT (GET /v1/subscriptions/{id}).

## Como estava implementado no PINK

- backend/app/clients/mega.py obtém metadados com token Bearer reservado ao servidor.
- backend/app/services/mappings.py guarda os dois DNS integralmente e permite actualização por ID.
- backend/app/services/session.py autentica username/password no DNS completo guardado
  e devolve xtream_base_url à app; não retransmite vídeo pela VPS.
- Para username já mapeado, o código original usa os valores locais; não refresca DNS
  em todos os logins. Uma eventual mudança de subdomínio exige importação/sincronização
  segura com a API e prova real, sem adivinhar novos prefixos.
- Android ProviderSession mantém o URL devolvido após validação; XtreamCatalogClient
  consulta canais, VOD, séries e EPG diretamente no fornecedor; XtreamPlaybackUrlFactory
  constrói ligações de reprodução sem passar o vídeo pela VPS.

## Cópia independente do cliente Android

Os 11 ficheiros Kotlin principais de sessão, catálogo, EPG e reprodução
estão preservados em reference/pink-android-core/ dentro do VivoAPK.
É uma cópia textual de REFERÊNCIA e não uma APK VivoAPK pronta a instalar.
Origem do código: martaxi-boss/pink-iptv commit 32f3638d3e02f01558ef86127d865956f5c52e64.
Alguns módulos Android estavam ausentes do checkout local da VPS; por isso
a fonte do GitHub na revisão exata foi copiada antes de descontinuar o repositório.

## Cofre e desinstalação

Os URLs completos, token do revendedor e 4 registos permanecem privados em
/root/project-secrets/VIVOAPK, não neste GitHub.
A desinstalação PINK continua bloqueada até o backend VivoAPK e os clientes
reproduzirem e autenticarem em testes reais e até ser confirmado o backup do cofre.
Os serviços atuais do PINK foram mantidos ativos nesta auditoria.
