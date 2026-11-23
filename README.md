# Vivoapk — sistema independente Mega OTT / Jellyfin

**Contrato de funcionamento dos clientes (fonte de verdade):** [BOOTSTRAP.md](BOOTSTRAP.md). Entrada apenas com `https://serveriptv.duckdns.org`, username/password Mega OTT; gestão exclusiva no painel Mega; integração Jellyfin sem painel de clientes, sem Pink IPTV e sem media na VPS. O estado implementado ainda pode não satisfazer esse contrato.

## Situação real e fonte de verdade — 23/11/2026

**Código efetivamente instalado na VPS:** `578f0454f8a4ef2e6dbeefb11eb0089afe5dd5d0`. O ramo principal do GitHub foi alinhado por fast-forward com essa revisão em 23/11/2026; as alterações documentais posteriores não modificam o binário já instalado. [Auditoria e evidências](docs/AUDITORIA_FERRARI_REA_VIVOAPK_20261123.md).

**Arquitetura:** o cliente utiliza as aplicações Jellyfin existentes, configura `https://serveriptv.duckdns.org` e introduz o username/password de uma subscrição Mega OTT autorizada. O servidor VivoAPK é um **plano de autenticação e catálogo**, não armazena nem retransmite filmes/canais. Os streams HLS do piloto são apresentados como fontes HTTP diretas; a reprodução em dispositivos físicos e a ausência de retransmissão no tráfego **ainda não estão demonstradas**.

| Área | Estado comprovado | Limites |
|---|---|---|
| Backend VivoAPK | Serviço ativo, PostgreSQL isolado e socket Unix HTTP 200 | Continua privado; sem porta pública de vídeo |
| Plugin nativo Jellyfin 12.4 | Compilado, instalado e ativo; binário coincide com a revisão implantada | Integração com contas Mega reais em dispositivos continua por validar |
| Piloto de TV portuguesa | Até cinco canais por vez, numa só conta ativa em memória; HLS remoto e capa PNG genérica | Não é catálogo multiutilizador; sem logos oficiais individuais |
| Segurança do socket | `0660` persistente após reinício, grupo `www-data`, acesso Jellyfin HTTP 200 | Proteção implementada no serviço existente, sem segundo daemon |
| Biblioteca por cliente | Testes de integração com duas contas fictícias e isolamento PASS | Bibliotecas privadas completas e catálogo Live/VOD/Séries/EPG **não estão em produção** |
| Comportamento do player | Testes sintéticos: `DirectPlay=true`, `DirectStream=false`, `Transcoding=false` | Android/iOS/Samsung/LG e medição real de tráfego pendentes |
| Pinky IPTV antigo | Serviços backend/VPN e pasta antiga ausentes; Nginx antigo desativado com backup | O serviço `jellyfin-pink.service` continua a ser o **Jellyfin ativo**, não o backend Pinky |

