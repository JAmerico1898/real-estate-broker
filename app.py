"""
Monitor de Coberturas - Lagoa, Rio de Janeiro
=============================================
Estratégia de coleta em 3 camadas:
1. Intercepta requisições XHR/fetch para capturar JSON da API interna
2. Tenta extrair do __NEXT_DATA__ no HTML
3. Parseia HTML diretamente via seletores + regex
Se tudo falhar, salva screenshot + HTML + APIs para diagnóstico.
"""

import streamlit as st
import pandas as pd
import requests
import json
import re
import time
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ============================================================
# CONFIGURAÇÃO
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%d/%m/%Y %H:%M:%S",
)
logger = logging.getLogger(__name__)

FUSO_BRASILIA = ZoneInfo("America/Sao_Paulo")

URL_ZAP = (
    "https://www.zapimoveis.com.br/venda/cobertura/"
    "rj+rio-de-janeiro+zona-sul+lagoa/"
    "?transacao=venda"
    "&onde=%2CRio+de+Janeiro%2CRio+de+Janeiro%2CZona+Sul%2CLagoa"
    "%2C%2C%2Cneighborhood%2CBR%3ERio+de+Janeiro%3ENULL%3ERio+de+Janeiro"
    "%3EZona+Sul%3ELagoa%2C-22.96182%2C-43.203077%2C"
    "&tipos=cobertura_residencial"
)

PUSHOVER_URL = "https://api.pushover.net/1/messages.json"
DEBUG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zap_debug")


# ============================================================
# CREDENCIAIS
# ============================================================
def obter_credenciais():
    try:
        return st.secrets["PUSHOVER_API_TOKEN"], st.secrets["PUSHOVER_USER_KEY"]
    except Exception as e:
        logger.error(f"Erro ao obter credenciais: {e}")
        return None, None


