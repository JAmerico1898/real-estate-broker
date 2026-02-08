"""
Scraper de Coberturas - Lagoa, Rio de Janeiro
==============================================
Estratégia (em ordem):
  1. API direta com diferentes combinações de headers (3 tentativas)
  2. Playwright com stealth como fallback
"""

import json
import os
import random
import time
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

import requests as req

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

FUSO = ZoneInfo("America/Sao_Paulo")
PUSHOVER_URL = "https://api.pushover.net/1/messages.json"

DADOS_DIR = Path("dados")
HISTORICO_DIR = DADOS_DIR / "historico"
COBERTURAS_FILE = DADOS_DIR / "coberturas.json"

GLUE_API_URL = "https://glue-api.zapimoveis.com.br/v2/listings"

GLUE_API_PARAMS = {
    "business": "SALE",
    "listingType": "USED",
    "unitTypes": "PENTHOUSE",
    "addressNeighborhood": "Lagoa",
    "addressCity": "Rio de Janeiro",
    "addressState": "Rio de Janeiro",
    "addressZone": "Zona Sul",
    "addressLocationId": "BR>Rio de Janeiro>NULL>Rio de Janeiro>Zona Sul>Lagoa",
    "addressPointLat": "-22.96182",
    "addressPointLon": "-43.203077",
    "addressType": "neighborhood",
    "categoryPage": "RESULT",
    "size": "36",
    "from": "0",
    "page": "1",
    "includeFields": (
        "search(result(listings(listing(displayAddressType,amenities,usableAreas,"
        "constructionStatus,listingType,description,title,stamps,createdAt,"
        "floors,unitTypes,nonActivationReason,providerId,propertyType,"
        "unitSubTypes,unitsOnTheFloor,legacyId,id,portal,unitFloor,"
        "parkingSpaces,updatedAt,address,suites,publicationType,"
        "externalId,bathrooms,usageTypes,totalAreas,advertiserId,"
        "advertiserContact,whatsappNumber,bedrooms,acceptExchange,"
        "pricingInfos,showPrice,resale,buildings,capacityLimit,"
        "status,ppiCategory,advertiserType),account(id,name,logoUrl,"
        "licenseNumber,showAddress,legacyVivarealId,legacyZapId,"
        "minisite),medias,accountLink,link)),totalCount))"
    ),
}

# Diferentes perfis de headers para tentar
HEADER_PROFILES = [
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.zapimoveis.com.br/venda/cobertura/rj+rio-de-janeiro+zona-sul+lagoa/",
        "Origin": "https://www.zapimoveis.com.br",
        "x-domain": "www.zapimoveis.com.br",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    },
    {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "pt-BR,pt;q=0.9",
        "Referer": "https://www.zapimoveis.com.br/",
        "Origin": "https://www.zapimoveis.com.br",
        "x-domain": "www.zapimoveis.com.br",
    },
    {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        "Referer": "https://www.zapimoveis.com.br/venda/cobertura/rj+rio-de-janeiro+zona-sul+lagoa/",
        "Origin": "https://www.zapimoveis.com.br",
        "x-domain": "www.zapimoveis.com.br",
    },
]


# ============================================================
# COLETA VIA API DIRETA
# ============================================================
def coletar_via_api():
    logger.info("📡 Tentando API direta...")

    for attempt, headers in enumerate(HEADER_PROFILES, 1):
        logger.info(f"  Tentativa {attempt}/{len(HEADER_PROFILES)}...")

        coberturas = tentar_api_com_headers(headers)
        if coberturas:
            return coberturas

        # Espera entre tentativas
        wait = random.uniform(3, 8)
        logger.info(f"  Aguardando {wait:.1f}s...")
        time.sleep(wait)

    logger.warning("📡 Todas tentativas da API falharam")
    return []


