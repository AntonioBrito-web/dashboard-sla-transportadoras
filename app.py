from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from src.auth import admin_exists, authenticate
from src.config import CACHE_TTL_SECONDS
from src.data import (
    clean_dataframe,
    compute_kpis,
    detalhe_atraso,
    fetch_raw_dataframe,
    monthly_sla,
    motivos_atraso_chegada,
    ranking_transportadoras,
    regional_dist,
)
from src.db import get_justificativas, init_db, salvar_justificativa_anexo, salvar_justificativa_texto
from src.seed import seed_all
from src.theme import BRAND_RED, chart_colors


def milhar_str(valor) -> str:
    return f"{valor:,.0f}".replace(",", ".")


def formatar_data_br(valor) -> str:
    return valor.strftime("%d/%m/%Y") if pd.notna(valor) else ""


st.set_page_config(page_title="Dashboard SLA Transportadoras", layout="wide")


@st.cache_resource(show_spinner="Preparando o banco de dados...")
def preparar_banco() -> str | None:
    # Roda uma única vez por processo (não a cada rerun) — cria as tabelas e,
    # se for a primeira execução neste ambiente (ex.: um deploy novo), semeia
    # a conta admin e uma conta por transportadora automaticamente.
    init_db()
    seed_all()
    # Válvula de escape: defina o secret RESET_ADMIN = "true" no painel do
    # Streamlit Cloud (Settings -> Secrets) para forçar uma senha nova de
    # admin. A senha nova é exibida na própria tela de login (não só no
    # log, que é pouco confiável quanto a timing). Remova o secret depois
    # de copiar a senha, senão ela troca de novo a cada reinício.
    try:
        forcar_reset = str(st.secrets.get("RESET_ADMIN", "")).strip().lower() == "true"
    except Exception:
        forcar_reset = False
    if forcar_reset:
        from src.seed import reset_admin_password

        return reset_admin_password()
    return None


NOVA_SENHA_ADMIN = preparar_banco()

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
LOGO_PATH = ASSETS_DIR / "Logo-JT-Express-Red.png"
MASCOTE_PATH = ASSETS_DIR / "mao mao.png"

ANEXOS_DIR = Path(__file__).resolve().parent / "data" / "anexos"
ANEXOS_DIR.mkdir(parents=True, exist_ok=True)


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner="Carregando dados da planilha...")
def load_data() -> pd.DataFrame:
    raw = fetch_raw_dataframe()
    return clean_dataframe(raw)


def _detectar_tema() -> str:
    try:
        detectado = st.context.theme.type
    except Exception:
        detectado = "light"
    return detectado if detectado in ("light", "dark") else "light"


def get_theme_mode() -> str:
    # st.context.theme.type é documentado pelo próprio Streamlit como não
    # totalmente confiável logo após uma troca de tema, então o valor
    # detectado só define o padrão inicial do seletor abaixo — ele nasce
    # sincronizado com o tema real (claro/escuro/sistema), e o usuário pode
    # corrigir manualmente se a detecção errar em algum caso.
    opcoes = ["Claro", "Escuro"]
    indice_padrao = 1 if _detectar_tema() == "dark" else 0
    escolha = st.sidebar.radio(
        "Aparência dos gráficos", opcoes, index=indice_padrao, horizontal=True, key="tema_graficos"
    )
    st.sidebar.caption(
        "Ajusta só as cores dos gráficos. Para trocar o tema geral do app, "
        "use o menu ⋮ (canto superior direito) → Settings → Theme."
    )
    return "dark" if escolha == "Escuro" else "light"


def _inject_header_css(key: str) -> None:
    st.markdown(
        f"""<style>
        .st-key-{key} {{
            background-color: {BRAND_RED};
            padding: 1rem 1.5rem;
            border-radius: 8px;
            margin-bottom: 1rem;
        }}
        .st-key-{key} * {{
            color: #ffffff !important;
        }}
        </style>""",
        unsafe_allow_html=True,
    )


def render_header() -> None:
    if not LOGO_PATH.exists() and not MASCOTE_PATH.exists():
        return
    _inject_header_css("app-header")
    with st.container(key="app-header"):
        col_logo, col_title, col_mascote = st.columns([2, 5, 1])
        with col_logo:
            if LOGO_PATH.exists():
                st.image(str(LOGO_PATH), width=160)
        with col_mascote:
            if MASCOTE_PATH.exists():
                st.image(str(MASCOTE_PATH), width=90)


