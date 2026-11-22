# Vivo Player — interoperabilidade Smart TV

Projeto independente para estudar a integração de um **servidor IPTV próprio, com conteúdos autorizados**, com a aplicação comercial **Vivo Player** instalada numa Smart TV Samsung (Tizen) ou LG (webOS), sem criar uma aplicação de substituição.

## Objetivo
- Identificar e documentar a interface de configuração de playlists do Vivo Player (MAC virtual + Device Key, quando disponíveis na aplicação).
- Confirmar o modo de ligação Xtream Codes oferecido oficialmente.
- Estudar apenas comunicações e sessões próprias, autorizadas, para interoperabilidade.
- Preparar um conector **independente** para configurar uma instalação própria, caso os testes o justifiquem.
- Testar numa Smart TV real com conteúdos que temos o direito de disponibilizar.

## Estado em 22-11-2026
- [x] Repositório de destino identificado: `martaxi-boss/Vivoapk`.
- [x] Motor Ferrari e ponte GitHub Actions/VPS existentes identificados, sem duplicar infraestrutura.
- [x] Ferramentas REA listadas numa execução real: [Ferrari #45060469022](https://github.com/martaxi-boss/VPS/actions/runs/45060469022).
- [x] Pré-verificação do Playwright realizada com `--dry-run` pelo Ferrari: [#45061225958](https://github.com/martaxi-boss/VPS/actions/runs/45061225958). Este teste **não** abriu o portal e **não** demonstra integração.
- [ ] Captura autorizada do portal Vivo Player: pendente.
- [ ] Mapa de endpoints privados do painel: pendente, **não assumir API da família IBO**.
- [ ] Autenticação e associação de playlist à nossa instalação da TV: pendentes.
- [ ] Teste real na Samsung/LG e reprodução do conteúdo autorizado: pendente.

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

## Ferramentas
A engenharia reversa e os testes remotos devem utilizar o **Motor Ferrari universal existente** (REA/Playwright), via GitHub Actions para a VPS; não instalar motores duplicados e não modificar outros projetos.
