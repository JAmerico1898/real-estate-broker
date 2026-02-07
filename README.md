# 🏠 Monitor de Coberturas - Lagoa, Rio de Janeiro

Monitora coberturas à venda na Lagoa (RJ) via Zap Imóveis.

## Arquitetura

```
GitHub Actions (diário 9:00 BRT)
  → Playwright + Stealth scrapes Zap Imóveis
  → Salva dados/coberturas.json
  → Envia push notification via Pushover
  → Commit automático no repositório

Streamlit Cloud
  → Lê dados/coberturas.json
  → Exibe interface com filtros
  → Permite disparar coleta manual
```

## Setup

### 1. Criar repositório no GitHub
Faça upload de todos os arquivos para um repositório no GitHub.

### 2. Configurar GitHub Secrets
No repositório: **Settings → Secrets and variables → Actions**

Adicione:
- `PUSHOVER_API_TOKEN` — Token da sua aplicação Pushover
- `PUSHOVER_USER_KEY` — Sua chave de usuário Pushover

### 3. Configurar Streamlit Cloud
1. Acesse [share.streamlit.io](https://share.streamlit.io)
2. Conecte seu repositório
3. Aponte para `app.py`
4. Em **Settings → Secrets**, adicione:
```toml
GITHUB_TOKEN = "ghp_seuTokenAqui"
GITHUB_REPO = "seuUsuario/seuRepo"
```

O `GITHUB_TOKEN` precisa ter permissão `actions:write` para que o botão
"Disparar Coleta" funcione. Crie em: GitHub → Settings → Developer settings → Personal access tokens.

### 4. Primeira execução
- Vá em **Actions** no GitHub e execute manualmente o workflow "Coleta de Coberturas"
- Ou aguarde a execução automática às 9:00h BRT

## Estrutura

```
├── app.py                      # Interface Streamlit
├── scraper.py                  # Scraper (roda no GitHub Actions)
├── requirements.txt            # Dependências do Streamlit Cloud
├── dados/
│   ├── coberturas.json         # Dados mais recentes
│   └── historico/              # Histórico diário
├── .github/
│   └── workflows/
│       └── scrape.yml          # GitHub Actions workflow
└── .streamlit/
    └── secrets.toml.exemplo    # Template de secrets
```

## Notas

- **Cloudflare**: O Zap Imóveis usa Cloudflare. O scraper usa `playwright-stealth`
  para tentar contornar, mas pode falhar. Os IPs do GitHub Actions são diferentes
  dos residenciais e podem ter mais sucesso.
- **Se o scraping falhar**: O arquivo `coberturas.json` será salvo com lista vazia.
  Verifique os logs do GitHub Actions para diagnóstico.