def render_hero(titulo: str, df: pd.DataFrame) -> None:
    _inject_header_css("app-hero")
    with st.container(key="app-hero"):
        col_logo, col_title, col_mascote = st.columns([2, 5, 1])
        with col_logo:
            if LOGO_PATH.exists():
                st.image(str(LOGO_PATH), width=160)
        with col_title:
            st.title(f"SLA — {titulo}")
        with col_mascote:
            if MASCOTE_PATH.exists():
                st.image(str(MASCOTE_PATH), width=90)
        render_kpis(df)


def login_screen() -> None:
    st.markdown(
        """<style>
        .st-key-login-wrap {
            min-height: 70vh;
            display: flex;
            flex-direction: column;
            justify-content: center;
        }
        </style>""",
        unsafe_allow_html=True,
    )
    with st.container(key="login-wrap"):
        _, col_centro, _ = st.columns([1, 1.4, 1])
        with col_centro:
            if LOGO_PATH.exists():
                _, col_logo, _ = st.columns([1, 2, 1])
                with col_logo:
                    st.image(str(LOGO_PATH), width="stretch")
            st.title("Dashboard SLA Transportadoras")
            with st.expander("Diagnóstico (temporário)"):
                try:
                    chaves = list(st.secrets.keys())
                except Exception as e:
                    chaves = f"erro ao ler secrets: {e}"
                st.write("Chaves em st.secrets:", chaves)
                try:
                    st.write("Valor de RESET_ADMIN:", repr(st.secrets.get("RESET_ADMIN", "AUSENTE")))
                except Exception as e:
                    st.write("Erro ao ler RESET_ADMIN:", str(e))
                st.write("NOVA_SENHA_ADMIN calculada:", repr(NOVA_SENHA_ADMIN))
                st.write("admin existe no banco:", admin_exists())
            if NOVA_SENHA_ADMIN:
                st.warning(
                    f"Senha de admin redefinida — usuário: `admin`, senha: `{NOVA_SENHA_ADMIN}`. "
                    "Copie agora e remova o secret RESET_ADMIN em Settings → Secrets.",
                    icon="🔑",
                )
            st.subheader("Login")
            with st.form("login_form"):
                username = st.text_input("Usuário")
                password = st.text_input("Senha", type="password")
                submitted = st.form_submit_button("Entrar", width="stretch")
            if submitted:
                user = authenticate(username.strip(), password)
                if user:
                    st.session_state["user"] = user
                    st.rerun()
                else:
                    st.error("Usuário ou senha inválidos.")


def render_kpis(df: pd.DataFrame) -> None:
    kpis = compute_kpis(df)
    cols = st.columns(5)
    cols[0].metric("Viagens", f"{kpis['total_viagens']:,}".replace(",", "."))
    cols[1].metric("No prazo (saída)", f"{kpis['pct_no_prazo_saida']:.1f}%")
    cols[2].metric("No prazo (chegada)", f"{kpis['pct_no_prazo_chegada']:.1f}%")
    cols[3].metric("Fora do prazo (chegada)", f"{kpis['qtd_fora_prazo_chegada']:,}".replace(",", "."))
    cols[4].metric("KM total", f"{kpis['km_total']:,.0f}".replace(",", "."))


