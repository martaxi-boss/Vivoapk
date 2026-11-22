# PINK IPTV -> Vivoapk — transferencia e descontinuacao

Decisao do proprietario (22/11/2026): o PINK IPTV e descontinuado apos
aproveitar o que funciona; Vivoapk e a fonte de verdade do novo trabalho.

**Origem auditada:** martaxi-boss/pink-iptv, main no commit
32f3638d3e02f01558ef86127d865956f5c52e64.
**Destino:** martaxi-boss/Vivoapk, pasta backend/.

## Ja migrado (codigo, nao dados reais)
- cliente Mega OTT Bearer, importacao por subscricao, pesquisa paginada;
- autenticacao Xtream, seguranca de resolucao DNS e transporte IP pinado;
- armazenamento sem password da subscricao, rate limiting e tokens curtos;
- selecao do destino atraves da plataforma: Android/Android TV/Windows/ios
  recebem dns_link; Tizen/webOS recebem dns_link_for_samsung_lg;
- testes artificiais do cliente Mega, Xtream, origem por plataforma e API;
- esquema inicial das tabelas no NOVO banco, nao no PINK.

## Excluido propositadamente
- VPN WireGuard, gateway e instalacoes da antiga app;
- APK e overlays PINK, marca, artes e codigo Extreme InfiniTV GPL;
- passwords, tokens, chaves, MAC de televisores, playlists e dados privados;
- database antiga, estado da VPS PINK e conteudos media;
- video relay pelo Jellyfin.

## Limites e estado
Este commit **nao** liga o Jellyfin oficial ao Mega OTT: a app Jellyfin
continua com utilizador e password Jellyfin. O backend Vivoapk e Xtream
e exige app cliente compatível para login com credenciais Mega OTT.

Nenhuma API Vivoapk nova esta em producao, nem ha aplicacao Tizen/webOS
ou iOS criada nesta migracao. Testes de CI sao sinteticos.

## So depois, eliminar o PINK
1. Aprovar CI e testes de API no novo repo.
2. Instalar backend Vivoapk na propria pasta, isolado dos outros projetos.
3. Configurar HTTPS e dominio de controlo depois de verificar infraestrutura.
4. Validar login e reprodução DIRETA com linha autorizada Mega OTT em aparelhos.
5. Confirmar que todas as dependencias, servicos, certificados, bases e
   backups PINK foram migrados ou descontinuados com seguranca.
6. Desativar servicos antigos; so entao arquivar/apagar o repositorio PINK.

A opcao do proprietario de eliminar o projeto NAO significa que seja
seguro destruir os dados de quem ainda depende dele. Nenhuma remocao foi feita.
