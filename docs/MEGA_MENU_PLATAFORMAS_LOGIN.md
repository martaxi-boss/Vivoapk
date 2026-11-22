# Mega OTT: comparação do menu de DNS com o PINK IPTV e implementação VivoAPK

Data: 2026-11-22. Base: captura do painel de criação de subscrição enviada pelo proprietário, seis exemplos de URLs de linhas anteriores, o código original PINK IPTV e a documentação pública da API Mega OTT em https://megaott.net/docs/subscriptions.

## 1. O que a captura REAL mostra

A imagem é o formulário **Create Subscription** do painel Mega OTT; apresenta o campo Username e uma lista de domínios disponíveis com capacidade momentânea. A captura revela:

| Domínio visível no menu | Capacidade mostrada na captura | Nota |
|---|---:|---|
| `zvpnm.com` | 94% | selecionado, com estrela |
| `fqvpnw.com` | 94% | disponível no menu |
| `fhvpnw.com` | 93% | disponível no menu |
| `beuaya.com` | 89% | disponível no menu |

Existe pelo menos uma opção tapada na captura; NÃO se conhece a lista completa. As percentagens são uma fotografia do painel, NÃO estados verificados em tempo real e nunca devem ser configuradas como capacidades fixas.

A captura, por si só, **não contém rótulos que provem** que cada uma destas opções é exclusiva de smartphones, nem mostra a lista específica de Samsung/LG. Portanto, não inventar categorias de domínio que a imagem não mostra.

## 2. O que demonstram os seis URLs fornecidos

- `beuaya.com` e `zvpnm.com` surgem como bases de `dns_link` genérico em diferentes subscrições.
- `daizsmart.com` surge como base do segundo URL, correspondente a Samsung/LG, nos pares apresentados.
- Os pares recebidos usam frequentemente o mesmo prefixo entre ambos os URLs. **Não pressupor que isso é obrigatório**: cada origem completa é fornecida pela API e deve ser guardada individualmente.
- Os URLs da amostra usam `http://`. Não reescrever para `https://` sem confirmar suporte real do fornecedor.
- Não publicar os subdomínios específicos de clientes em repositório público; os originais completos já estão no cofre privado.

## 3. Comparação técnica com o PINK

| Tema | PINK IPTV (fonte original) | VivoAPK (arquitetura alvo) |
|---|---|---|
| Criar a subscrição | No painel Mega OTT, com menus de pacote, tipo e domínio, fora da app cliente | Continuar no painel do revendedor; guardar a resposta oficial por ID |
| Conta por username/password | Somente subscrições M3U/Xtream | Preservar M3U; MAG/Enigma utilizam MAC e não encaixam no login de dois campos |
| Identificar conta | Mapeamento local ou pesquisa paginada por username na API Mega | Reutilizar procura e verificação do ID |
| DNS genérico | Guarda `dns_link` completo da API | Aceitar diferentes domínios base, sem lista fixa nem prefixos inventados |
| DNS TV nativa | Guarda `dns_link_for_samsung_lg`, mas cliente original Android não o utilizava | Tizen/webOS selecionam o campo Samsung/LG |
| Login | Android enviava username/password para `/v1/session/resolve` | Mesmo ecrã de dois campos; plataforma vem pré-definida pela aplicação |
| Reprodução | Aplicação Android consulta Xtream e reproduz diretamente no fornecedor | Igual por cliente compatível; VPS não retransmite media |
| Alteração de subdomínio | Importação explícita da API por ID atualizava os campos | Não reconstruir: sincronizar exclusivamente a resposta da API, preservando contas existentes |

## 4. Funcionamento destinado ao cliente (dois campos visíveis)