def render_monthly_chart(df: pd.DataFrame, colors: dict) -> None:
    mensal = monthly_sla(df)
    if mensal.empty:
        st.info("Sem dados mensais suficientes para o período filtrado.")
        return
    melted = mensal.melt(
        id_vars=["mes_nome"],
        value_vars=["pct_no_prazo_saida", "pct_no_prazo_chegada"],
        var_name="indicador",
        value_name="percentual",
    )
    melted["indicador"] = melted["indicador"].map(
        {"pct_no_prazo_saida": "No prazo saída", "pct_no_prazo_chegada": "No prazo chegada"}
    )
    ordem_indicador = ["No prazo chegada", "No prazo saída"]

    ordem_mes = mensal["mes_nome"].tolist()
    eixo_x = alt.X("mes_nome:N", sort=ordem_mes, title="Mês", axis=alt.Axis(domainColor=colors["gridline"], tickColor=colors["gridline"], labelColor=colors["ink_secondary"]))

    base = alt.Chart(melted)
    line = base.mark_line(point=True, strokeWidth=2).encode(
        x=eixo_x,
        y=alt.Y(
            "percentual:Q", title="% no prazo",
            axis=alt.Axis(grid=False, labels=False, ticks=False, domainColor=colors["gridline"]),
        ),
        color=alt.Color(
            "indicador:N", title="", sort=ordem_indicador,
            scale=alt.Scale(domain=ordem_indicador, range=[BRAND_RED, colors["cor_secundaria"]]),
        ),
        tooltip=["mes_nome", "indicador", alt.Tooltip("percentual:Q", format=".1f")],
    )
    labels_chegada = (
        base.transform_filter(alt.datum.indicador == "No prazo chegada")
        .mark_text(dy=14, fontSize=11, fontWeight="bold")
        .encode(
            x=eixo_x, y="percentual:Q", text=alt.Text("percentual:Q", format=".0f"),
            color=alt.value(colors["ink_primary"]),
        )
    )
    labels_saida = (
        base.transform_filter(alt.datum.indicador == "No prazo saída")
        .mark_text(dy=-12, fontSize=11, fontWeight="bold")
        .encode(
            x=eixo_x, y="percentual:Q", text=alt.Text("percentual:Q", format=".0f"),
            color=alt.value(colors["ink_primary"]),
        )
    )
    chart = (
        alt.layer(line, labels_chegada, labels_saida)
        .properties(height=320, background="transparent")
        .configure_view(strokeWidth=0)
        .configure_legend(labelColor=colors["ink_secondary"], titleColor=colors["ink_primary"])
    )
    st.altair_chart(chart, width="stretch", theme=None)


def render_motivos_chart(df: pd.DataFrame, colors: dict) -> str | None:
    motivos = motivos_atraso_chegada(df)
    if motivos.empty:
        st.info("Sem atrasos de chegada registrados no período filtrado.")
        return None
    sel = alt.selection_point(fields=["motivo"], name="motivo_sel", empty=False)
    base = alt.Chart(motivos).transform_calculate(
        ocorrencias_fmt="replace(format(datum.ocorrencias, ',.0f'), /,/g, '.')"
    )
    bars = base.mark_bar(cornerRadiusEnd=3).encode(
        x=alt.X(
            "ocorrencias:Q", title="Ocorrências",
            axis=alt.Axis(grid=False, labels=False, ticks=False, domainColor=colors["gridline"]),
        ),
        y=alt.Y("motivo:N", sort="-x", title="", axis=alt.Axis(domainColor=colors["gridline"], labelColor=colors["ink_secondary"])),
        tooltip=["motivo", "ocorrencias"],
        color=alt.value(BRAND_RED),
        opacity=alt.condition(sel, alt.value(1.0), alt.value(0.5)),
    )
    labels = base.mark_text(align="left", dx=4, fontWeight="bold").encode(
        x="ocorrencias:Q", y=alt.Y("motivo:N", sort="-x"), text="ocorrencias_fmt:N",
        color=alt.value(colors["ink_primary"]),
    )
    chart = (
        alt.layer(bars, labels)
        .add_params(sel)
        .properties(height=320, background="transparent")
        .configure_view(strokeWidth=0)
    )
    event = st.altair_chart(chart, width="stretch", theme=None, on_select="rerun", key="motivos_chart")
    st.caption("Clique numa barra para ver o detalhe das viagens e registrar justificativa.")

    selecionados = []
    if event and event.selection:
        selecionados = event.selection.get("motivo_sel") or []
    return selecionados[0].get("motivo") if selecionados else None