def tentar_api_com_headers(headers):
    todas = []
    page = 1

    while page <= 10:
        params = {**GLUE_API_PARAMS, "page": str(page), "from": str((page - 1) * 36)}

        try:
            r = req.get(GLUE_API_URL, params=params, headers=headers, timeout=30)
            logger.info(f"    Pág {page}: {r.status_code} ({len(r.content):,}B)")

            if r.status_code == 403:
                logger.warning(f"    Bloqueado (403)")
                # Tenta logar corpo do erro para diagnosticar
                try:
                    logger.info(f"    Corpo 403: {r.text[:300]}")
                except Exception:
                    pass
                return []

            if r.status_code != 200:
                return []

            data = r.json()
            search = data.get("search", data)
            result = search.get("result", search)
            listings = result.get("listings", [])
            total = result.get("totalCount", 0)

            if not listings:
                break

            logger.info(f"    {len(listings)} listings (total: {total})")

            for item in listings:
                c = extrair_campos(
                    item.get("listing", item),
                    item.get("medias", []),
                    item.get("link", {}),
                )
                if c and c.get("preco"):
                    todas.append(c)

            if len(listings) < 36 or len(todas) >= total:
                break

            page += 1
            time.sleep(random.uniform(1, 3))

        except Exception as e:
            logger.error(f"    Erro: {e}")
            return []

    return todas


# ============================================================
# EXTRAÇÃO DE CAMPOS
# ============================================================
def extrair_campos(listing, medias=None, link_data=None):
    if not isinstance(listing, dict):
        return None

    c = {"endereco": "", "preco": "", "area_m2": "", "quartos": "", "vagas": "", "link": "", "foto": ""}

    # Endereço
    addr = listing.get("address", {})
    if isinstance(addr, dict):
        parts = [str(addr[k]) for k in ["street", "streetNumber", "neighborhood", "city"] if addr.get(k)]
        c["endereco"] = ", ".join(parts)

    # Preço
    for p in (listing.get("pricingInfos") or []):
        if isinstance(p, dict) and p.get("businessType") == "SALE":
            val = p.get("price")
            if val:
                try:
                    n = int(str(val).replace(".", "").replace(",", ""))
                    c["preco"] = f"R$ {n:,.0f}".replace(",", ".")
                except (ValueError, TypeError):
                    c["preco"] = f"R$ {val}"
            break

    # Área
    areas = listing.get("usableAreas")
    if isinstance(areas, list) and areas:
        c["area_m2"] = f"{areas[0]} m²"
    else:
        t = listing.get("totalAreas")
        if isinstance(t, list) and t:
            c["area_m2"] = f"{t[0]} m²"

    # Quartos & vagas
    beds = listing.get("bedrooms")
    c["quartos"] = str(beds[0]) if isinstance(beds, list) and beds else ""
    park = listing.get("parkingSpaces")
    c["vagas"] = str(park[0]) if isinstance(park, list) and park else ""

    # Link
    href = ""
    if isinstance(link_data, dict):
        href = link_data.get("href", "")
    if not href:
        lid = listing.get("id") or listing.get("externalId")
        if lid:
            href = f"/imovel/{lid}"
    if href and href.startswith("/"):
        href = "https://www.zapimoveis.com.br" + href
    c["link"] = href

    # Foto
    if isinstance(medias, list):
        for m in medias:
            if isinstance(m, dict) and m.get("url"):
                url = m["url"]
                if not url.startswith("http"):
                    url = f"https://resizedimgs.zapimoveis.com.br/fit-in/800x600/{url}"
                c["foto"] = url
                break

    return c


