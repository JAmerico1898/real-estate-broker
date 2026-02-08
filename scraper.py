"""
Scraper de Coberturas - Lagoa, Rio de Janeiro
==============================================
Estratégia:
  1. Chama a API glue-api.zapimoveis.com.br diretamente (sem browser)
  2. Se falhar, usa Playwright como fallback

Roda no GitHub Actions. Salva em dados/coberturas.json.
"""

import json
import os
import re
import time
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

import requests as req

# ============================================================
# CONFIG
# ============================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

FUSO = ZoneInfo("America/Sao_Paulo")
PUSHOVER_URL = "https://api.pushover.net/1/messages.json"

DADOS_DIR = Path("dados")
HISTORICO_DIR = DADOS_DIR / "historico"
COBERTURAS_FILE = DADOS_DIR / "coberturas.json"

# API interna do Zap Imóveis (descoberta via interceptação de rede)
GLUE_API_URL = "https://glue-api.zapimoveis.com.br/v2/listings"

# Parâmetros para coberturas à venda na Lagoa, Rio de Janeiro
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
    "size": "100",
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

GLUE_API_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
    "Referer": "https://www.zapimoveis.com.br/",
    "Origin": "https://www.zapimoveis.com.br",
    "x-domain": "www.zapimoveis.com.br",
}


# ============================================================
# COLETA VIA API DIRETA
# ============================================================
def coletar_via_api():
    """Chama a API glue-api diretamente."""
    logger.info("📡 Tentando API direta: glue-api.zapimoveis.com.br...")

    todas_coberturas = []
    page = 1
    max_pages = 10

    while page <= max_pages:
        params = {**GLUE_API_PARAMS, "page": str(page), "from": str((page - 1) * 100)}

        try:
            response = req.get(
                GLUE_API_URL,
                params=params,
                headers=GLUE_API_HEADERS,
                timeout=30,
            )

            logger.info(f"  Página {page}: status {response.status_code} ({len(response.content):,}B)")

            if response.status_code != 200:
                logger.warning(f"  Resposta não-200: {response.status_code}")
                break

            data = response.json()

            # Extrai listings
            search = data.get("search", data)
            result = search.get("result", search)
            listings_data = result.get("listings", [])
            total_count = result.get("totalCount", 0)

            if not listings_data:
                listings_data = data.get("listings", data.get("results", []))

            if not listings_data:
                logger.info(f"  Nenhum listing na página {page}")
                break

            logger.info(f"  {len(listings_data)} listings (total no site: {total_count})")

            for item in listings_data:
                listing = item.get("listing", item)
                medias = item.get("medias", [])
                link_data = item.get("link", {})

                c = extrair_campos(listing, medias, link_data)
                if c and c.get("preco"):
                    todas_coberturas.append(c)

            if len(listings_data) < 100 or len(todas_coberturas) >= total_count:
                break

            page += 1
            time.sleep(1)

        except req.exceptions.Timeout:
            logger.error("  Timeout na API")
            break
        except req.exceptions.ConnectionError:
            logger.error("  Erro de conexão")
            break
        except Exception as e:
            logger.error(f"  Erro: {type(e).__name__}: {e}")
            break

    logger.info(f"📡 API direta: {len(todas_coberturas)} coberturas")
    return todas_coberturas


def extrair_campos(listing, medias=None, link_data=None):
    """Extrai campos de um listing da API glue."""
    if not isinstance(listing, dict):
        return None

    c = {"endereco": "", "preco": "", "area_m2": "", "quartos": "", "vagas": "", "link": "", "foto": ""}

    # Endereço
    addr = listing.get("address", {})
    if isinstance(addr, dict):
        parts = [str(addr[k]) for k in ["street", "streetNumber", "neighborhood", "city"] if addr.get(k)]
        c["endereco"] = ", ".join(parts)

    # Preço
    pricing = listing.get("pricingInfos", [])
    if isinstance(pricing, list):
        for p in pricing:
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
    elif listing.get("totalAreas"):
        t = listing["totalAreas"]
        if isinstance(t, list) and t:
            c["area_m2"] = f"{t[0]} m²"

    # Quartos
    beds = listing.get("bedrooms")
    if isinstance(beds, list) and beds:
        c["quartos"] = str(beds[0])

    # Vagas
    parking = listing.get("parkingSpaces")
    if isinstance(parking, list) and parking:
        c["vagas"] = str(parking[0])

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
    if isinstance(medias, list) and medias:
        for media in medias:
            if isinstance(media, dict):
                url = media.get("url", "")
                if url:
                    if not url.startswith("http"):
                        url = f"https://resizedimgs.zapimoveis.com.br/fit-in/800x600/{url}"
                    c["foto"] = url
                    break

    return c


