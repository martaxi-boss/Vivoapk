# Separação de login e reprodução — candidato isolado

A password de ativação/login da linha de teste é fornecida pelo proprietário. Não existe requisito de pedir uma segunda password. Não se deve fabricar uma origem de reprodução a partir da password nem publicá-la.

Este candidato usa o plugin e os pedidos privados de catálogo existentes, sem nova aplicação. `RequireAuthorizedPlaybackGrant` está desligado por omissão. Quando ativado em ambiente autorizado, o plugin solicita um grant só ao selecionar um conteúdo para reprodução. O contrato **interno proposto**, não uma API Mega documentada, é `playback_grant` com `asset_id`, `kind`, `url` e `expires_at`. A origem é a mesma origem HTTPS previamente autorizada para a sessão. O grant deve ser emitido pelo fornecedor ou por integração expressamente autorizada, com expiração e restrições efetivamente aplicadas na origem; nunca inventar um URL público a partir da password.

Sem grant válido, o modo seguro não retorna fonte e não recorre ao URL que contém login/password. O código atual do backend não fornece grants; **não ativar este modo em produção** antes de confirmar a capacidade oficial do fornecedor e completar testes reais. O novo caminho de seleção não resolve por si só os leitores que solicitam a rota de streaming estática do Jellyfin nem prova redirecionamento de todos os clientes. As fixtures não representam a linha real. Não alterar DNS, login, catálogo, credenciais nem serviços na VPS.

O limite de 2 minutos é uma janela de aceitação conservadora, não prova de revogação no fornecedor nem de continuidade HLS após expirar o grant. Não registar URLs de grants em logs. O backend ainda deve garantir que os grants são autorizados por conta, conteúdo e sessão, e que a origem suporta a reprodução segura.
