import streamlit as st
import pandas as pd
import json
import io
import re
import requests

st.set_page_config(page_title="Actualizador Stock Etiquetas", page_icon="🍷", layout="wide")

st.title("🍷 Actualizar stock de vino en previsión de etiquetas")
st.markdown("La IA relaciona las ETQ/CONTRA (col. B etiquetas) con los vinos (col. C stock) y copia el stock disponible (col. F) a la columna D del excel de etiquetas.")

api_key = st.text_input("🔑 API Key de Anthropic (sk-ant-...)", type="password", placeholder="sk-ant-api03-...")

st.markdown("---")
tab1, tab2 = st.tabs(["🏢 Empresa 1 (Hoja 1)", "🏡 Finca (Hoja 2)"])

def parse_etq(file, sheet_index):
    raw = pd.read_excel(file, header=None, sheet_name=sheet_index)
    rows = []
    for i in range(1, len(raw)):
        name = str(raw.iloc[i, 1] if len(raw.columns) > 1 else '').strip()
        if not name or name == 'nan':
            continue
        stock_etq = raw.iloc[i, 2] if len(raw.columns) > 2 else ''
        rows.append({"row_idx": i + 1, "name": name, "stock_etq": stock_etq})
    return rows

def parse_vino(file):
    raw = pd.read_excel(file, header=None)
    rows = []
    for i in range(len(raw)):
        name = str(raw.iloc[i, 2] if len(raw.columns) > 2 else '').strip()
        if not name or name in ('Nombre', 'nan'):
            continue
        raw_stock = raw.iloc[i, 5] if len(raw.columns) > 5 else None
        if raw_stock is None or str(raw_stock).strip() in ('', '-', 'nan'):
            continue
        try:
            stock = float(raw_stock)
        except:
            continue
        rows.append({"name": name, "stock": stock})
    return rows

