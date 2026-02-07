"""
Monitor de Coberturas - Lagoa, Rio de Janeiro
==============================================
Interface Streamlit que lê dados coletados pelo GitHub Actions.
Os dados ficam em dados/coberturas.json (commitado pelo scraper).
"""

import streamlit as st
import pandas as pd
import json
import os
import requests
from datetime import datetime
from pathlib import Path

# ============================================================
# CONFIG
# ============================================================
COBERTURAS_FILE = Path("dados/coberturas.json")
HISTORICO_DIR = Path("dados/historico")


def carregar_dados():
    """Carrega dados do arquivo JSON gerado pelo scraper."""
    if not COBERTURAS_FILE.exists():
        return None
    try:
        with open(COBERTURAS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def listar_historico():
    """Lista arquivos de histórico disponíveis."""
    if not HISTORICO_DIR.exists():
        return []
    arquivos = sorted(HISTORICO_DIR.glob("*.json"), reverse=True)
    return arquivos


def disparar_coleta():
    """Dispara o GitHub Actions workflow via API (requer token)."""
    token = st.secrets.get("GITHUB_TOKEN")
    repo = st.secrets.get("GITHUB_REPO")  # ex: "usuario/repo"

    if not token or not repo:
        return False, "GitHub Token ou Repo não configurado em secrets"

    try:
        r = requests.post(
            f"https://api.github.com/repos/{repo}/actions/workflows/scrape.yml/dispatches",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github.v3+json",
            },
            json={"ref": "main"},
            timeout=10,
        )
        if r.status_code == 204:
            return True, "Coleta disparada! Aguarde ~2 min e recarregue a página."
        else:
            return False, f"Erro {r.status_code}: {r.text}"
    except Exception as e:
        return False, f"Erro: {e}"


# ============================================================
# INTERFACE
# ============================================================
def main():
    st.set_page_config(
        page_title="Monitor Coberturas Lagoa",
        page_icon="🏠",
        layout="wide",
    )

    st.title("🏠 Monitor de Coberturas - Lagoa, RJ")
    st.markdown(
        "Coberturas à venda na **Lagoa, Rio de Janeiro** — "
        "dados coletados diariamente do Zap Imóveis."
    )

    # ---------------------------------------------------------
    # Sidebar
    # ---------------------------------------------------------
    with st.sidebar:
        st.header("⚙️ Configurações")
        st.info("⏰ Coleta automática diária às **9:00h** BRT via GitHub Actions")

        st.divider()

        # Botão para disparar coleta manual
        st.header("🔄 Coleta Manual")
        if st.button("🚀 Disparar Coleta Agora", type="primary", use_container_width=True):
            ok, msg = disparar_coleta()
            if ok:
                st.success(msg)
            else:
                st.error(msg)

        st.caption(
            "Requer `GITHUB_TOKEN` e `GITHUB_REPO` em secrets. "
            "O token precisa de permissão `actions:write`."
        )

        st.divider()

        # Histórico
        st.header("📅 Histórico")
        arquivos = listar_historico()
        if arquivos:
            datas = [a.stem for a in arquivos]
            sel = st.selectbox("Ver dados de:", datas, index=0)
            if sel:
                arq = HISTORICO_DIR / f"{sel}.json"
                with open(arq, "r", encoding="utf-8") as f:
                    hist = json.load(f)
                st.caption(
                    f"Coleta: {hist.get('ultima_coleta', 'N/A')} | "
                    f"Total: {hist.get('total', 0)}"
                )
        else:
            st.caption("Nenhum histórico disponível ainda.")

    # ---------------------------------------------------------
    # Dados principais
    # ---------------------------------------------------------
    dados = carregar_dados()

    if not dados:
        st.warning(
            "📭 Nenhum dado disponível ainda.\n\n"
            "A primeira coleta será feita automaticamente às 9:00h BRT, "
            "ou você pode disparar manualmente pela barra lateral."
        )
        return

    # Info da última coleta
    ultima = dados.get("ultima_coleta", "")
    total = dados.get("total", 0)
    coberturas = dados.get("coberturas", [])

    if ultima:
        try:
            dt = datetime.fromisoformat(ultima)
            ultima_fmt = dt.strftime("%d/%m/%Y %H:%M")
        except Exception:
            ultima_fmt = ultima
        st.success(f"📅 Última coleta: **{ultima_fmt}** | 🏠 **{total}** coberturas")

    if not coberturas:
        st.warning("A última coleta não encontrou coberturas. Isso pode indicar bloqueio anti-bot.")
        return

    # ---------------------------------------------------------
    # Exibe coberturas
    # ---------------------------------------------------------
    st.subheader(f"📋 {len(coberturas)} coberturas encontradas")

    # Filtro de preço
    precos_num = []
    for c in coberturas:
        try:
            p = c.get("preco", "").replace("R$", "").replace(".", "").replace(",", "").strip()
            if p:
                precos_num.append(int(p))
        except (ValueError, TypeError):
            pass

    if precos_num:
        preco_min = min(precos_num)
        preco_max = max(precos_num)
        if preco_min < preco_max:
            faixa = st.slider(
                "Faixa de preço (R$)",
                min_value=preco_min,
                max_value=preco_max,
                value=(preco_min, preco_max),
                step=100000,
                format="R$ %d",
            )
        else:
            faixa = (preco_min, preco_max)
    else:
        faixa = None

    # Cards
    exibidos = 0
    for c in coberturas:
        # Aplica filtro de preço
        if faixa:
            try:
                p = int(c.get("preco", "").replace("R$", "").replace(".", "").replace(",", "").strip())
                if p < faixa[0] or p > faixa[1]:
                    continue
            except (ValueError, TypeError):
                pass

        exibidos += 1
        with st.container():
            col1, col2 = st.columns([1, 3])

            with col1:
                if c.get("foto"):
                    try:
                        st.image(c["foto"], width=200)
                    except Exception:
                        st.markdown("📷 *Sem foto*")
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
                    st.markdown(f"[🔗 Ver no Zap Imóveis]({c['link']})")

            st.divider()

    if faixa and exibidos == 0:
        st.info("Nenhuma cobertura na faixa de preço selecionada.")

    # Tabela e download
    with st.expander("📊 Tabela completa"):
        df = pd.DataFrame(coberturas)
        st.dataframe(df, use_container_width=True)

        csv = df.to_csv(index=False, encoding="utf-8-sig")
        st.download_button(
            "⬇️ Baixar CSV",
            csv,
            f"coberturas_lagoa_{datetime.now().strftime('%Y%m%d')}.csv",
            "text/csv",
        )


if __name__ == "__main__":
    main()
