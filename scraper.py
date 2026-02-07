"""
Scraper de Coberturas - Lagoa, Rio de Janeiro
==============================================
Roda no GitHub Actions com Playwright + Stealth.
Salva resultados em dados/coberturas.json e envia push via Pushover.
"""

import json
import os
import re
import time
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

# Tenta importar stealth; se não tiver, segue sem
try:
    from playwright_stealth import stealth_sync
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False
    print("⚠️ playwright-stealth não instalado. Rodando sem stealth.")

# ============================================================
# CONFIG
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

FUSO = ZoneInfo("America/Sao_Paulo")
PUSHOVER_URL = "https://api.pushover.net/1/messages.json"

URL_ZAP = (
    "https://www.zapimoveis.com.br/venda/cobertura/"
    "rj+rio-de-janeiro+zona-sul+lagoa/"
    "?transacao=venda"
    "&onde=%2CRio+de+Janeiro%2CRio+de+Janeiro%2CZona+Sul%2CLagoa"
    "%2C%2C%2Cneighborhood%2CBR%3ERio+de+Janeiro%3ENULL%3ERio+de+Janeiro"
    "%3EZona+Sul%3ELagoa%2C-22.96182%2C-43.203077%2C"
    "&tipos=cobertura_residencial"
)

DADOS_DIR = Path("dados")
HISTORICO_DIR = DADOS_DIR / "historico"
COBERTURAS_FILE = DADOS_DIR / "coberturas.json"