def call_claude(api_key, prompt):
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        },
        json={
            "model": "claude-sonnet-4-5",
            "max_tokens": 2000,
            "messages": [{"role": "user", "content": prompt}]
        },
        timeout=60
    )
    if not resp.ok:
        raise Exception(f"API {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    return data["content"][0]["text"].strip()

def run_matching(tab_key, file_etq, sheet_index, sheet_label, file_vino, api_key):
    if not file_etq or not file_vino:
        st.info("Carga ambos excels para continuar.")
        return

    file_etq.seek(0)
    file_vino.seek(0)

    try:
        etq_rows = parse_etq(file_etq, sheet_index)
    except Exception as e:
        st.error(f"Error leyendo hoja '{sheet_label}': {e}")
        return

    vino_rows = parse_vino(file_vino)
    st.success(f"✅ Etiquetas ({sheet_label}): **{len(etq_rows)}** artículos · Vino: **{len(vino_rows)}** vinos con stock")

    if st.button("🤖 Buscar coincidencias con IA", type="primary", disabled=not api_key, key=f"btn_match_{tab_key}"):
        vino_lines = [f"{i}|{v['name']}" for i, v in enumerate(vino_rows)]
        etq_names = [r["name"] for r in etq_rows]
        all_results = {}
        BATCH = 35
        progress = st.progress(0, text="Iniciando análisis...")
        total_batches = (len(etq_names) + BATCH - 1) // BATCH
        error_found = False

        for b, i in enumerate(range(0, len(etq_names), BATCH)):
            batch = etq_names[i:i+BATCH]
            pct = int((b / total_batches) * 90)
            progress.progress(pct, text=f"Lote {b+1} de {total_batches} — {i+1}–{min(i+BATCH, len(etq_names))} de {len(etq_names)}")

            prompt = f"""Eres experto en vinos de Ego Bodegas. Relaciona cada etiqueta con el vino del inventario.

REGLAS:
- Ignora prefijos ETQ/CONTRA al comparar
- "EL GORU" y "GORU" son el mismo producto
- Los años (2022/2023/2024/2025) son importantes: intenta que coincidan
- "LCBO" = Canadá cilíndrica, "CA" = Canadá, "EU/Europa" = Europa
- Para etiquetas sin año (S/A), asigna el más reciente disponible
- Si no hay coincidencia razonable pon null
- conf: "high"=clara, "mid"=probable, "low"=dudosa

Los vinos tienen formato "INDICE|nombre". Debes devolver el INDICE numérico, no el nombre.

Responde SOLO con JSON array sin texto ni backticks:
[{{"etq":"nombre etiqueta","idx":0,"conf":"high/mid/low"}}]

Si no hay coincidencia: {{"etq":"nombre","idx":null,"conf":"low"}}

ETIQUETAS:
{chr(10).join(batch)}

VINOS CON STOCK (INDICE|nombre):
{chr(10).join(vino_lines)}"""

            try:
                text = call_claude(api_key, prompt)
                text = re.sub(r'```json|```', '', text).strip()
                m = re.search(r'\[[\s\S]*\]', text)
                parsed = json.loads(m.group(0) if m else text)
                for p in parsed:
                    all_results[p["etq"]] = p
            except Exception as e:
                st.error(f"❌ Error en lote {b+1}: {e}")
                error_found = True
                break

        if not error_found:
            progress.progress(95, text="Cruzando datos...")
            matches = []
            for etq in etq_rows:
                m = all_results.get(etq["name"], {})
                idx = m.get("idx")
                vino_entry = vino_rows[idx] if idx is not None and 0 <= idx < len(vino_rows) else None
                matches.append({
                    **etq,
                    "matched_vino": vino_entry["name"] if vino_entry else None,
                    "matched_stock": vino_entry["stock"] if vino_entry else None,
                    "conf": m.get("conf", "low") if vino_entry else "skip"
                })
            progress.progress(100, text="¡Completado!")
            st.session_state[f"matches_{tab_key}"] = matches
            st.session_state[f"vino_rows_{tab_key}"] = vino_rows

    if f"matches_{tab_key}" in st.session_state:
        matches = st.session_state[f"matches_{tab_key}"]
        vino_rows_s = st.session_state[f"vino_rows_{tab_key}"]

        high = sum(1 for m in matches if m["conf"] == "high")
        mid  = sum(1 for m in matches if m["conf"] == "mid")
        low  = sum(1 for m in matches if m["conf"] in ("low", "skip"))

        st.markdown("---")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total artículos", len(matches))
        c2.metric("✅ Coincidencia alta", high)
        c3.metric("⚠️ Media", mid)
        c4.metric("❌ Sin coincidencia", low)

        st.markdown("### Revisa y corrige las coincidencias")
        conf_filter = st.selectbox("Filtrar", ["Todas", "Alta", "Media", "Revisar", "Manual"], key=f"filter_{tab_key}")
        search = st.text_input("Buscar etiqueta...", key=f"search_{tab_key}")

        vino_options = ["— sin asignar —"] + [v["name"] for v in vino_rows_s]
        CONF_LABELS = {"high": "✅ Alta", "mid": "⚠️ Media", "low": "❌ Revisar", "skip": "— Sin asignar", "manual": "✏️ Manual"}
        CONF_FILTER_MAP = {"Todas": None, "Alta": "high", "Media": "mid", "Revisar": "low_skip", "Manual": "manual"}
        cf = CONF_FILTER_MAP[conf_filter]

        filtered = []
        for i, m in enumerate(matches):
            if search and search.lower() not in m["name"].lower():
                continue
            if cf == "low_skip" and m["conf"] not in ("low", "skip"):
                continue
            elif cf and cf != "low_skip" and m["conf"] != cf:
                continue
            filtered.append((i, m))

        for i, m in filtered:
            cols = st.columns([3, 1, 1, 3, 1])
            cols[0].markdown(f"**{m['name']}**")
            cols[1].markdown(f"`{m.get('stock_etq', '—')}`")
            cols[2].markdown(CONF_LABELS.get(m["conf"], m["conf"]))
            current_vino = m.get("matched_vino") or "— sin asignar —"
            idx = vino_options.index(current_vino) if current_vino in vino_options else 0
            selected = cols[3].selectbox("", vino_options, index=idx, key=f"sel_{tab_key}_{i}", label_visibility="collapsed")
            if selected != current_vino:
                matches[i]["matched_vino"] = selected if selected != "— sin asignar —" else None
                matches[i]["conf"] = "manual" if selected != "— sin asignar —" else "skip"
                vino_entry = next((v for v in vino_rows_s if v["name"] == selected), None)
                matches[i]["matched_stock"] = vino_entry["stock"] if vino_entry else None
                st.session_state[f"matches_{tab_key}"] = matches
            sv = m.get("matched_stock")
            cols[4].markdown(f"`{int(sv) if sv is not None else '—'}`")

        st.markdown("---")
        if st.button("⬇️ Generar Excel actualizado", type="primary", key=f"btn_download_{tab_key}"):
            file_etq.seek(0)
            all_sheets = pd.read_excel(file_etq, header=None, sheet_name=None)
            sheet_names = list(all_sheets.keys())
            raw = all_sheets[sheet_names[sheet_index]]
            updated = 0
            for m in matches:
                if m.get("matched_stock") is not None:
                    raw.iloc[m["row_idx"] - 1, 3] = m["matched_stock"]
                    updated += 1
            all_sheets[sheet_names[sheet_index]] = raw
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine="openpyxl") as writer:
                for sname, sdf in all_sheets.items():
                    sdf.to_excel(writer, index=False, header=False, sheet_name=sname)
            output.seek(0)
            st.download_button(
                label=f"📥 Descargar ({updated} celdas actualizadas en col. D — {sheet_label})",
                data=output,
                file_name=f"previsión_etiquetas_2026_ACTUALIZADO_{tab_key}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"dl_{tab_key}"
            )

with tab1:
    st.subheader("Empresa 1 — Hoja 1 del excel de etiquetas")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**📋 Excel de etiquetas**")
        file_etq1 = st.file_uploader("Col. B = nombre · Col. D = stock vino (a actualizar)", type=["xlsx","xls"], key="etq1")
    with col2:
        st.markdown("**🍾 Excel de stock de vino (Empresa 1)**")
        file_vino1 = st.file_uploader("Col. C = nombre vino · Col. F = stock disponible", type=["xlsx","xls"], key="vino1")
    if file_etq1 and file_vino1 and api_key:
        run_matching("emp1", file_etq1, 0, "Hoja 1", file_vino1, api_key)
    elif not api_key:
        st.warning("Introduce tu API key arriba para continuar.")

with tab2:
    st.subheader("Finca — Hoja 2 del excel de etiquetas")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**📋 Excel de etiquetas**")
        file_etq2 = st.file_uploader("Col. B = nombre · Col. D = stock vino (a actualizar)", type=["xlsx","xls"], key="etq2")
    with col2:
        st.markdown("**🍾 Excel de stock de vino (Finca)**")
        file_vino2 = st.file_uploader("Col. C = nombre vino · Col. F = stock disponible", type=["xlsx","xls"], key="vino2")
    if file_etq2 and file_vino2 and api_key:
        run_matching("finca", file_etq2, 1, "FINCA", file_vino2, api_key)
    elif not api_key:
        st.warning("Introduce tu API key arriba para continuar.")