# ============================================================
# COLETA PRINCIPAL
# ============================================================
def coletar_coberturas():
    """Coleta coberturas usando Playwright com interceptação de rede."""
    coberturas = []
    api_responses = []

    os.makedirs(DEBUG_DIR, exist_ok=True)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
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
                    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
                },
            )

            page = context.new_page()

            # --- CAMADA 1: Interceptar respostas de rede ---
            def on_response(response):
                try:
                    url = response.url.lower()
                    keywords = [
                        "listing", "search", "result", "property",
                        "imovel", "imoveis", "glue", "bff", "api",
                        "graphql", "/v2/", "/v3/", "/v1/",
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
                                logger.info(
                                    f"  📡 API: {response.url[:120]} "
                                    f"({len(body):,} bytes)"
                                )
                        except (json.JSONDecodeError, Exception):
                            pass
                except Exception:
                    pass

            page.on("response", on_response)

            logger.info("Acessando Zap Imóveis...")
            page.goto(URL_ZAP, wait_until="domcontentloaded", timeout=60000)

            # Espera generosa para carregamento completo + APIs
            logger.info("Aguardando carregamento...")
            page.wait_for_timeout(10000)

            # Scroll para disparar lazy loading
            logger.info("Scroll para carregar mais dados...")
            for _ in range(8):
                page.evaluate("window.scrollBy(0, window.innerHeight)")
                page.wait_for_timeout(2500)

            page.wait_for_timeout(3000)
            logger.info(f"APIs interceptadas: {len(api_responses)}")

            # Processa respostas interceptadas
            if api_responses:
                coberturas = _processar_api_responses(api_responses)

            # --- CAMADA 2: __NEXT_DATA__ ---
            if not coberturas:
                logger.info("Tentando __NEXT_DATA__...")
                coberturas = _extrair_next_data(page)

            # --- CAMADA 3: HTML direto ---
            if not coberturas:
                logger.info("Tentando extração HTML...")
                coberturas = _extrair_via_html(page)

            # --- DIAGNÓSTICO ---
            if not coberturas:
                logger.warning("Nenhuma cobertura. Salvando diagnóstico...")
                _salvar_diagnostico(page, api_responses)

            browser.close()

    except PlaywrightTimeout:
        logger.error("Timeout ao acessar o Zap Imóveis.")
    except Exception as e:
        logger.error(f"Erro inesperado: {type(e).__name__}: {e}")

    logger.info(f"Total coletadas: {len(coberturas)}")
    return coberturas


# ============================================================
# PROCESSAMENTO DE APIs INTERCEPTADAS
# ============================================================
def _processar_api_responses(responses):
    """Processa JSONs interceptados buscando listings."""
    responses.sort(key=lambda x: x["size"], reverse=True)

    for resp in responses:
        # Salva para debug
        try:
            safe = re.sub(r'[^\w]', '_', resp["url"].split('?')[0].split('/')[-1][:40])
            with open(f"{DEBUG_DIR}/api_{safe}.json", "w", encoding="utf-8") as f:
                json.dump(resp["data"], f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        encontrados = _buscar_listings(resp["data"])
        if encontrados:
            logger.info(f"✅ {len(encontrados)} coberturas em: {resp['url'][:80]}")
            return encontrados

    return []


def _buscar_listings(data, depth=0):
    """Busca recursivamente por arrays de listings no JSON."""
    if depth > 6:
        return []

    chaves = [
        "listings", "listing", "results", "searchResults",
        "items", "hits", "data", "properties", "imoveis", "search",
    ]

    if isinstance(data, dict):
        for key, value in data.items():
            key_l = key.lower()
            if any(c in key_l for c in chaves):
                if isinstance(value, list) and len(value) > 0:
                    parsed = _parsear_lista(value)
                    if parsed:
                        return parsed
                elif isinstance(value, dict):
                    found = _buscar_listings(value, depth + 1)
                    if found:
                        return found
            elif isinstance(value, (dict, list)):
                found = _buscar_listings(value, depth + 1)
                if found:
                    return found

    elif isinstance(data, list) and len(data) > 0:
        return _parsear_lista(data)

    return []


def _parsear_lista(items):
    """Tenta parsear lista como imóveis."""
    coberturas = []
    for item in items:
        if not isinstance(item, dict):
            continue
        listing = item.get("listing", item)
        if not isinstance(listing, dict):
            continue
        c = _extrair_campos(listing)
        if c:
            coberturas.append(c)

    return coberturas if any(c.get("preco") for c in coberturas) else []


def _extrair_campos(obj):
    """Extrai campos de um objeto JSON de imóvel."""
    c = {"endereco": "", "preco": "", "area_m2": "", "quartos": "", "vagas": "", "link": "", "foto": ""}

    # Endereço
    addr = obj.get("address", {})
    if isinstance(addr, dict):
        parts = [str(addr.get(k, "")) for k in ["street", "streetNumber", "neighborhood", "city"] if addr.get(k)]
        c["endereco"] = ", ".join(parts)
    elif isinstance(addr, str):
        c["endereco"] = addr
    if not c["endereco"]:
        for k in ["endereco", "location", "title"]:
            if obj.get(k) and isinstance(obj[k], str):
                c["endereco"] = obj[k]
                break

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

    # Área
    for k in ["usableAreas", "totalAreas", "area"]:
        v = obj.get(k)
        if v:
            c["area_m2"] = f"{v[0] if isinstance(v, list) else v} m²"
            break

    # Quartos
    for k in ["bedrooms", "quartos"]:
        v = obj.get(k)
        if v:
            c["quartos"] = str(v[0] if isinstance(v, list) else v)
            break

    # Vagas
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
        lid = obj.get("id") or obj.get("externalId") or obj.get("listingId")
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
    if not c["foto"]:
        for k in ["image", "photo", "thumbnail", "coverImage"]:
            v = obj.get(k)
            if v:
                if isinstance(v, list) and v:
                    v = v[0]
                if isinstance(v, dict):
                    v = v.get("url", "")
                if v:
                    c["foto"] = str(v)
                    break

    return c


# ============================================================
# CAMADA 2: __NEXT_DATA__
# ============================================================
def _extrair_next_data(page):
    try:
        text = page.evaluate("""
            () => {
                const el = document.querySelector('script#__NEXT_DATA__');
                return el ? el.textContent : null;
            }
        """)
        if not text:
            return []

        logger.info(f"__NEXT_DATA__: {len(text):,} bytes")
        with open(f"{DEBUG_DIR}/next_data.json", "w", encoding="utf-8") as f:
            f.write(text)

        data = json.loads(text)
        return _buscar_listings(data)
    except Exception as e:
        logger.warning(f"Erro __NEXT_DATA__: {e}")
        return []


# ============================================================
# CAMADA 3: HTML DIRETO
# ============================================================
def _extrair_via_html(page):
    coberturas = []
    try:
        html = page.content()
        with open(f"{DEBUG_DIR}/pagina.html", "w", encoding="utf-8") as f:
            f.write(html)

        # Busca JSONs embutidos nos scripts da página
        scripts = page.evaluate("""
            () => {
                return Array.from(document.querySelectorAll('script'))
                    .map(s => s.textContent || '')
                    .filter(t => t.length > 300 &&
                        (t.includes('listing') || t.includes('price') ||
                         t.includes('address') || t.includes('pricingInfos')))
                    .map(t => t.substring(0, 100000));
            }
        """)

        logger.info(f"Scripts com dados potenciais: {len(scripts)}")

        for script_text in scripts:
            # Busca JSONs completos dentro do script
            for pattern in [
                r'window\.__\w+__\s*=\s*(\{.+\})\s*;',
                r'JSON\.parse\(["\'](.+?)["\']\)',
                r'data["\']?\s*:\s*(\{.+\})',
            ]:
                matches = re.findall(pattern, script_text, re.DOTALL)
                for match in matches[:3]:
                    try:
                        # Desescapa se necessário
                        cleaned = match.replace('\\"', '"').replace("\\'", "'")
                        data = json.loads(cleaned)
                        found = _buscar_listings(data)
                        if found:
                            logger.info(f"✅ {len(found)} coberturas via script embutido")
                            return found
                    except json.JSONDecodeError:
                        continue

        # Fallback: busca links de imóveis no DOM
        links = page.evaluate("""
            () => {
                const selectors = [
                    'a[href*="/imovel/"]',
                    'a[href*="cobertura"]',
                    '[data-testid*="listing"] a',
                    '.listing-wrapper a',
                    'article a[href]',
                ];
                const found = new Set();
                const results = [];
                for (const sel of selectors) {
                    document.querySelectorAll(sel).forEach(a => {
                        if (!found.has(a.href) && a.href.includes('zapimoveis')) {
                            found.add(a.href);
                            const card = a.closest('div[class*="card"], article, [data-testid]') || a;
                            results.push({
                                href: a.href,
                                text: card.innerText.substring(0, 1000)
                            });
                        }
                    });
                }
                return results;
            }
        """)

        logger.info(f"Links de imóveis no DOM: {len(links)}")

        for link in links:
            text = link.get("text", "")
            href = link.get("href", "")
            if not text or len(text) < 10:
                continue

            preco_m = re.search(r'R\$\s*[\d.,]+', text)
            area_m = re.search(r'(\d+)\s*m²', text)
            quartos_m = re.search(r'(\d+)\s*(?:quarto|dorm|suíte)', text, re.I)
            vagas_m = re.search(r'(\d+)\s*(?:vaga|garag)', text, re.I)

            if preco_m:
                coberturas.append({
                    "endereco": text.split("\n")[0][:120].strip(),
                    "preco": preco_m.group(0),
                    "area_m2": f"{area_m.group(1)} m²" if area_m else "",
                    "quartos": quartos_m.group(1) if quartos_m else "",
                    "vagas": vagas_m.group(1) if vagas_m else "",
                    "link": href,
                    "foto": "",
                })

    except Exception as e:
        logger.error(f"Erro extração HTML: {e}")

    return coberturas


# ============================================================
# DIAGNÓSTICO
# ============================================================
def _salvar_diagnostico(page, api_responses):
    os.makedirs(DEBUG_DIR, exist_ok=True)
    try:
        page.screenshot(path=f"{DEBUG_DIR}/screenshot.png", full_page=True)

        html = page.content()
        with open(f"{DEBUG_DIR}/pagina.html", "w", encoding="utf-8") as f:
            f.write(html)

        with open(f"{DEBUG_DIR}/urls_interceptadas.txt", "w") as f:
            for r in api_responses:
                f.write(f"{r['url']} ({r['size']:,} bytes)\n")
            if not api_responses:
                f.write("Nenhuma resposta interceptada.\n")

        info = page.evaluate("""
            () => ({
                title: document.title,
                url: window.location.href,
                scripts: document.querySelectorAll('script').length,
                links: document.querySelectorAll('a').length,
                has_next_data: !!document.querySelector('#__NEXT_DATA__'),
                body_text_length: (document.body?.innerText || '').length,
                body_preview: (document.body?.innerText || '').substring(0, 2000),
            })
        """)

        with open(f"{DEBUG_DIR}/info.json", "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)

        logger.info(f"Diagnóstico salvo. Título: {info.get('title')}")
        logger.info(f"URL final: {info.get('url')}")
        logger.info(f"Body text: {info.get('body_text_length')} chars")
        logger.info(f"Preview: {info.get('body_preview', '')[:300]}")

    except Exception as e:
        logger.error(f"Erro diagnóstico: {e}")


# ============================================================
# PUSHOVER
# ============================================================
def enviar_pushover(coberturas, token, user_key):
    if not coberturas or not token or not user_key:
        return False

    agora = datetime.now(FUSO_BRASILIA).strftime("%d/%m/%Y %H:%M")
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
                "token": token, "user": user_key,
                "message": m, "title": "Coberturas Lagoa - Novas Ofertas",
                "priority": 0, "html": 1,
            }, timeout=10)
            if r.status_code == 200:
                logger.info(f"Pushover {i+1}/{len(mensagens)} ✓")
            else:
                logger.error(f"Pushover erro {r.status_code}: {r.text}")
                ok = False
            if i < len(mensagens) - 1:
                time.sleep(1)
        except Exception as e:
            logger.error(f"Pushover erro: {e}")
            ok = False
    return ok


# ============================================================
# EXECUÇÃO
# ============================================================
def executar_tarefa():
    logger.info("=" * 50)
    logger.info("INÍCIO DA COLETA")
    logger.info("=" * 50)

    token, user_key = obter_credenciais()
    coberturas = coletar_coberturas()

    if coberturas:
        sucesso = enviar_pushover(coberturas, token, user_key)
        try:
            df = pd.DataFrame(coberturas)
            ts = datetime.now(FUSO_BRASILIA).strftime("%Y%m%d_%H%M")
            df.to_csv(f"coberturas_{ts}.csv", index=False, encoding="utf-8-sig")
        except Exception:
            pass
        status = "✅ Sucesso" if sucesso else "⚠️ Dados ok, push falhou"
    else:
        status = "❌ Nenhuma cobertura encontrada"

    logger.info(f"RESULTADO: {status}")
    return coberturas, status


def iniciar_agendador():
    sched = BackgroundScheduler(timezone=FUSO_BRASILIA)
    sched.add_job(executar_tarefa, "cron", hour=9, minute=0,
                  id="coleta_diaria", replace_existing=True)
    sched.start()
    logger.info("⏰ Agendador: diário às 9:00 BRT")
    return sched


# ============================================================
# INTERFACE STREAMLIT
# ============================================================
def main():
    st.set_page_config(page_title="Monitor Coberturas Lagoa", page_icon="🏠", layout="wide")

    st.title("🏠 Monitor de Coberturas - Lagoa, RJ")
    st.markdown("Monitora coberturas à venda na **Lagoa** via Zap Imóveis + notificações **Pushover**.")

    with st.sidebar:
        st.header("⚙️ Configurações")
        token, user_key = obter_credenciais()
        if token and user_key:
            st.success("🔑 Pushover: OK")
        else:
            st.error(
                "🔑 Pushover não configurado!\n\n"
                "`.streamlit/secrets.toml`:\n```toml\n"
                'PUSHOVER_API_TOKEN = "..."\nPUSHOVER_USER_KEY = "..."\n```'
            )

        st.divider()
        st.info("⏰ Execução diária às **9:00h** BRT")
        st.caption(f"Agora: {datetime.now(FUSO_BRASILIA).strftime('%d/%m/%Y %H:%M:%S')}")

        st.divider()
        st.header("🔧 Debug")
        if os.path.exists(DEBUG_DIR):
            files = sorted(os.listdir(DEBUG_DIR))
            if files:
                for f in files:
                    sz = os.path.getsize(os.path.join(DEBUG_DIR, f))
                    st.caption(f"📄 {f} ({sz:,}B)")
                if st.button("🗑️ Limpar"):
                    import shutil
                    shutil.rmtree(DEBUG_DIR)
                    st.rerun()

    if "init" not in st.session_state:
        st.session_state.init = True
        st.session_state.scheduler = iniciar_agendador()
        st.session_state.coberturas = []
        st.session_state.status = ""
        st.session_state.ultima = None

    st.divider()
    if st.button("🔍 Buscar Agora", type="primary"):
        with st.spinner("🔄 Acessando Zap Imóveis... (até 60s)"):
            coberturas, status = executar_tarefa()
            st.session_state.coberturas = coberturas
            st.session_state.status = status
            st.session_state.ultima = datetime.now(FUSO_BRASILIA)

    if st.session_state.get("ultima"):
        st.success(
            f"Última coleta: {st.session_state.ultima.strftime('%d/%m/%Y %H:%M')} "
            f"| {st.session_state.status}"
        )

    coberturas = st.session_state.get("coberturas", [])

    if coberturas:
        st.subheader(f"📋 {len(coberturas)} coberturas")
        for c in coberturas:
            with st.container():
                col1, col2 = st.columns([1, 3])
                with col1:
                    if c.get("foto"):
                        try:
                            st.image(c["foto"], width=200)
                        except Exception:
                            st.markdown("📷 *Erro*")
                    else:
                        st.markdown("📷 *Sem foto*")
                with col2:
                    st.markdown(f"### {c.get('preco', 'Preço N/A')}")
                    st.markdown(f"📍 **{c.get('endereco', 'N/A')}**")
                    cols = st.columns(3)
                    cols[0].metric("Área", c.get("area_m2", "-"))
                    cols[1].metric("Quartos", c.get("quartos", "-"))
                    cols[2].metric("Vagas", c.get("vagas", "-"))
                    if c.get("link"):
                        st.markdown(f"[🔗 Ver no Zap]({c['link']})")
                st.divider()

        with st.expander("📊 Tabela"):
            df = pd.DataFrame(coberturas)
            st.dataframe(df, use_container_width=True)
            st.download_button(
                "⬇️ CSV", df.to_csv(index=False, encoding="utf-8-sig"),
                f"coberturas_{datetime.now().strftime('%Y%m%d')}.csv", "text/csv"
            )

    elif st.session_state.get("ultima"):
        st.warning(
            "Nenhuma cobertura encontrada.\n\n"
            "**Possíveis causas:** proteção anti-bot, Cloudflare, ou mudança no site.\n"
            "Verifique o debug na barra lateral."
        )
        ss = f"{DEBUG_DIR}/screenshot.png"
        if os.path.exists(ss):
            with st.expander("📸 Screenshot (debug)"):
                st.image(ss, caption="O que o Playwright viu")
        ip = f"{DEBUG_DIR}/info.json"
        if os.path.exists(ip):
            with st.expander("ℹ️ Info da página"):
                with open(ip) as f:
                    st.json(json.load(f))


if __name__ == "__main__":
    main()