def render_regional_chart(df: pd.DataFrame, colors: dict) -> None:
    regional = regional_dist(df)
    if regional.empty:
        st.info("Sem dados regionais para o período filtrado.")
        return
    base = alt.Chart(regional).transform_calculate(
        viagens_fmt="replace(format(datum.viagens, ',.0f'), /,/g, '.')"
    )
    bars = base.mark_bar(cornerRadiusEnd=3).encode(
        x=alt.X("regional:N", sort="-y", title="Regional", axis=alt.Axis(domainColor=colors["gridline"], labelColor=colors["ink_secondary"])),
        y=alt.Y(
            "viagens:Q", title="Viagens",
            axis=alt.Axis(grid=False, labels=False, ticks=False, domainColor=colors["gridline"]),
        ),
        tooltip=["regional", "viagens"],
        color=alt.value(BRAND_RED),
    )
    labels = base.mark_text(dy=-6, fontWeight="bold").encode(
        x=alt.X("regional:N", sort="-y"), y="viagens:Q", text="viagens_fmt:N",
        color=alt.value(colors["ink_primary"]),
    )
    chart = alt.layer(bars, labels).properties(height=320, background="transparent").configure_view(strokeWidth=0)
    st.altair_chart(chart, width="stretch", theme=None)


def render_ranking(df: pd.DataFrame) -> None:
    ranking = ranking_transportadoras(df)
    if ranking.empty:
        st.info("Sem dados suficientes para ranking.")
        return
    ranking = ranking.copy()
    ranking["viagens"] = ranking["viagens"].apply(milhar_str)
    st.dataframe(
        ranking.rename(
            columns={
                "abreviatura": "Transportadora",
                "viagens": "Viagens",
                "pct_no_prazo_chegada": "% no prazo (chegada)",
            }
        )[["Transportadora", "Viagens", "% no prazo (chegada)"]],
        width="stretch",
        hide_index=True,
        column_config={
            "Viagens": st.column_config.TextColumn(),
            "% no prazo (chegada)": st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100),
        },
    )


def render_detalhe_atraso(df: pd.DataFrame, motivo: str, user: dict) -> None:
    detalhe, colunas = detalhe_atraso(df, motivo)
    st.markdown(f"#### Detalhe — {motivo}")
    if detalhe.empty:
        st.info("Sem viagens para este motivo no período filtrado.")
        return

    chaves = detalhe["chave_viagem"].tolist()
    justificativas = get_justificativas(chaves)
    detalhe["Justificativa"] = detalhe["chave_viagem"].map(
        lambda k: justificativas.get(k, {}).get("justificativa", "")
    )
    detalhe["Anexo"] = detalhe["chave_viagem"].map(
        lambda k: justificativas.get(k, {}).get("anexo_nome", "") or "—"
    )

    colunas_exibir = list(colunas.values()) + ["Justificativa", "Anexo"]
    pode_editar = user["role"] == "transportadora"
    desabilitadas = colunas_exibir if not pode_editar else [c for c in colunas_exibir if c != "Justificativa"]

    config_colunas = {"Data": st.column_config.DateColumn(format="DD/MM/YYYY")}
    for col_datahora in ("Previsto chegada", "Real chegada", "Planejado saída", "Real saída"):
        if col_datahora in colunas_exibir:
            config_colunas[col_datahora] = st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm")

    editado = st.data_editor(
        detalhe[colunas_exibir],
        width="stretch",
        hide_index=True,
        disabled=desabilitadas,
        column_config=config_colunas,
        key=f"detalhe_editor_{motivo}",
    )

    if pode_editar:
        houve_alteracao = False
        for idx in detalhe.index:
            texto_novo = editado.loc[idx, "Justificativa"]
            texto_antigo = detalhe.loc[idx, "Justificativa"]
            if texto_novo != texto_antigo:
                salvar_justificativa_texto(
                    detalhe.loc[idx, "chave_viagem"], user["transportadora"], texto_novo, user["username"]
                )
                houve_alteracao = True
        if houve_alteracao:
            st.success("Justificativa salva.", icon="✅")
            st.rerun()

        with st.expander("Anexar arquivo a uma viagem"):
            opcoes = detalhe.index.tolist()
            escolha = st.selectbox(
                "Viagem",
                options=opcoes,
                format_func=lambda i: f"{detalhe.loc[i, 'ID Viagem']} — {formatar_data_br(detalhe.loc[i, 'Data'])}",
                key=f"anexo_sel_{motivo}",
            )
            arquivo = st.file_uploader("Arquivo", key=f"anexo_upload_{motivo}")
            if st.button("Salvar anexo", key=f"anexo_botao_{motivo}") and arquivo is not None:
                chave = detalhe.loc[escolha, "chave_viagem"]
                nome_seguro = f"{chave.replace('|', '_').replace('/', '-')}_{arquivo.name}"
                caminho = ANEXOS_DIR / nome_seguro
                caminho.write_bytes(arquivo.getbuffer())
                salvar_justificativa_anexo(
                    chave, user["transportadora"], arquivo.name, str(caminho), user["username"]
                )
                st.success(f"Anexo salvo para {detalhe.loc[escolha, 'ID Viagem']}.", icon="📎")
                st.rerun()


