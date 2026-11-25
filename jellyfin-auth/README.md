# Integração nativa Jellyfin 12.4 → VivoAPK

Contrato: [BOOTSTRAP](../BOOTSTRAP.md). Plugin C#/.NET 10 instalado, usando o backend privado por socket Unix para validar o login Mega OTT e disponibilizar navegação por catálogo próprio. Preservar identidades existentes, administração, seleção DNS, expiração e isolamento.

## Estado instalado versus objetivo

O plugin instalado oferece canais/pastas privadas TV, filmes, séries e episódios através de `IChannel`; não é tuner global nem EPG nativo certificado. A produção usa o caminho Direct Play histórico. O novo objetivo é permitir remux e transcodificação nativa Jellyfin/FFmpeg apenas para media autorizada, com guarda de rotas, isolamento por subscrição, revogação e cache temporária.

A branch `vivoapk/native-ffmpeg-20261125` contém um laboratório sintético ainda não aprovado: o reteste [#45628159340](https://github.com/martaxi-boss/Vivoapk/actions/runs/45628159340) falhou no acesso anónimo/de outra conta. Não instalar a branch nem ativar transcodificação global como atalho. FFmpeg H.264/AAC sintético passou na identidade do serviço, mas reprodução via Jellyfin e clientes físicos ainda por validar.

## Testes e implantação

A CI compila contra Jellyfin.Controller 12.4.0, executa testes sintéticos e ensaios descartáveis do servidor. Exigir isolamento, autorização por sessão/segmento, descodificação de imagem e som, limpeza e rollback antes de ativação. Usar o instalador/atualizador protegido existente; não alterar o backend ou conta administrativa. Nenhuma credencial privada em testes públicos.

[Provas históricas](../docs/HISTORICO_BOOTSTRAP_20261125.md) · [Operação VPS](../infra/README.md).
