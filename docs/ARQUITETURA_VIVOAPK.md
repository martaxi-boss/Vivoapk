# Arquitetura atual — VivoAPK / Jellyfin

Fonte normativa: [BOOTSTRAP](../BOOTSTRAP.md), decisão de 25/11/2026. A arquitetura de reprodução exclusivamente direta é histórica.

```text
Aplicação oficial Jellyfin (Android, Android TV, iOS, Windows, Tizen, webOS)
    -> HTTPS serveriptv.duckdns.org, login Mega OTT
Jellyfin 12.4 / plugin VivoAPK
    -> socket Unix privado -> backend VivoAPK
    -> valida subscrição, DNS atribuído e catálogo individual
    -> origem autorizada por subscrição
    -> reprodução nativa Jellyfin: remux quando possível, FFmpeg quando necessário
    -> imagem e áudio compatíveis entregues pelo Jellyfin via HTTPS
```

O painel Mega OTT é o único painel de gestão de clientes. Não criar APK, tuner M3U global, biblioteca partilhada, outro servidor ou login. Preservar o isolamento, a expiração e a conta administrativa existente; nenhum segredo ou endereço privado no cliente, logs ou GitHub. Cache de media estritamente temporária, com limites e limpeza.

**Realidade instalada:** o plugin usa `IChannel` e pastas por categoria/subscrição (64 reservas lógicas, duas famílias de origem). O caminho de reprodução atual ainda é histórico de Direct Play; a nova arquitetura não está instalada. O FFmpeg está presente, mas as rotas nativas da experiência sintética não passaram o controlo de acesso. A rede do serviço permanece isolada. O EPG visual nativo e os aparelhos físicos ainda precisam de validação. Não confundir requisitos com resultados.

Provas, SHA e decisões anteriores: [arquivo histórico](HISTORICO_BOOTSTRAP_20261125.md). Não seguir instruções de reprodução direta dos estudos datados.