def render_table(df: pd.DataFrame) -> None:
    exibir = df[
        ["data", "id_viagem", "status", "transportadora", "motorista", "placa", "origem", "destino", "regional"]
    ].copy()
    exibir["no_prazo_saida"] = df["no_prazo_saida"].map({True: "No prazo", False: "Fora do prazo"})
    exibir["no_prazo_chegada"] = df["no_prazo_chegada"].map({True: "No prazo", False: "Fora do prazo"})
    exibir = exibir.rename(
        columns={
            "data": "Data",
            "id_viagem": "ID Viagem",
            "status": "Status",
            "transportadora": "Transportadora",
            "motorista": "Motorista",
            "placa": "Placa",
            "origem": "Origem",
            "destino": "Destino",
            "regional": "Regional",
            "no_prazo_saida": "Saída",
            "no_prazo_chegada": "Chegada",
        }
    )
    st.dataframe(
        exibir.sort_values("Data", ascending=False),
        width="stretch",
        hide_index=True,
        height=400,
        column_config={"Data": st.column_config.DateColumn(format="DD/MM/YYYY")},
    )


def dashboard_screen(user: dict) -> None:
    df = load_data()

    st.sidebar.title("Dashboard SLA")
    st.sidebar.caption(f"Usuário: {user['username']} ({user['role']})")

    colors = chart_colors(get_theme_mode())

    if user["role"] == "admin":
        opcoes = ["Todas"] + sorted(df["transportadora"].dropna().unique().tolist())
        selecionada = st.sidebar.selectbox("Transportadora", opcoes)
        if selecionada != "Todas":
            df = df[df["transportadora"] == selecionada]
        titulo = selecionada
    else:
        df = df[df["transportadora"] == user["transportadora"]]
        titulo = user["transportadora"] or "Transportadora"

    meses_disponiveis = sorted(df["mes_nome"].dropna().unique().tolist(), key=lambda m: df.loc[df["mes_nome"] == m, "mes"].iloc[0])
    if meses_disponiveis:
        meses_selecionados = st.sidebar.multiselect("Mês", meses_disponiveis, default=meses_disponiveis)
        if meses_selecionados:
            df = df[df["mes_nome"].isin(meses_selecionados)]

    quinzenas_disponiveis = sorted(df["quinzena"].dropna().unique().tolist())
    if quinzenas_disponiveis:
        quinzenas_selecionadas = st.sidebar.multiselect("Quinzena", quinzenas_disponiveis, default=quinzenas_disponiveis)
        if quinzenas_selecionadas:
            df = df[df["quinzena"].isin(quinzenas_selecionadas)]

    if st.sidebar.button("Sair"):
        del st.session_state["user"]
        st.rerun()

    render_hero(titulo, df)

    st.divider()
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Evolução mensal do SLA")
        render_monthly_chart(df, colors)
    motivo_selecionado = None
    with col2:
        st.subheader("Principais motivos de atraso (chegada)")
        motivo_selecionado = render_motivos_chart(df, colors)

    if motivo_selecionado:
        render_detalhe_atraso(df, motivo_selecionado, user)

    st.divider()
    if user["role"] == "admin":
        col3, col4 = st.columns(2)
        with col3:
            st.subheader("Viagens por regional")
            render_regional_chart(df, colors)
        with col4:
            st.subheader("Ranking de transportadoras")
            render_ranking(df)
        st.divider()

    st.subheader("Viagens")
    render_table(df)


def main() -> None:
    if "user" not in st.session_state:
        login_screen()
    else:
        dashboard_screen(st.session_state["user"])


if __name__ == "__main__":
    main()
