# Diagnóstico real: canal visível mas erro fatal no Jellyfin — 23/11/2026

## Sintoma observado no Android
A interface de `serveriptv.duckdns.org` apresenta **VivoAPK — TV de teste**, mas um pedido de reprodução devolveu `Erro na reprodução — A reprodução falhou devido a um erro fatal do leitor.`
A presença da secção não comprova reprodução de um canal real.

## Provas controladas (Ferrari GitHub → VPS)
- [#45101797715](https://github.com/martaxi-boss/VPS/actions/runs/45101797715): Jellyfin tem `PrivateNetwork=yes`, `IPAddressDeny=any`, `IPAddressAllow=loopback`. 12 linhas detetadas como falhas FFmpeg e 37 associadas a transcode nas linhas examinadas; serviços VivoAPK, Jellyfin e PINK continuam ativos. Não foram exportadas linhas brutas nem URLs privadas.
- [#45102084187](https://github.com/martaxi-boss/VPS/actions/runs/45102084187): a subscrição Mega OTT ativa passou `Xtream SUCCESS`, API devolveu 453 categorias TV e 1 canal válido na primeira categoria. As origens atualmente fornecidas para Android e Samsung/LG usam **HTTP**; não foi declarada `direct_source` nem extensão alternativa. Nenhum vídeo foi descarregado.
- [#45102214242](https://github.com/martaxi-boss/VPS/actions/runs/45102214242): tentativa de API em **HTTPS/443 com TLS verificado** nos dois hostnames do fornecedor resultou em `ConnectError`. Não demonstra ausência de todas as portas HTTPS possíveis, mas não há origem HTTPS funcional comprovada.
- [#45099394791](https://github.com/martaxi-boss/Vivoapk/actions/runs/45099394791): os testes com dados **sintéticos** em Jellyfin 12.4 mostram o item, isolam as contas, impedem acesso por ID e não persistem password no armazenamento. Isto não constitui um teste de reprodução real.

## Causa e restrições
1. O canal é apresentado por um `IChannel` no Jellyfin, com `MediaSourceInfo` que anuncia `SupportsDirectPlay/DirectStream/Transcoding=true` e caminho `.ts`.
2. Quando o cliente não suporta direto o formato, o Jellyfin tenta fornecer a fonte via servidor (FFmpeg, stream/remux/transcode). O isolamento de rede do serviço impede a ligação de saída ao fornecedor.
3. O serviço Jellyfin é servido ao utilizador por HTTPS, mas a origem do fornecedor é HTTP: reprodução direta no navegador pode ser bloqueada por mixed content/auto-upgrade; `.ts` não é um formato universal de reprodução direta nos navegadores.
4. **Não remover `PrivateNetwork`, não abrir IP de saída para o Jellyfin nem pôr o media relay na VPS sem a aprovação expressa do dono.** O requisito é que o dispositivo obtenha o vídeo diretamente do fornecedor.

## Caminhos técnicos válidos
- **Requisito de vídeo direto mantido:** obter do fornecedor uma origem HTTPS de reprodução com HLS/CORS adequado e testar o cliente real; se não houver, usar player nativo Xtream no dispositivo, onde o fluxo HTTP é obtido diretamente pelo próprio cliente. Uma única aplicação Jellyfin para todos os dispositivos não está demonstrada com essas condições.
- **Jellyfin Web obrigatório:** permitir ligação restrita do Jellyfin à origem de media e remux/transcode/proxy quando necessário, **o que faria o tráfego de vídeo passar pela VPS** e contraria o requisito atual. Exige decisão explícita e controlo de utilização de recursos.

## Estado
A alteração de piloto permanece opt-in e limitada a uma conta, no ramo draft. **Reprodução de conteúdo Mega OTT real: BLOQUEADA.** Não declarar funcional só porque os testes de backend/plugin ou a apresentação do canal passaram.
Não colocar credenciais, URL completo, detalhes da conta ou comandos ffmpeg nos relatórios públicos.