# ============================================================
# FALLBACK: PLAYWRIGHT
# ============================================================
def coletar_via_playwright():
    logger.info("🌐 Fallback: Playwright...")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("Playwright não disponível")
        return []

    try:
        from playwright_stealth import stealth_sync
        has_stealth = True
    except ImportError:
        has_stealth = False

    api_responses = []

    url = (
        "https://www.zapimoveis.com.br/venda/cobertura/"
        "rj+rio-de-janeiro+zona-sul+lagoa/"
        "?transacao=venda&tipos=cobertura_residencial"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="pt-BR",
        )
        page = ctx.new_page()
        if has_stealth:
            stealth_sync(page)

        def on_resp(resp):
            try:
                u = resp.url.lower()
                if any(k in u for k in ["listing", "glue", "/v2/"]) and resp.status == 200:
                    body = resp.text()
                    if body and len(body) > 500:
                        api_responses.append({"data": json.loads(body), "size": len(body)})
            except Exception:
                pass

        page.on("response", on_resp)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        title = page.title()
        logger.info(f"  Título: {title}")

        if "cloudflare" in title.lower():
            page.wait_for_timeout(12000)
            if "cloudflare" in page.title().lower():
                logger.error("  ❌ Cloudflare")
                browser.close()
                return []

        page.wait_for_timeout(8000)
        for _ in range(8):
            page.evaluate("window.scrollBy(0, window.innerHeight)")
            page.wait_for_timeout(2000)
        page.wait_for_timeout(3000)

        coberturas = []
        if api_responses:
            api_responses.sort(key=lambda x: x["size"], reverse=True)
            for resp in api_responses:
                found = buscar_listings(resp["data"])
                if found:
                    coberturas = found
                    break

        browser.close()

    logger.info(f"🌐 Playwright: {len(coberturas)} coberturas")
    return coberturas


def buscar_listings(data, depth=0):
    if depth > 6:
        return []
    if isinstance(data, dict):
        for key, value in data.items():
            if key.lower() in ("listings", "results", "searchresults", "items", "search", "result"):
                if isinstance(value, list) and value:
                    parsed = [extrair_campos(i.get("listing", i), i.get("medias"), i.get("link"))
                              for i in value if isinstance(i, dict)]
                    parsed = [c for c in parsed if c and c.get("preco")]
                    if parsed:
                        return parsed
                elif isinstance(value, dict):
                    f = buscar_listings(value, depth + 1)
                    if f:
                        return f
            elif isinstance(value, (dict, list)):
                f = buscar_listings(value, depth + 1)
                if f:
                    return f
    return []


# ============================================================
# PUSHOVER
# ============================================================
def enviar_pushover(coberturas):
    token = os.environ.get("PUSHOVER_API_TOKEN")
    user = os.environ.get("PUSHOVER_USER_KEY")
    if not token or not user:
        logger.warning("⚠️ Pushover não configurado (defina PUSHOVER_API_TOKEN e PUSHOVER_USER_KEY nos GitHub Secrets)")
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

    for i, m in enumerate(mensagens):
        try:
            r = req.post(PUSHOVER_URL, data={
                "token": token, "user": user,
                "message": m, "title": "Coberturas Lagoa",
                "priority": 0, "html": 1,
            }, timeout=10)
            logger.info(f"  Pushover {i+1}/{len(mensagens)}: {r.status_code}")
            time.sleep(1)
        except Exception as e:
            logger.error(f"  Pushover: {e}")
    return True


# ============================================================
# SALVAR
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

    with open(COBERTURAS_FILE, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)

    hist = HISTORICO_DIR / f"{agora.strftime('%Y-%m-%d')}.json"
    with open(hist, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)

    logger.info(f"💾 Salvo em {COBERTURAS_FILE}")


# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("INÍCIO DA COLETA")
    logger.info("=" * 50)

    coberturas = coletar_via_api()

    if not coberturas:
        logger.info("API falhou. Tentando Playwright...")
        coberturas = coletar_via_playwright()

    if coberturas:
        salvar(coberturas)
        enviar_pushover(coberturas)
        logger.info(f"✅ {len(coberturas)} coberturas")
    else:
        salvar([])
        logger.error("❌ Nenhuma cobertura encontrada")

    logger.info("FIM")
