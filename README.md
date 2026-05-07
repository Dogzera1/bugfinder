# Bug Finder

Ferramenta para encontrar **ofertas com alto desconto em lojas brasileiras** e estimar **margem de revenda no Mercado Livre**. Coleta de múltiplas fontes, pontua por desconto + qualidade comunitária, busca preço de referência no ML, calcula ROI.

## Status atual: Fase 5 — Telegram + scheduler 24/7

- [x] Arquitetura de **sources** plugáveis
- [x] Source: **Promobit** (agrega Amazon, Magalu, Netshoes, KaBuM, Pichau, AliExpress…)
- [x] Source: **Kabum** (eletrônicos / informática)
- [x] Detector com filtros duros (desconto mínimo, faixa de preço, rating)
- [x] **Enrichment ML via Playwright** — bypass do PolicyAgent / 2FA loops
- [x] **Validação de match por overlap de tokens** (evita comparar Asics com Qix)
- [x] **Calculadora de ROI**: margem estimada após taxa ML, frete absorvido
- [x] **Notificador Telegram** com ROI colorizado e link clicável
- [x] **Scheduler `watch`**: scans periódicos + dedup de candidatos já notificados
- [x] OAuth2 ML (client_credentials + authorization_code + refresh_token)
- [x] Persistência SQLite + export CSV
- [x] CLI: scan, candidates, scans, mark, sources, ml-auth, telegram-test, watch

### Próximas fases
- [ ] **Fase 3**: sources adicionais — Amazon BR, Magalu, Pelando, Casas Bahia
- [ ] **Fase 4**: filtros — velocidade de venda, reputação seller, blocklist de categorias

## Setup

### Instalação

```powershell
# 1. Virtualenv
py -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Instalar
pip install -e .

# 3. Copiar .env
copy .env.example .env
```

### (Recomendado) Configurar Telegram para notificações 24/7

