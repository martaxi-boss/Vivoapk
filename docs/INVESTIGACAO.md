# Investigação técnica — Vivo Player

Data inicial: 22/11/2026. **Documento de trabalho; resultados em revisão.**

## Pedido
Utilizar a aplicação comercial Vivo Player já instalada numa Smart TV Samsung ou LG para ligar um serviço Xtream Codes próprio, estudar a integração com o painel que associa uma instalação por MAC + Device Key e, se viável, automatizar a configuração. Não construir um leitor novo para a televisão.

## Fontes e material de referência
1. Site do fabricante: https://vivo-player.com/
2. Portal de playlists referido pelo fabricante: https://www.vivo-player.com/playlists (disponibilidade e fluxo autenticado a confirmar em sessão própria).
3. Exemplo público de investigação **de outra família comercial**, sem presumir compatibilidade com Vivo: https://github.com/Dispatcharr/Dispatcharr/pull/1569 (IBO/IBOSOL; fechado sem merge).
4. BOOTSTRAP e contrato operacional do Ferrari: https://github.com/martaxi-boss/skill-universal/blob/main/BOOTSTRAP.md

## Evidência Ferrari obtida
- [Execução #45060469022](https://github.com/martaxi-boss/VPS/actions/runs/45060469022): chamada real ao executor privado REA para enumerar ferramentas. Resultado `completed`; apenas prova a existência/execução do catálogo.
- [Execução #45061225958](https://github.com/martaxi-boss/VPS/actions/runs/45061225958): `capture_process_scenario` com o Node privado e `playwright/cli.js install --dry-run`. `completed`; documenta o caminho previsto de instalação Playwright/Chromium, sem instalação nova e sem navegação ao portal.
- Em ambos os casos, a qualidade automática da funcionalidade de Vivo Player **não foi certificada**. Não usar o sucesso do workflow como evidência de engenharia reversa concluída.

## Factos / hipóteses / lacunas
| Questão | Estado |
| --- | --- |
| Vivo Player tem portal de associação de playlists | Documentação pública aponta nesse sentido; testar em sessão própria |
| Vivo Player suporta Xtream Codes | Anunciado publicamente; validar num dispositivo real |
| Comunicação privada do painel já foi descodificada | **Não demonstrado** |
| Endpoint do Vivo igual ao IBO | **Não há evidência; nunca pressupor** |
| Código-fonte oficial do Vivo é aberto | **Não encontrado** |
| Servidor de teste fornecido pelo proprietário responde por HTTPS/HTTP | **Ainda por validar** |
| A configuração funciona numa TV física | **Ainda por testar** |

## Plano de teste autorizado
1. **Servidor:** verificar apenas resolução DNS, protocolo, certificado, disponibilidade e API Xtream do serviço controlado pelo proprietário. Não explorar hosts terceiros nem supor caminhos privados.
2. **Portal:** numa instalação própria e licenciada do Vivo Player, observar a página oficial de gestão de playlists e identificar os campos e chamadas acessíveis ao utilizador.
3. **Protocolo:** documentar verbos HTTP, rotas, campos obrigatórios, respostas e erros reais, usando dados fictícios nas notas públicas. Confirmar autenticação, gestão da playlist e mecanismos normais de proteção sem os contornar.
4. **Compatibilidade:** associar uma playlist de teste com canais próprios/licenciados; validar categorias, EPG, reprodução e persistência após reinício da TV.
5. **Engenharia reversa adicional:** apenas se necessário e autorizado, estudar artefactos de aplicação obtidos legalmente (Tizen/webOS), sem publicar binários proprietários.
6. **Verificação:** guardar evidência técnica sanitizada (HTTP status, hashes de artefactos próprios, descrição de resultados), diferenciar testes simulados de testes reais e obter revisão independente no processo Ferrari.

## Critérios de conclusão
- O servidor de teste responde corretamente com credenciais autorizadas.
- A aplicação comercial genuína associa a playlist à nossa TV.
- Uma playlist de conteúdo autorizado carrega e reproduz na TV.
- A automação, se for desenvolvida, repete um fluxo autorizado sem contornar ativação, login ou CAPTCHA.
- É possível reproduzir o teste e proteger todas as credenciais.

## Dados não publicados
O proprietário forneceu um endereço DuckDNS para ensaio. **Não copiar endereços privados de teste, credenciais, MAC/Device Key ou tokens para este repositório público.** Usar valores não sensíveis em qualquer teste publicado e proteger logs locais. Se o servidor estiver acessível apenas por uma rede privada, efetuar o ensaio no ambiente autorizado e não tentar contornar restrições de acesso.

## Próxima evidência necessária
A primeira captura real autorizada da associação de playlist na interface oficial do Vivo Player e o teste de comunicação com o servidor do proprietário. Não declarar a engenharia reversa concluída antes disso.