1. O revendedor cria/renova a subscrição M3U no Mega OTT e escolhe um dos domínios que o painel oferece, de acordo com a disponibilidade.
2. O Mega OTT atribui `id`, `username`, `password`, `dns_link` e `dns_link_for_samsung_lg`.
3. O backend VivoAPK importa os dados por ID oficial; não armazena a password do cliente em base de dados nem expõe o token do revendedor.
4. A aplicação apresenta só **Utilizador** e **Palavra-passe**. A plataforma é constante interna: `android`, `android_tv`, `ios`, `windows`, `tizen`, `webos`.
5. Via HTTPS, a app envia à API VivoAPK `username`, `password` e a plataforma interna. Uma app Android antiga sem campo de plataforma usa o valor predefinido `android`.
6. O backend encontra a subscrição, verifica validade e credenciais Xtream, escolhe **o URL completo** correspondente à plataforma e devolve apenas a origem autorizada e a sessão temporária.
7. A aplicação obtém Live/VOD/Series/EPG e reproduz diretamente na origem do Mega OTT, com formato e qualidade permitidos pelo dispositivo/player.

**Os DNS não são campos do login do cliente.** A origem do backend (`DuckDNS`, quando publicada com certificado) é configuração da aplicação, não uma URL para o utilizador escrever.

## 5. Distinção plataforma vs domínio do menu

- Android, Android TV, TV Box e Windows: usar `dns_link` tal como veio do fornecedor, seja ele `zvpnm.com`, `fqvpnw.com`, `fhvpnw.com`, `beuaya.com` ou outro oficialmente atribuído.
- iOS: hipótese de usar `dns_link`; compatibilidade e player concreto carecem de teste real.
- Samsung Tizen e LG webOS com app nativa: usar `dns_link_for_samsung_lg`; `daizsmart.com` é o exemplo observado, não uma garantia de ser o único domínio do fornecedor.
- A categoria da plataforma **não depende de uma whitelist de domínios**; decorre da aplicação instalada e do campo oficial do Mega OTT.
- Para Smart TV sem campo `dns_link_for_samsung_lg`, recusar esse destino: não substituir silenciosamente pelo DNS genérico.

## 6. O que não está confirmado e não devemos fingir

- A documentação pública de `POST /v1/subscriptions` lista os campos para criar contas, mas não documenta uma API para listar os domínios do menu e respetiva capacidade, nem um parâmetro específico para escolher a base nessa chamada. A escolha exata no UI não pode ser replicada automaticamente na nossa administração sem verificar outra interface oficial.
- A conta Vivo Player comercial e as apps Jellyfin têm autenticação/licenças próprias. Este backend não lhes muda o ecrã de login nem ativa software comercial.
- Os domínios reais observados usam HTTP, incluindo acesso Xtream com credenciais. Isso exige testes de segurança, TLS, restrições de mixed content (webOS/Tizen) e compatibilidade antes de disponibilizar a clientes.
- Android PINK era a implementação real de referência. iOS, LG e Samsung exigem cliente compatível concreto e testes físicos de catálogo e reprodução; os testes simulados não certificam essas plataformas.

## 7. Regra de atualização e preservação

Não editar contas ou DNS existentes só porque o painel mostra uma base mais livre. Guardar os URLs de cada subscrição conforme a API. Caso um subdomínio mude no fornecedor, atualizar o mapeamento local apenas depois de obter `GET /v1/subscriptions/{id}`, confirmar a identidade e usar os valores completos devolvidos. Um login de conta já conhecida não faz automaticamente essa sincronização no código presente; existe CLI de importação por ID para refresh explícito. Automatização futura só após testes contra a API real.

## 8. Código e testes VivoAPK

- `backend/app/services/provider_dns.py` centraliza seleção por plataforma sem lista fixa de bases ou alterações aos URLs.
- `backend/app/services/session.py` usa essa seleção depois de verificar a subscrição.
- `backend/tests/test_mega_dns_menu.py` testa os quatro domínios visíveis, as seis plataformas, origem Samsung/LG independente, dois campos de login e proteção contra destinos não permitidos.
- `reference/pink-android-core` preserva os 11 ficheiros de sessão, catálogo e player do Android original.
- O código não cria subscrições reais, não mexe nas quatro linhas já preservadas no cofre, não instala apps e não reinicia serviços antigos.