1. **Criar bot**:
   - No Telegram, fala com [@BotFather](https://t.me/BotFather)
   - Manda `/newbot`, escolhe um nome (ex: `Meu Bug Finder`)
   - Recebe token tipo `1234567890:AAH...` — copia
2. **Achar seu chat_id**:
   - Manda qualquer mensagem pro seu novo bot (clica `/start`)
   - Acessa `https://api.telegram.org/bot<TOKEN>/getUpdates` no browser
   - Procura `"chat":{"id": NUMERO` — esse número é seu chat_id
3. **No `.env`**:
   ```
   TELEGRAM_BOT_TOKEN=1234567890:AAH...
   TELEGRAM_CHAT_ID=123456789
   ```
4. **Testar**:
   ```powershell
   bugfinder telegram-test
   ```
   Deve aparecer "🤖 Bug Finder ativo" no chat.

### (Recomendado) Configurar Mercado Livre para enrichment de ROI

Sem isto, o sistema funciona mas não calcula margem de revenda — você só vê desconto bruto.

1. Acesse https://developers.mercadolivre.com.br
2. Faça login com sua conta ML e clique em **"Crie sua aplicação"**
3. Preencha:
   - **Nome**: `bug-finder-pessoal` (ou o que preferir)
   - **Descrição curta**: "Lookup de preços para análise de revenda"
   - **URI de redirecionamento**: `https://localhost` (não usamos, mas é obrigatório)
   - **Tópicos**: pode deixar vazio
4. Em **Tipo de fluxo de OAuth**, **habilite "Server side"** (que inclui `client_credentials`)
5. Clique em **Criar**
6. Copie **Client ID** e **Client Secret** para o seu `.env`:
   ```
   ML_CLIENT_ID=...
   ML_CLIENT_SECRET=...
   ```
7. Rode `bugfinder scan` — vai imprimir "✓ enrichment: N com referência ML"

### Tunar parâmetros (`.env`)

```bash
MIN_DISCOUNT_PCT=20      # filtro: oferta precisa ter >=20% off
MIN_RATING_SCORE=0.5     # se tem rating, precisa ser >=50% positivo
MIN_SCORE=0.25           # threshold do score composto
MIN_ROI_PCT=0            # 15 = só mostra candidatos com ROI estimado >=15%

ML_FEE_PCT=0.14          # taxa ML média (Clássico ~12%, Premium ~17%)
FREIGHT_BUY=0            # frete que VOCÊ paga pra receber (0 se free)
FREIGHT_SELL=20          # frete que ABSORVE no anúncio do ML
```

## Uso

```powershell
# Listar sources
bugfinder sources

# Scan padrão (Promobit + Kabum, 80 ofertas/source, com ML enrichment)
bugfinder scan

# Scan rápido sem ML lookup (debug)
bugfinder scan --no-ml

# Foco
bugfinder scan --sources promobit
bugfinder scan --sources kabum --category hardware
bugfinder scan --query "iphone" --max-items 50

# Sensibilidade
bugfinder scan --min-discount 30
bugfinder scan --top 50

# Export
bugfinder scan --csv data/hoje.csv

# Histórico
bugfinder scans
bugfinder candidates --top 20
bugfinder candidates --scan-id 5

# Filtrar
bugfinder candidates --status new
bugfinder candidates --source promobit

# Status
bugfinder mark 42 bought
bugfinder mark 43 ignored
```

### Modo automático 24/7 (local)

```powershell
# Scans a cada 30min, notifica no Telegram só ROI ≥ 15%
bugfinder watch

# Customizando
bugfinder watch --interval 15 --min-roi 25 --min-match 0.7
bugfinder watch --sources promobit --query "notebook"
```

Ctrl+C pra parar. Cada candidato é notificado uma única vez (`notified_at` no DB).

Pra rodar em background no Windows: cria task no **Agendador de Tarefas** apontando pra `.\.venv\Scripts\python.exe -m bugfinder watch` com a pasta do projeto como working dir.

## Deploy 24/7 na Railway (cloud)

Container com Chromium pré-instalado roda no Railway sem precisar do seu PC ligado.

### 1. Pegar o refresh_token local

```powershell
bugfinder ml-token-info
```

Copia o `refresh_token` que aparece. **Esse token vale 6 meses** e renova sozinho — só precisa do seed inicial.

### 2. Subir o código pro GitHub

```powershell
cd "C:\Users\vict_\Desktop\bug finder"
git init
git add -A
git commit -m "initial bug finder"
# Criar repo privado em github.com/new e copiar a URL
git remote add origin https://github.com/SEU_USER/bug-finder.git
git branch -M main
git push -u origin main
```

> O `.gitignore` já exclui `.env`, `data/`, `.venv/` — secrets não vão pro repo.

### 3. Criar serviço na Railway

1. https://railway.app → **New Project** → **Deploy from GitHub repo** → seleciona o repo
2. Railway detecta o `Dockerfile` automaticamente e começa a buildar
3. **Settings** → **Volumes** → **New Volume**:
   - **Mount path**: `/data`
   - Tamanho: 1GB é suficiente (DB cresce devagar)
4. **Variables** — adiciona todas as env vars:

| Variável | Valor | Origem |
|---|---|---|
| `ML_CLIENT_ID` | seu client_id | do .env local |
| `ML_CLIENT_SECRET` | seu client_secret | do .env local |
| `ML_REFRESH_TOKEN_SEED` | refresh_token | do `ml-token-info` |
| `TELEGRAM_BOT_TOKEN` | seu bot token | do .env |
| `TELEGRAM_CHAT_ID` | seu chat_id | do .env |
| `MIN_ROI_PCT` | `15` (ou outro) | filtro de notificação |
| `DB_PATH` | `/data/bugfinder.db` | usa o volume |

5. **Deploy** — Railway buildará a imagem (~3-5min na primeira vez por causa do Chromium). Depois fica online.

6. **Logs** — confere no painel:
   ```
   watch iniciado — intervalo 30min, ROI mínimo 15%, match ≥ 50%
   ciclo 1 — 2026-05-07 ...
     scan #N: 80 ofertas, 12 candidatos
     enviando 3 notificações ...
   ```

### Custos

Railway tem plano **Hobby ($5/mês fixo)** com bastante crédito. Esse container roda em ~256MB RAM e dá pra ficar dentro do free tier de uso pra um único worker.

### Atualizando

`git push` → Railway faz redeploy automático. O volume e os tokens persistem.

### Solução de problemas

- **"ML lookup pulado"** nos logs = `ML_REFRESH_TOKEN_SEED` não foi definido OU expirou. Roda `bugfinder ml-token-info` local de novo, copia o seed novo.
- **Telegram não envia** = checa `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`; rode local `bugfinder telegram-test` pra confirmar que o par funciona.
- **OOM (out of memory)** = aumenta RAM do plano, ou baixa `--max-items`.

## Como o pipeline funciona

```
┌──────────┐  ┌──────────┐         ┌──────────┐  ┌────────────┐  ┌──────────┐
│ Promobit │  │  Kabum   │   ...   │  fontes  │→ │  Detector  │→ │ Candidatos│
└──────────┘  └──────────┘         └──────────┘  │ (filtros + │  │ (Fase 1) │
                                                  │   score)   │  └─────┬────┘
                                                  └────────────┘        │
                                                                        ▼
                                       ┌────────────────────────────────────┐
                                       │ Enricher (Fase 2)                  │
                                       │  - clean_title -> query ML         │
                                       │  - search ML -> mediana, P25, P75  │
                                       │  - compute viability (taxa+frete)  │
                                       └────────────────┬───────────────────┘
                                                        ▼
                                          ┌────────────────────────┐
                                          │ Candidato enriquecido  │
                                          │ + ROI estimado         │
                                          │ + margem em R$         │
                                          └────────────────────────┘
```

### Detector (Fase 1)

1. **Filtros duros**: descarta ofertas que não satisfazem
   - `discount_pct >= MIN_DISCOUNT_PCT`
   - `MIN_PRICE_BRL <= price <= MAX_PRICE_BRL`
   - se `rating_score` existe: `>= MIN_RATING_SCORE`

2. **Score** (0..1):
   - `0.6 · min(1, desconto/50)` (satura em 50% off)
   - `+ 0.3 · rating_score` (ou 0.5 neutro se desconhecido)
   - `+ 0.1 · log10(1+likes)/3`

3. Candidato passa se `score >= MIN_SCORE`.

### Enrichment (Fase 2)

Para cada candidato:
1. **clean_title()** simplifica o título da oferta (remove ruído de retail)
2. Busca a query no ML, pega top 15 ativos
3. Calcula mediana/P25/P75 dos preços
4. **compute_viability()**:
   - Receita: `ml_p25 · (1 - taxa) - taxa_fixa - frete_absorvido`
   - Custo: `preço_oferta + frete_compra`
   - Margem = receita - custo
   - ROI% = margem / custo

Usa P25 (não mediana) como preço de venda esperado — postura conservadora.

### Cores no ROI
- 🟢 **verde-escuro** ≥ 30%
- 🟢 verde 10–30%
- 🟡 amarelo 0–10%
- 🔴 vermelho negativo (não vale revender)

## Arquitetura

```
src/bugfinder/
├── cli.py              # entrypoint
├── config.py           # leitura de .env
├── models.py           # Offer, Candidate, MarketReference, Viability
├── scanner.py          # orquestração: coleta -> detect -> enrich -> persist
├── detector.py         # filtros + score (Fase 1)
├── enricher.py         # ML lookup + viabilidade (Fase 2)
├── matcher.py          # clean_title + busca no ML
├── viability.py        # cálculo de margem/ROI
├── storage.py          # SQLite (scans, offers, candidates)
├── auth/
│   └── ml_oauth.py     # OAuth2 client_credentials + cache
└── sources/
    ├── base.py         # Source ABC
    ├── promobit.py     # scrape de __NEXT_DATA__
    ├── kabum.py        # scrape de __NEXT_DATA__
    └── mercadolivre.py # cliente autenticado (referência, não discovery)
```

## Avisos

- Scraping respeitoso de páginas públicas. Rate limit conservador (3 retries, backoff exponencial).
- O cálculo de ROI usa modelo simplificado (taxa fixa + frete fixo) — **piso** de margem; tributação não está contemplada.
- Decisões de compra/revenda são responsabilidade do operador.
- Em erros de digitação grosseiros (ex: produto a 5% do valor), o seller costuma cancelar — o detector limita por `discount_pct ≤ 80%` no comportamento padrão; ajuste se quiser.
