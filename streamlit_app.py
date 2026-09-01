"""
Home — punto de entrada de la web. Aquí es donde, más adelante, vivirán el
resto de herramientas junto al screener de funding rates (cada una como una
página nueva en pages/).
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="Funding Toolkit",
    page_icon="📡",
    layout="wide",
)

st.title("📡 Funding Toolkit")
st.caption("Tu propio panel de herramientas de trading — construido combinando lo mejor de ProFunding, Loris y John5Cripto.")

st.divider()

col1, col2, col3 = st.columns(3)

with col1:
    st.subheader("📊 Funding Rates")
    st.write(
        "Screener de arbitraje delta-neutral: ranking por spread, vista matriz "
        "completa, APR histórico real y Consistency Score."
    )
    st.page_link("pages/1_Funding_Rates.py", label="Abrir herramienta", icon="➡️")

with col2:
    st.subheader("🔜 Próxima herramienta")
    st.write("Aquí irá la siguiente pieza del toolkit — dímelo cuando quieras y la montamos igual que esta.")
    st.button("Próximamente", disabled=True, key="soon_1")

with col3:
    st.subheader("🔜 Próxima herramienta")
    st.write("Espacio libre para lo que decidas añadir después: otro screener, un backtester, alertas...")
    st.button("Próximamente", disabled=True, key="soon_2")

st.divider()
st.caption(
    "Corriendo en modo desarrollo. Cuando esté lista, esta app se despliega en Streamlit Cloud "
    "con una URL fija — sin instalar nada en tu ordenador."
)