# ============================================================
# FALLBACK: PLAYWRIGHT
# ============================================================
def coletar_via_playwright():
    """Fallback: usa Playwright se a API direta falhar."""
    logger.info("🌐 Fallback: Playwright...")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("Playwright não instalado")
        return []

    try:
        from playwright_stealth import stealth_sync
        has_stealth = True
    except ImportError:
        has_stealth = False

    coberturas = []
    api_responses = []

    url_zap = (
        "https://www.zapimoveis.com.br/venda/cobertura/"
        "rj+rio-de-janeiro+zona-sul+lagoa/"
        "?transacao=venda"
        "&onde=%2CRio+de+Janeiro%2CRio+de+Janeiro%2CZona+Sul%2CLagoa"
        "%2C%2C%2Cneighborhood%2CBR%3ERio+de+Janeiro%3ENULL%3ERio+de+Janeiro"
        "%3EZona+Sul%3ELagoa%2C-22.96182%2C-43.203077%2C"
        "&tipos=cobertura_residencial"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="pt-BR",
        )
        page = context.new_page()
        if has_stealth:
            stealth_sync(page)

        def on_response(response):
            try:
                url = response.url.lower()
                if any(kw in url for kw in ["listing", "glue", "/v2/"]) and response.status == 200:
                    body = response.text()
                    if body and len(body) > 200:
                        api_responses.append({"data": json.loads(body), "size": len(body)})
            except Exception:
                pass

        page.on("response", on_response)
        page.goto(url_zap, wait_until="domcontentloaded", timeout=60000)

        if "cloudflare" in page.title().lower():
            page.wait_for_timeout(10000)
            if "cloudflare" in page.title().lower():
                logger.error("  ❌ Cloudflare bloqueou")
                browser.close()
                return []

        page.wait_for_timeout(8000)
        for _ in range(8):
            page.evaluate("window.scrollBy(0, window.innerHeight)")
            page.wait_for_timeout(2000)
        page.wait_for_timeout(3000)

        if api_responses:
            api_responses.sort(key=lambda x: x["size"], reverse=True)
            for resp in api_responses:
                found = buscar_listings_recursivo(resp["data"])
                if found:
                    coberturas = found
                    break

        browser.close()

    logger.info(f"🌐 Playwright: {len(coberturas)} coberturas")
    return coberturas


def buscar_listings_recursivo(data, depth=0):
    if depth > 6:
        return []
    chaves = ["listings", "results", "searchResults", "items", "data", "search"]
    if isinstance(data, dict):
        for key, value in data.items():
            if any(c in key.lower() for c in chaves):
                if isinstance(value, list) and value:
                    parsed = [extrair_campos(item.get("listing", item), item.get("medias"), item.get("link"))
                              for item in value if isinstance(item, dict)]
                    parsed = [c for c in parsed if c and c.get("preco")]
                    if parsed:
                        return parsed
                elif isinstance(value, dict):
                    found = buscar_listings_recursivo(value, depth + 1)
                    if found:
                        return found
            elif isinstance(value, (dict, list)):
                found = buscar_listings_recursivo(value, depth + 1)
                if found:
                    return found
    return []


# ============================================================
# PUSHOVER
# ============================================================
def enviar_pushover(coberturas):
    token = os.environ.get("PUSHOVER_API_TOKEN")
    user = os.environ.get("PUSHOVER_USER_KEY")
    if not token or not user:
        logger.warning("⚠️ Pushover não configurado")
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
            r = req.post(PUSHOVER_URL, data={
                "token": token, "user": user,
                "message": m, "title": "Coberturas Lagoa",
                "priority": 0, "html": 1,
            }, timeout=10)
            logger.info(f"  Pushover {i+1}/{len(mensagens)}: {r.status_code}")
            if r.status_code != 200:
                ok = False
            time.sleep(1)
        except Exception as e:
            logger.error(f"  Pushover: {e}")
            ok = False
    return ok


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

    # Tenta API direta primeiro (rápido, sem browser)
    coberturas = coletar_via_api()

    # Fallback: Playwright
    if not coberturas:
        logger.info("API direta falhou. Tentando Playwright...")
        coberturas = coletar_via_playwright()

    if coberturas:
        salvar(coberturas)
        enviar_pushover(coberturas)
        logger.info(f"✅ {len(coberturas)} coberturas coletadas e salvas")
    else:
        salvar([])
        logger.error("❌ Nenhuma cobertura encontrada")

    logger.info("FIM")
