# 🏠 Monitor de Coberturas - Lagoa, Rio de Janeiro

Aplicativo Streamlit que monitora coberturas à venda na Lagoa (RJ) via **Zap Imóveis** e envia notificações push via **Pushover**.

## 📋 O que o app faz

1. **Coleta automática**: Acessa o Zap Imóveis diariamente às 9:00h (horário de Brasília)
2. **Extrai dados**: Endereço, preço, área, quartos, vagas, link e foto de cada cobertura
3. **Notifica via Pushover**: Envia push notification para seu celular/desktop
4. **Interface visual**: Exibe os resultados em cards e permite busca manual
5. **Exporta CSV**: Permite baixar os dados em planilha

## 🚀 Instalação

### Pré-requisitos
- Python 3.10 ou superior

### Passo a passo

```bash
# 1. Clone ou copie a pasta do projeto
cd cobertura_monitor

# 2. Instale as dependências
pip install -r requirements.txt

# 3. Instale o navegador Chromium para o Playwright
playwright install chromium

# 4. Configure suas credenciais (veja seção abaixo)
cp .streamlit/secrets.toml.exemplo .streamlit/secrets.toml
# Edite o arquivo secrets.toml com suas credenciais

# 5. Execute o app
streamlit run app.py
```

## 🔑 Configuração do Pushover

1. Crie uma conta em [pushover.net](https://pushover.net/)
2. Instale o app Pushover no celular (iOS/Android)
3. Copie seu **User Key** (aparece na tela inicial)
4. Crie uma aplicação em [pushover.net/apps/build](https://pushover.net/apps/build)
5. Copie o **API Token** da aplicação
6. Cole ambos no arquivo `.streamlit/secrets.toml`:

```toml
PUSHOVER_API_TOKEN = "seu_token_aqui"
PUSHOVER_USER_KEY = "sua_chave_aqui"
```

## 📂 Estrutura do projeto

```
cobertura_monitor/
├── app.py                          # Aplicativo principal
├── requirements.txt                # Dependências Python
├── README.md                       # Este arquivo
└── .streamlit/
    ├── secrets.toml.exemplo        # Template de credenciais
    └── secrets.toml                # Suas credenciais (NÃO versionar!)
```

## ⚠️ Observações importantes

### Sobre o web scraping
- O Zap Imóveis é um site dinâmico (SPA). O app usa **Playwright** para simular um navegador real.
- Se o Zap alterar a estrutura do site, os **seletores CSS** no código podem precisar de atualização.
- O app tenta múltiplas estratégias de extração (seletores CSS, dados do Next.js, variáveis JavaScript).

### Sobre o agendamento
- O agendamento funciona apenas enquanto o app Streamlit estiver **rodando**.
- Para execução 24/7, considere hospedar em um servidor (VPS, Streamlit Cloud, etc.).
- No Streamlit Cloud, o app "dorme" se ninguém acessar por um tempo.

### Sobre proteção anti-bot
- O Zap Imóveis pode bloquear acessos automatizados.
- O app usa User-Agent de navegador real e pausas entre ações para minimizar detecções.
- Se a coleta falhar consistentemente, o site pode estar usando CAPTCHA ou Cloudflare.