**Prova final independente:** Ferrari + REA com 14/14 verificações específicas da instalação ([ronda de teste](https://github.com/martaxi-boss/VPS/actions/runs/45196155845)); Ruflo com papel executor e revisor distinto, hashes conferidos e fecho `completed` ([#45196549188](https://github.com/martaxi-boss/VPS/actions/runs/45196549188)). O teste de persistência do socket está em [#45195426400](https://github.com/martaxi-boss/VPS/actions/runs/45195426400) e a desativação reversível do vhost antigo em [#45197504440](https://github.com/martaxi-boss/VPS/actions/runs/45197504440).

**CI no `main` — 4 verificações concluídas com sucesso:** [backend Mega OTT](https://github.com/martaxi-boss/Vivoapk/actions/runs/45196903854), [infraestrutura](https://github.com/martaxi-boss/Vivoapk/actions/runs/45196904034), [Jellyfin 12.4 real descartável](https://github.com/martaxi-boss/Vivoapk/actions/runs/45196904065) e [compilação do plugin](https://github.com/martaxi-boss/Vivoapk/actions/runs/45196905034). CI sintética não comprova funcionamento físico.

**Decisões que continuam válidas:** sem reinstalar o Pinky, sem APKs ou vídeos na VPS, sem M3U ou tuner global com credenciais partilhadas, sem abrir o cofre ao público, sem criar segunda Skill/REA, sem alterar outros projetos. Manter os backups; nunca apagar o Jellyfin por conter `pink` no nome.

**Pendências funcionais reais:** login e reprodução efetivos em Android, iOS, LG e Samsung; logos específicos de cada canal; catálogo completo e seguro para várias subscrições, filmes, séries, episódios e EPG; revogação/renovação validada em contas reais; prova de Direct Play sem tráfego de vídeo pela VPS. O ramo `feature/p3-private-catalog-20261123` conserva trabalho experimental divergente — não fazer merge cego.

O objetivo é interoperabilidade com conteúdo licenciado e autorizado. Este repositório não contorna licenças comerciais ou DRM. Nenhuma credencial, URL de subscrição privada ou vídeo de terceiros é publicada no GitHub.

---

## Histórico da investigação inicial Vivo Player (preservado)

Projeto independente para estudar a integração de um **servidor IPTV próprio, com conteúdos autorizados**, com a aplicação comercial **Vivo Player** instalada numa Smart TV Samsung (Tizen) ou LG (webOS), sem criar uma aplicação de substituição.

## Conclusao Ferrari — app instalada vs controlo proprio (22/11/2026)

- **Vivo Player instalado:** a TV mostra "A conta expirou"; a ativacao pertence ao fabricante. O repositorio **nao contem o binario Vivo** nem uma sessao de depuracao propria, pelo que substituir a origem de ativacao ou provar uma integracao direta atraves de DuckDNS **nao foi demonstrado**. Nao tentar simular uma licenca.
- **Alternativa independente e licenciada:** leitores livres Xtream para Samsung Tizen e LG webOS possuem codigo aberto e pacotes publicados. O modulo de Xtream do projeto MIT `vantaflow/ultra-tv` passou **8/8 testes estaticos Ferrari** ([execucao #45064996879](https://github.com/martaxi-boss/VPS/actions/runs/45064996879)), nao testes fisicos. A rota pode ser indicada pelo servidor Mega OTT para Samsung/LG (credenciais sempre privadas).
- **Relatorio com evidencias, falhas e instaladores pesquisados:** [docs/ESTUDO-APP-DIRETA-2026-11-22.md](docs/ESTUDO-APP-DIRETA-2026-11-22.md).
- **Status:** nenhuma alteracao na TV, no dominio, no Nginx ou na ativacao Vivo; nenhuma reproducao real demonstrada.

## Endereço definitivo de teste

**`vivo-team.duckdns.org`** — confirmado pelo proprietário como o único hostname a utilizar na ligação de teste à Smart TV (22/11/2026). O endereço alternativo `vivoteam.dns.org` foi considerado numa investigação anterior, mas **não é o destino solicitado**. Não misturar os dois servidores nem presumir a porta Xtream.

**Último resultado real neste hostname:** DNS apontava para a VPS do proprietário; `http://vivo-team.duckdns.org/` apresentava a página padrão Nginx, `http://vivo-team.duckdns.org/player_api.php` devolvia HTTP 404, HTTPS não tinha certificado válido para o domínio. O servidor Xtream e a ligação à TV não estão validados. Fontes: [Ferrari #45061831368](https://github.com/martaxi-boss/VPS/actions/runs/45061831368), [#45062067969](https://github.com/martaxi-boss/VPS/actions/runs/45062067969), [#45062232933](https://github.com/martaxi-boss/VPS/actions/runs/45062232933).

**Teste Ferrari exclusivo ao hostname corrigido (22/11/2026):** [#45062660837](https://github.com/martaxi-boss/VPS/actions/runs/45062660837) — DNS resolvido; `GET /` HTTP 200 (615 bytes, HTML); `GET /player_api.php` HTTP 404 (162 bytes, HTML); classificação `not_confirmed_without_credentials`. O processo do Ferrari terminou com código 0, `report.status=completed`, mas `quality_gate=not_certified`. A aplicação/TV não foi conectada.

## Objetivo
- Identificar e documentar a interface de configuração de playlists do Vivo Player (MAC virtual + Device Key, quando disponíveis na aplicação).
- Confirmar o modo de ligação Xtream Codes oferecido oficialmente.
- Estudar apenas comunicações e sessões próprias, autorizadas, para interoperabilidade.
- Preparar um conector **independente** para configurar uma instalação própria, caso os testes o justifiquem.
- Testar numa Smart TV real com conteúdos que temos o direito de disponibilizar.

## Estado em 22-11-2026 (evidencias reais)
- [x] Motor Ferrari e ponte GitHub/VPS executados, sem reinstalacoes: [#45061831368](https://github.com/martaxi-boss/VPS/actions/runs/45061831368).
- [x] Testados ambos os dominios: HTTP raiz responde, mas as falhas de TLS impedem HTTPS validado.
- [x] Investigado o acesso nao autenticado a `/player_api.php`: No-IP devolveu **403**; DuckDNS, **404** ([#45062067969](https://github.com/martaxi-boss/VPS/actions/runs/45062067969)).
- [x] Verificada a rota DNS: o host No-IP mencionado mais recentemente **nao aponta para a VPS Ferrari**; o DuckDNS anterior aponta para ela, mas so exibe o Nginx padrao ([#45062232933](https://github.com/martaxi-boss/VPS/actions/runs/45062232933)).
- [x] Vivo Player `/playlists` abriu em Chromium Ferrari, mas o conteudo ficou protegido por **Cloudflare 403**, sem acesso ao painel autenticado ([#45061953094](https://github.com/martaxi-boss/VPS/actions/runs/45061953094)).
- [x] Codigo real `tools/xtream_diagnostic.py` submetido e **executado no Ferrari**: 8 autoverificacoes passaram e o teste sem credenciais devolveu `not_confirmed_without_credentials` ([#45062365308](https://github.com/martaxi-boss/VPS/actions/runs/45062365308)).
- [ ] Engenharia reversa dos endpoints autenticados do Vivo Player: nao comprovada.
- [ ] Ligacao da playlist a uma TV Samsung/LG e reproducao: nao testadas; exigem instalacao propria, Device Key e configuracao autorizada.

**Relatorio completo:** [docs/RESULTADOS-2026-11-22.md](docs/RESULTADOS-2026-11-22.md).

**Aviso:** a aplicação Vivo Player continua sujeita às suas condições e licença de ativação. Este projeto não contorna pagamentos, DRM, CAPTCHAs ou acessos de terceiros.

## Organização
- `docs/INVESTIGACAO.md`: evidências já obtidas, limites e roteiro de testes.
- `.gitignore`: evita publicar segredos e capturas sensíveis.

## Segurança
**Este repositório é público.** Nunca publicar MAC real da TV, Device Key, URL com credenciais, username, password, tokens de sessão, cookies, CAPTCHA, playlists privadas, capturas HAR com dados pessoais, chaves SSH, conteúdos sujeitos a direitos nem pacotes proprietários não autorizados. Mesmo conteúdo codificado em base64 continua público.

Guardar quaisquer dados de teste em canal privado e trabalhar apenas com dispositivos/serviços autorizados. Os pedidos para a ponte Ferrari no GitHub público devem conter exclusivamente entradas sintéticas ou não confidenciais.

## Arquitetura
```
Nosso servidor Xtream (autorizado)
        |
        | API Xtream Codes
        v
Vivo Player instalado na Samsung/LG
        ^
        |
Painel oficial do Vivo Player (associação MAC + Device Key)
```

A configuração oficial e a eventual automatização do painel são problemas distintos. Um servidor Xtream operacional **não autoriza automaticamente a TV** nem ativa a licença da aplicação.

**Correcao de configuracao do proprietario:** o `dns_link_for_samsung_lg` e o endereco **generico das Smart TVs Samsung e LG**, nao um URL exclusivo do IPTV Smarters. Para testes do Vivo Player instalado numa Samsung/LG, usar o servidor nativo Samsung/LG fornecido pelo Mega OTT; a compatibilidade efetiva e a associacao no painel Vivo continuam por validar. O hostname individual da subscricao nao e publicado neste repositorio publico.

## Reutilizacao Mega OTT da PinkIPTV (verificada em 22-11-2026)

A base privada `martaxi-boss/pink-iptv` ja implementa consulta API Mega OTT por token Bearer, autenticacao Xtream e preservacao separada de `dns_link` (Android/Windows) e `dns_link_for_samsung_lg` (Samsung/LG nativas). **O token e as credenciais reais nao estao publicados.**

- [Plano tecnico seguro](docs/PINK_MEGA_INTEROPERABILIDADE.md)
- [Ferramenta independente e read-only para obter o endereco Samsung/LG num ambiente privado](tools/mega_samsung_lg_lookup.py)
- [Ferrari: 13 testes sinteticos PASS, sem rede nem credenciais reais](https://github.com/martaxi-boss/VPS/actions/runs/45063258187)

Isto NAO significa que uma playlist ja foi associada ao Vivo Player. A API Mega fornece metadados de subscricao; o painel Vivo e a ativacao comercial continuam separados. `vivo-team.duckdns.org` nao substitui automaticamente a origem Xtream atribuida pela Mega.

## Ferramentas
A engenharia reversa e os testes remotos devem utilizar o **Motor Ferrari universal existente** (REA/Playwright), via GitHub Actions para a VPS; não instalar motores duplicados e não modificar outros projetos.