# ============================================================
# COLETA
# ============================================================
def coletar():
    """Coleta coberturas via Playwright com stealth."""
    coberturas = []
    api_responses = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )

        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            extra_http_headers={
                "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )

        page = context.new_page()

        # Aplica stealth se disponível
        if HAS_STEALTH:
            stealth_sync(page)
            logger.info("✅ Stealth aplicado")

        # Intercepta respostas de rede
        def on_response(response):
            try:
                url = response.url.lower()
                keywords = [
                    "listing", "search", "result", "property",
                    "imovel", "glue", "bff", "api",
                    "/v2/", "/v3/", "/v1/",
                ]
                if any(kw in url for kw in keywords) and response.status == 200:
                    try:
                        body = response.text()
                        if body and len(body) > 200:
                            data = json.loads(body)
                            api_responses.append({
                                "url": response.url,
                                "data": data,
                                "size": len(body),
                            })
                            logger.info(f"📡 API: {response.url[:100]} ({len(body):,}B)")
                    except Exception:
                        pass
            except Exception:
                pass

        page.on("response", on_response)

        # Acessa o site
        logger.info("🌐 Acessando Zap Imóveis...")
        page.goto(URL_ZAP, wait_until="domcontentloaded", timeout=60000)

        # Verifica se caiu no Cloudflare
        title = page.title()
        logger.info(f"📄 Título: {title}")

        if "cloudflare" in title.lower() or "attention" in title.lower():
            logger.warning("🛡️ Cloudflare detectado! Aguardando challenge...")
            # Espera mais tempo para o challenge resolver
            page.wait_for_timeout(10000)
            title = page.title()
            logger.info(f"📄 Título após espera: {title}")

            if "cloudflare" in title.lower() or "attention" in title.lower():
                logger.error("❌ Cloudflare não foi contornado.")
                page.screenshot(path="debug_cloudflare.png")
                browser.close()
                return []

        # Espera carregamento
        logger.info("⏳ Aguardando carregamento...")
        page.wait_for_timeout(8000)

        # Scroll para carregar mais
        logger.info("📜 Scroll...")
        for _ in range(8):
            page.evaluate("window.scrollBy(0, window.innerHeight)")
            page.wait_for_timeout(2000)

        page.wait_for_timeout(3000)
        logger.info(f"📡 APIs interceptadas: {len(api_responses)}")

        # Processa APIs interceptadas
        if api_responses:
            coberturas = processar_apis(api_responses)

        # Fallback: __NEXT_DATA__
        if not coberturas:
            logger.info("Tentando __NEXT_DATA__...")
            coberturas = extrair_next_data(page)

        # Fallback: HTML direto
        if not coberturas:
            logger.info("Tentando HTML...")
            coberturas = extrair_html(page)

        # Debug: salva screenshot se falhou
        if not coberturas:
            page.screenshot(path="debug_pagina.png")
            html = page.content()
            with open("debug_pagina.html", "w", encoding="utf-8") as f:
                f.write(html)
            logger.error(f"❌ Nenhuma cobertura. Título: {page.title()}")
            logger.error(f"URL: {page.url}")
            body_text = page.evaluate("() => document.body?.innerText?.substring(0, 500) || ''")
            logger.error(f"Body: {body_text}")

        browser.close()

    logger.info(f"✅ Total: {len(coberturas)} coberturas")
    return coberturas


# ============================================================
# PROCESSAMENTO
# ============================================================
def processar_apis(responses):
    responses.sort(key=lambda x: x["size"], reverse=True)
    for resp in responses:
        found = buscar_listings(resp["data"])
        if found:
            logger.info(f"✅ {len(found)} coberturas via API: {resp['url'][:80]}")
            return found
    return []


def buscar_listings(data, depth=0):
    if depth > 6:
        return []
    chaves = ["listings", "results", "searchResults", "items", "hits", "data", "search"]

    if isinstance(data, dict):
        for key, value in data.items():
            if any(c in key.lower() for c in chaves):
                if isinstance(value, list) and value:
                    parsed = parsear_lista(value)
                    if parsed:
                        return parsed
                elif isinstance(value, dict):
                    found = buscar_listings(value, depth + 1)
                    if found:
                        return found
            elif isinstance(value, (dict, list)):
                found = buscar_listings(value, depth + 1)
                if found:
                    return found
    elif isinstance(data, list) and data:
        return parsear_lista(data)
    return []


def parsear_lista(items):
    coberturas = []
    for item in items:
        if not isinstance(item, dict):
            continue
        listing = item.get("listing", item)
        if isinstance(listing, dict):
            c = extrair_campos(listing)
            if c:
                coberturas.append(c)
    return coberturas if any(c.get("preco") for c in coberturas) else []


def extrair_campos(obj):
    c = {"endereco": "", "preco": "", "area_m2": "", "quartos": "", "vagas": "", "link": "", "foto": ""}

    # Endereço
    addr = obj.get("address", {})
    if isinstance(addr, dict):
        parts = [str(addr[k]) for k in ["street", "streetNumber", "neighborhood", "city"] if addr.get(k)]
        c["endereco"] = ", ".join(parts)
    elif isinstance(addr, str):
        c["endereco"] = addr

    # Preço
    pricing = obj.get("pricingInfos", [])
    if isinstance(pricing, list):
        for p in pricing:
            if isinstance(p, dict):
                val = p.get("price") or p.get("salePrice")
                if val:
                    try:
                        n = int(str(val).replace(".", "").replace(",", ""))
                        c["preco"] = f"R$ {n:,.0f}".replace(",", ".")
                    except (ValueError, TypeError):
                        c["preco"] = f"R$ {val}"
                    break
    if not c["preco"]:
        for k in ["price", "preco", "valor"]:
            if obj.get(k):
                try:
                    n = int(str(obj[k]).replace(".", "").replace(",", "").replace("R$", "").strip())
                    c["preco"] = f"R$ {n:,.0f}".replace(",", ".")
                except (ValueError, TypeError):
                    c["preco"] = str(obj[k])
                break

    # Área, quartos, vagas
    for k in ["usableAreas", "totalAreas", "area"]:
        v = obj.get(k)
        if v:
            c["area_m2"] = f"{v[0] if isinstance(v, list) else v} m²"
            break
    for k in ["bedrooms", "quartos"]:
        v = obj.get(k)
        if v:
            c["quartos"] = str(v[0] if isinstance(v, list) else v)
            break
    for k in ["parkingSpaces", "vagas"]:
        v = obj.get(k)
        if v:
            c["vagas"] = str(v[0] if isinstance(v, list) else v)
            break

    # Link
    link = obj.get("link", {})
    href = link.get("href", "") if isinstance(link, dict) else (link if isinstance(link, str) else "")
    if not href:
        href = obj.get("url", "") or obj.get("href", "")
    if not href:
        lid = obj.get("id") or obj.get("externalId")
        if lid:
            href = f"/imovel/{lid}"
    if href and href.startswith("/"):
        href = "https://www.zapimoveis.com.br" + href
    c["link"] = href

    # Foto
    imgs = obj.get("images", [])
    if isinstance(imgs, list) and imgs:
        foto = imgs[0]
        if isinstance(foto, dict):
            foto = foto.get("url", "") or foto.get("src", "")
        if foto and not str(foto).startswith("http"):
            foto = f"https://resizedimgs.zapimoveis.com.br/fit-in/800x600/{foto}"
        c["foto"] = str(foto)

    return c


def extrair_next_data(page):
    try:
        text = page.evaluate("() => document.querySelector('script#__NEXT_DATA__')?.textContent")
        if not text:
            return []
        data = json.loads(text)
        return buscar_listings(data)
    except Exception as e:
        logger.warning(f"__NEXT_DATA__ erro: {e}")
        return []


def extrair_html(page):
    coberturas = []
    try:
        links = page.evaluate("""
            () => {
                const sels = ['a[href*="/imovel/"]', 'a[href*="cobertura"]', 'article a[href]'];
                const found = new Set();
                const results = [];
                for (const sel of sels) {
                    document.querySelectorAll(sel).forEach(a => {
                        if (!found.has(a.href)) {
                            found.add(a.href);
                            const card = a.closest('div[class*="card"], article, [data-testid]') || a;
                            results.push({ href: a.href, text: card.innerText.substring(0, 1000) });
                        }
                    });
                }
                return results;
            }
        """)

        for link in links:
            text = link.get("text", "")
            preco_m = re.search(r'R\$\s*[\d.,]+', text)
            if preco_m:
                area_m = re.search(r'(\d+)\s*m²', text)
                quartos_m = re.search(r'(\d+)\s*(?:quarto|dorm|suíte)', text, re.I)
                vagas_m = re.search(r'(\d+)\s*(?:vaga|garag)', text, re.I)
                coberturas.append({
                    "endereco": text.split("\n")[0][:120].strip(),
                    "preco": preco_m.group(0),
                    "area_m2": f"{area_m.group(1)} m²" if area_m else "",
                    "quartos": quartos_m.group(1) if quartos_m else "",
                    "vagas": vagas_m.group(1) if vagas_m else "",
                    "link": link.get("href", ""),
                    "foto": "",
                })
    except Exception as e:
        logger.error(f"HTML erro: {e}")
    return coberturas


# ============================================================
# PUSHOVER
# ============================================================
def enviar_pushover(coberturas):
    token = os.environ.get("PUSHOVER_API_TOKEN")
    user = os.environ.get("PUSHOVER_USER_KEY")
    if not token or not user:
        logger.warning("Pushover não configurado")
        return False

    agora = datetime.now(FUSO).strftime("%d/%m/%Y %H:%M")
    mensagens = []
    msg = f"🏠 {len(coberturas)} coberturas na Lagoa\n{agora}\n\n"

    for i, c in enumerate(coberturas, 1):
        item = (
            f"#{i} {c.get('preco', 'N/A')}\n"
            f"📍 {c.get('endereco', 'N/A')}\n"
            f"📐 {c.get('area_m2', '-')} | 🛏 {c.get('quartos', '-')} | 🚗 {c.get('vagas', '-')}\n"
            f"🔗 {c.get('link', '')}\n\n"
        )
        if len(msg) + len(item) > 1000:
            mensagens.append(msg)
            msg = "🏠 Coberturas (cont.)\n\n"
        msg += item
    mensagens.append(msg)

    ok = True
    for i, m in enumerate(mensagens):
        try:
            r = requests.post(PUSHOVER_URL, data={
                "token": token, "user": user,
                "message": m, "title": "Coberturas Lagoa",
                "priority": 0, "html": 1,
            }, timeout=10)
            if r.status_code == 200:
                logger.info(f"Pushover {i+1}/{len(mensagens)} ✓")
            else:
                logger.error(f"Pushover erro: {r.status_code}")
                ok = False
            time.sleep(1)
        except Exception as e:
            logger.error(f"Pushover: {e}")
            ok = False
    return ok


# ============================================================
# SALVAR DADOS
# ============================================================
def salvar(coberturas):
    DADOS_DIR.mkdir(exist_ok=True)
    HISTORICO_DIR.mkdir(exist_ok=True)

    agora = datetime.now(FUSO)
    resultado = {
        "ultima_coleta": agora.isoformat(),
        "total": len(coberturas),
        "coberturas": coberturas,
    }

    # Arquivo principal (lido pelo Streamlit)
    with open(COBERTURAS_FILE, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)

    # Histórico diário
    hist_file = HISTORICO_DIR / f"{agora.strftime('%Y-%m-%d')}.json"
    with open(hist_file, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)

    logger.info(f"💾 Salvo em {COBERTURAS_FILE}")


# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("INÍCIO DA COLETA")
    logger.info("=" * 50)

    coberturas = coletar()

    if coberturas:
        salvar(coberturas)
        enviar_pushover(coberturas)
        logger.info(f"✅ {len(coberturas)} coberturas coletadas e salvas")
    else:
        # Salva resultado vazio para o Streamlit saber que tentou
        salvar([])
        logger.error("❌ Nenhuma cobertura encontrada")

    logger.info("FIM")
