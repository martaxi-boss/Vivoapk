# VivoAPK — auditoria Ferrari/REA e higiene controlada (23/11/2026)

## Fontes de verdade

- Código de produção auditado: `cbc926c641463818f755df7a2e9c797ad7228201` no ramo de HLS.
- Ferrari/REA original via GitHub Actions, leitura real da VPS: [#45190935917](https://github.com/martaxi-boss/VPS/actions/runs/45190935917) e [#45191441518](https://github.com/martaxi-boss/VPS/actions/runs/45191441518).
- Nenhuma credencial, hostname individual de subscrição, playlist ou media privados neste documento.

## Observações reais da VPS

1. Jellyfin (`jellyfin-pink.service`), VivoAPK, Nginx e PostgreSQL ativos, ambos os sockets HTTP 200; HTTPS público válido.
2. Backend `/srv/projects/Vivoapk/run/backend.sock` estava com permissões `0666`, grupo `www-data`; a pasta é `vivoapksvc:www-data` com modo `2770`. O Jellyfin corre como `jellyfinpink` com grupo primário `www-data`. Há passagem segura a `0660` sem retirar acesso ao Jellyfin.
3. O `UMask=0007` do serviço **não** produziu o modo desejado. É necessário limitar o socket **depois** do bind do Uvicorn. Não basta repetir `UMask`.
4. Plugin `Enabled=true` e `PilotLiveChannels=true`; `MonitorEntitlements=false` e `ProvisionPrivateLibraries=false`. Piloto é uma única conta em memória e cinco canais no máximo, não catálogo multiutilizador.
5. A fonte explicitamente devolvia `HasImage=false` e não suportava quaisquer imagens. A imagem genérica incorporada neste patch é apenas capa para a **secção de testes**; não representa os logótipos oficiais dos canais.
6. Jellyfin apresentou `DirectPlay=true` e `DirectStream/Transcoding=false` em testes sintéticos. Isto **não** comprova reprodução em aparelhos reais nem prova a ausência de bytes de vídeo encaminhados pela VPS.
7. O serviço antigo `pink-iptv-backend.service` e `pink-vpn.service` está ausente; a antiga pasta Pinky já não existe. O ficheiro `/etc/nginx/sites-enabled/pink-iptv-https.conf` ainda existe com certificado, e não se deve apagar sem confirmar consumidores.
8. A fonte `main` está atrás dos ramos de catálogo/HLS; não fazer merge cego de patches experimentais.
9. O callback `GetChannelItemMediaInfo` não recebe `userId`, e o `IChannel` mantém um snapshot estático por piloto. Os testes atuais de dois utilizadores recusam acessos cruzados sintéticos, mas isto **não certifica** segurança de um catálogo real multicliente.
10. Os cinco canais portugueses não cobrem Live/VOD/Series/EPG geral, nem as imagens oficiais por estação.

## Alterações seguras neste ramo de higiene

- Acrescentar `infra/secure_runtime_socket.py` com guarda de proprietários, grupo, symlinks, modo de diretório, timeout e validação de resultado. É um processo pontual `ExecStartPost` no **mesmo serviço existente**, sem daemon, túnel, proxy ou nova aplicação.
- Alinhar `infra/deploy.py` para que futuras instalações usem exatamente este endurecimento, com testes locais.
- Acrescentar capa PNG local genérica e teste de integridade de PNG ao `IChannel` (sem acesso a URLs de imagens do fornecedor ou alteração da origem de vídeo).
- O piloto continua desativado por omissão na fonte. A alteração de código **não faz instalação automática** na VPS.

## Portas de aceitação ainda obrigatórias

- CI Python/.NET e integração Jellyfin 12.4 com duas contas reais ou fictícias isoladas, sem password em armazenamento.
- Deploy transacional e com rollback do endurecimento para o mesmo serviço, confirmando socket `0660`, Jellyfin HTTP 200, admin preservado, sem alterações ao Nginx nem aos outros projetos.
- Teste de reprodução **num dispositivo Android real** usando conta autorizada, medição de tráfego de vídeo na VPS e desativação imediata de qualquer encaminhamento. Testes específicos iOS/Tizen/webOS ainda em falta.
- Implementar o catálogo completo individual, EPG e logótipos reais por canal com origem segura, não apenas o piloto. Manter funcionalidade em draft se não houver testes.
- Não eliminar Jellyfin nem vestígios de Nginx potencialmente ativos só porque têm `pink` no nome.

**Estado: auditoria factual e correções de fonte submetidas a CI; sucesso final depende de execução e read-back.**
