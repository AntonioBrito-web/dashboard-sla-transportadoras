from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from src.auth import admin_exists, authenticate, list_transportadora_users
from src.config import CACHE_TTL_SECONDS
from src.data import (
    clean_dataframe,
    compute_kpis,
    detalhe_atraso,
    detalhe_categoria,
    eh_motivo_saida,
    fetch_raw_dataframe,
    monthly_sla,
    motivos_atraso_chegada,
    ranking_transportadoras,
    regional_dist,
)
from src.db import (
    CATEGORIAS_APROVACAO,
    aprovar_justificativa,
    chaves_reprovadas,
    get_justificativas,
    get_meta,
    init_db,
    reprovar_justificativa,
    salvar_justificativa_anexo,
    salvar_justificativa_texto,
    set_meta,
)
from src.seed import reset_transportadora_password, seed_all
from src.theme import BRAND_RED, chart_colors


def milhar_str(valor) -> str:
    return f"{valor:,.0f}".replace(",", ".")


def formatar_data_br(valor) -> str:
    return valor.strftime("%d/%m/%Y") if pd.notna(valor) else ""


st.set_page_config(page_title="Dashboard SLA Transportadoras", layout="wide")


@st.cache_resource(show_spinner="Carregando transportadoras...")
def preparar_seed() -> str | None:
    # Só a semeadura fica em cache (rede + Google Sheets, é cara). O
    # init_db() NÃO pode ficar aqui dentro: se ele só rodasse uma vez por
    # processo, uma migração de esquema nova (ex.: colunas de aprovação)
    # nunca chegaria a rodar num processo que já estava de pé antes do
    # deploy — foi exatamente isso que quebrou a tabela de justificativas.
    # Retorna a senha do admin SE uma conta nova precisou ser criada agora
    # (ex.: disco zerado num reboot) — assim ela aparece na tela de login
    # em vez de sumir só no log, como aconteceu da primeira vez.
    return seed_all()


def verificar_reset_admin() -> str | None:
    # Roda em TODO rerun (é barato: só leitura de secret + banco) — não
    # depende do processo reiniciar. Funciona por VALOR, não por
    # true/false: guardamos o último texto do secret que já geramos senha
    # para ele, e disparamos de novo sempre que o texto atual for diferente
    # do último aplicado. Assim, pedir uma segunda senha nova é só trocar o
    # texto do secret (ex.: "true" -> "true2") — não precisa zerar antes.
    #
    # Válvula de escape: defina o secret RESET_ADMIN no painel do Streamlit
    # Cloud (Settings -> Secrets) com qualquer texto não vazio (ex. "true")
    # para forçar uma senha nova de admin — ela aparece na própria tela de
    # login. Quer outra senha depois? Só mudar o texto pra outro valor
    # (ex. "true2") e salvar de novo.
    try:
        valor_secret = str(st.secrets.get("RESET_ADMIN", "")).strip()
    except Exception:
        valor_secret = ""

    ultimo_valor_aplicado = get_meta("reset_admin_valor") or ""

    if valor_secret and valor_secret.lower() != "false" and valor_secret != ultimo_valor_aplicado:
        try:
            from src.seed import reset_admin_password

            nova_senha = reset_admin_password()
            set_meta("reset_admin_valor", valor_secret)
            return nova_senha
        except Exception as e:
            print(f"[seed] Falha ao redefinir senha do admin: {e}")
            return None

    if not valor_secret and ultimo_valor_aplicado:
        set_meta("reset_admin_valor", "")

    return None


def aplicar_padronizacao_usernames() -> None:
    # Roda só uma vez (flag gravada no banco, não em cache de processo) —
    # renomeia contas de transportadora já existentes para o padrão
    # abreviatura_logistica. Qualquer falha aqui (ex.: processo com um
    # sys.modules desatualizado após um deploy) não pode derrubar o app
    # inteiro — na pior hipótese, o admin refaz isso manualmente pelo botão
    # "Padronizar nomes de usuário" no painel lateral.
    if get_meta("usernames_padronizados") == "true":
        return
    try:
        from src.seed import padronizar_usernames_transportadora

        padronizar_usernames_transportadora()
        set_meta("usernames_padronizados", "true")
    except Exception as e:
        print(f"[seed] Falha ao padronizar usernames automaticamente: {e}")


init_db()  # roda em todo rerun — barato, e garante que o esquema fica sempre atualizado
SENHA_ADMIN_RECEM_CRIADA = preparar_seed()
aplicar_padronizacao_usernames()
NOVA_SENHA_ADMIN = verificar_reset_admin() or SENHA_ADMIN_RECEM_CRIADA

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
                st.write("reset_admin_valor (banco):", repr(get_meta("reset_admin_valor")))
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


STATUS_APROVACAO_LABEL = {"pendente": "Pendente", "aprovado": "Aprovado", "reprovado": "Reprovado"}


def render_tabela_detalhe(detalhe: pd.DataFrame, colunas: dict, user: dict, titulo: str, key_sufixo: str) -> None:
    st.markdown(f"#### {titulo}")
    if detalhe.empty:
        st.info("Sem viagens nesta categoria no período filtrado.")
        return

    chaves = detalhe["chave_viagem"].tolist()
    justificativas = get_justificativas(chaves)
    detalhe = detalhe.copy()
    detalhe["Justificativa"] = detalhe["chave_viagem"].map(
        lambda k: justificativas.get(k, {}).get("justificativa", "")
    )
    detalhe["Anexo"] = detalhe["chave_viagem"].map(
        lambda k: justificativas.get(k, {}).get("anexo_nome", "") or "—"
    )
    detalhe["_anexo_caminho"] = detalhe["chave_viagem"].map(
        lambda k: justificativas.get(k, {}).get("anexo_caminho", "")
    )
    detalhe["_status"] = detalhe["chave_viagem"].map(
        lambda k: justificativas.get(k, {}).get("status_aprovacao", "pendente")
    )

    pode_editar = user["role"] == "transportadora"
    eh_admin = user["role"] == "admin"

    colunas_exibir = list(colunas.values()) + ["Justificativa", "Anexo"]
    if eh_admin:
        detalhe["Decisão"] = detalhe["_status"].map(STATUS_APROVACAO_LABEL).fillna("Pendente")
        colunas_exibir = colunas_exibir + ["Decisão"]

    if pode_editar:
        # A Justificativa não é mais editável direto na tabela — escrever e
        # anexar viram formulários dedicados abaixo, cada um restrito às
        # viagens no estágio certo (sem justificativa / com justificativa
        # mas sem anexo). Isso é o que garante o bloqueio: uma vez escrita,
        # só o admin mexe nela de novo (reprovando).
        desabilitadas = colunas_exibir
    elif eh_admin:
        # só a Decisão é editável, e só quando existe justificativa pra avaliar
        desabilitadas = [c for c in colunas_exibir if c != "Decisão"]
    else:
        desabilitadas = colunas_exibir

    config_colunas = {"Data": st.column_config.DateColumn(format="DD/MM/YYYY")}
    for col_datahora in ("Previsto chegada", "Real chegada", "Planejado saída", "Real saída"):
        if col_datahora in colunas_exibir:
            config_colunas[col_datahora] = st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm")
    if eh_admin:
        config_colunas["Decisão"] = st.column_config.SelectboxColumn(
            options=["Pendente", "Aprovado", "Reprovado"]
        )

    editado = st.data_editor(
        detalhe[colunas_exibir],
        width="stretch",
        hide_index=True,
        disabled=desabilitadas,
        column_config=config_colunas,
        key=f"detalhe_editor_{key_sufixo}",
    )

    if pode_editar:
        sem_justificativa = [idx for idx in detalhe.index if not detalhe.loc[idx, "Justificativa"]]
        com_justificativa = [idx for idx in detalhe.index if detalhe.loc[idx, "Justificativa"]]

        with st.expander("Escrever justificativa"):
            if not sem_justificativa:
                st.info("Todas as viagens desta tabela já têm justificativa.")
            else:
                gen_justif = st.session_state.setdefault(f"justif_gen_{key_sufixo}", 0)
                escolha_j = st.selectbox(
                    "Viagem",
                    options=sem_justificativa,
                    format_func=lambda i: f"{detalhe.loc[i, 'ID Viagem']} — {formatar_data_br(detalhe.loc[i, 'Data'])}",
                    key=f"justif_sel_{key_sufixo}_{gen_justif}",
                )
                texto = st.text_area("Justificativa", key=f"justif_texto_{key_sufixo}_{gen_justif}")
                if st.button("Salvar justificativa", key=f"justif_botao_{key_sufixo}_{gen_justif}"):
                    if not texto.strip():
                        st.warning("Escreva um texto antes de salvar.", icon="⚠️")
                    else:
                        chave = detalhe.loc[escolha_j, "chave_viagem"]
                        salvar_justificativa_texto(chave, user["transportadora"], texto.strip(), user["username"])
                        st.session_state[f"justif_gen_{key_sufixo}"] = gen_justif + 1
                        st.success(f"Justificativa salva para {detalhe.loc[escolha_j, 'ID Viagem']}.", icon="✅")
                        st.rerun()

        with st.expander("Anexar arquivo a uma viagem"):
            if not com_justificativa:
                st.warning(
                    "Nenhuma viagem com justificativa preenchida ainda. "
                    "Escreva a justificativa antes de anexar um arquivo.",
                    icon="⚠️",
                )
            else:
                gen_anexo = st.session_state.setdefault(f"anexo_gen_{key_sufixo}", 0)
                escolha = st.selectbox(
                    "Viagem",
                    options=com_justificativa,
                    format_func=lambda i: f"{detalhe.loc[i, 'ID Viagem']} — {formatar_data_br(detalhe.loc[i, 'Data'])}",
                    key=f"anexo_sel_{key_sufixo}_{gen_anexo}",
                )
                arquivo = st.file_uploader("Arquivo", key=f"anexo_upload_{key_sufixo}_{gen_anexo}")
                if st.button("Salvar anexo", key=f"anexo_botao_{key_sufixo}_{gen_anexo}"):
                    if arquivo is None:
                        st.warning("Selecione um arquivo antes de salvar.", icon="⚠️")
                    else:
                        chave = detalhe.loc[escolha, "chave_viagem"]
                        nome_seguro = f"{chave.replace('|', '_').replace('/', '-')}_{arquivo.name}"
                        caminho = ANEXOS_DIR / nome_seguro
                        caminho.write_bytes(arquivo.getbuffer())
                        salvar_justificativa_anexo(
                            chave, user["transportadora"], arquivo.name, str(caminho), user["username"]
                        )
                        st.session_state[f"anexo_gen_{key_sufixo}"] = gen_anexo + 1
                        st.success(f"Anexo salvo para {detalhe.loc[escolha, 'ID Viagem']}.", icon="📎")
                        st.rerun()

    if eh_admin:
        com_anexo = [idx for idx in detalhe.index if detalhe.loc[idx, "_anexo_caminho"]]
        with st.expander(f"Ver anexos ({len(com_anexo)})"):
            if not com_anexo:
                st.caption("Nenhum anexo nesta tabela.")
            else:
                escolha_anexo = st.selectbox(
                    "Viagem",
                    options=com_anexo,
                    format_func=lambda i: f"{detalhe.loc[i, 'ID Viagem']} — {detalhe.loc[i, 'Anexo']}",
                    key=f"ver_anexo_sel_{key_sufixo}",
                )
                caminho_anexo = Path(detalhe.loc[escolha_anexo, "_anexo_caminho"])
                if caminho_anexo.exists():
                    st.download_button(
                        f"Abrir {detalhe.loc[escolha_anexo, 'Anexo']}",
                        data=caminho_anexo.read_bytes(),
                        file_name=detalhe.loc[escolha_anexo, "Anexo"],
                        key=f"ver_anexo_botao_{key_sufixo}",
                    )
                else:
                    st.error("Arquivo não encontrado no servidor (pode ter sido perdido num reinício do app).")

        for idx in detalhe.index:
            chave = detalhe.loc[idx, "chave_viagem"]
            chave_id = chave.replace("|", "_").replace("/", "-")
            justificativa_atual = detalhe.loc[idx, "Justificativa"]
            decisao_antiga = detalhe.loc[idx, "Decisão"]
            decisao_nova = editado.loc[idx, "Decisão"]
            if not justificativa_atual or decisao_nova == decisao_antiga:
                continue
            if decisao_nova == "Reprovado":
                reprovar_justificativa(chave, user["username"])
                st.warning(f"Justificativa de {detalhe.loc[idx, 'ID Viagem']} reprovada.", icon="🚫")
                st.rerun()
            elif decisao_nova == "Aprovado":
                st.session_state[f"aprovando_{chave_id}"] = True
            elif decisao_nova == "Pendente":
                st.session_state.pop(f"aprovando_{chave_id}", None)

        pendentes_categoria = [
            idx for idx in detalhe.index
            if st.session_state.get(f"aprovando_{detalhe.loc[idx, 'chave_viagem'].replace('|', '_').replace('/', '-')}")
        ]
        for idx in pendentes_categoria:
            chave = detalhe.loc[idx, "chave_viagem"]
            chave_id = chave.replace("|", "_").replace("/", "-")
            with st.form(f"form_aprova_{key_sufixo}_{chave_id}"):
                st.write(f"Aprovar justificativa — {detalhe.loc[idx, 'ID Viagem']}")
                categoria = st.selectbox(
                    "Categoria de responsabilidade", CATEGORIAS_APROVACAO, key=f"cat_{key_sufixo}_{chave_id}"
                )
                confirmar = st.form_submit_button("Confirmar aprovação")
            if confirmar:
                aprovar_justificativa(chave, categoria, user["username"])
                st.session_state.pop(f"aprovando_{chave_id}", None)
                st.success("Justificativa aprovada.", icon="✅")
                st.rerun()


def render_detalhe_atraso(df: pd.DataFrame, motivo: str, user: dict) -> None:
    detalhe, colunas = detalhe_atraso(df, motivo)
    titulo = "Detalhe Atraso Saída" if eh_motivo_saida(motivo) else f"Detalhe — {motivo}"
    render_tabela_detalhe(detalhe, colunas, user, titulo, motivo)


def render_notificacao_reprovacao(user: dict) -> None:
    chaves = chaves_reprovadas(user["transportadora"])
    if chaves:
        st.error(
            f"⚠️ {len(chaves)} justificativa(s) sua(s) foram reprovadas pelo admin. "
            "Refaça a justificativa e/ou o anexo nas tabelas abaixo para que a notificação suma.",
            icon="🚫",
        )


def render_tabelas_transportadora(df: pd.DataFrame, user: dict) -> None:
    detalhe_saida, colunas_saida = detalhe_categoria(df, "saida")
    render_tabela_detalhe(detalhe_saida, colunas_saida, user, "Detalhe Atraso Saída", "fixo_saida")

    detalhe_chegada, colunas_chegada = detalhe_categoria(df, "chegada")
    render_tabela_detalhe(detalhe_chegada, colunas_chegada, user, "Detalhe Atraso Chegada", "fixo_chegada")

    detalhe_transit, colunas_transit = detalhe_categoria(df, "transit")
    render_tabela_detalhe(detalhe_transit, colunas_transit, user, "Detalhe Atraso Transit time", "fixo_transit")


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


def render_gerenciar_senhas() -> None:
    with st.sidebar.expander("Gerenciar senhas de transportadoras"):
        usuarios = list_transportadora_users()
        if not usuarios:
            st.caption("Nenhuma conta de transportadora encontrada.")
            return
        opcoes = {f"{u['transportadora']} ({u['username']})": u["username"] for u in usuarios}
        escolha_label = st.selectbox("Transportadora", list(opcoes.keys()), key="reset_senha_transp_sel")
        if st.button("Gerar nova senha", key="reset_senha_transp_botao"):
            username = opcoes[escolha_label]
            nova_senha = reset_transportadora_password(username)
            st.success(f"Nova senha para `{username}`: `{nova_senha}`")
            st.caption("Copie agora — essa senha não fica salva em nenhuma tela depois de sair daqui.")

        st.divider()
        st.caption("Renomeia contas antigas para o padrão abreviatura_logistica (mantém a senha).")
        if st.button("Padronizar nomes de usuário", key="padronizar_usernames_botao"):
            from src.seed import padronizar_usernames_transportadora

            qtd = padronizar_usernames_transportadora()
            if qtd:
                st.success(f"{qtd} nome(s) de usuário padronizado(s).")
            else:
                st.info("Todos os nomes de usuário já estão padronizados.")


def dashboard_screen(user: dict) -> None:
    df = load_data()

    st.sidebar.title("Dashboard SLA")
    st.sidebar.caption(f"Usuário: {user['username']} ({user['role']})")

    if user["role"] == "admin":
        render_gerenciar_senhas()

    colors = chart_colors(get_theme_mode())

    if user["role"] == "admin":
        opcoes = ["Todas"] + sorted(df["transportadora"].dropna().unique().tolist())
        selecionada = st.sidebar.selectbox("Transportadora", opcoes)
        if selecionada != "Todas":
            df = df[df["transportadora"] == selecionada]
            titulo = selecionada
        else:
            titulo = "Geral das Transportadoras Parceiras"
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

    if user["role"] == "transportadora":
        render_notificacao_reprovacao(user)

    st.divider()
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Evolução mensal do SLA")
        render_monthly_chart(df, colors)
    motivo_selecionado = None
    with col2:
        st.subheader("Principais motivos de atraso")
        motivo_selecionado = render_motivos_chart(df, colors)

    if motivo_selecionado and user["role"] == "admin":
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

    if user["role"] == "transportadora":
        render_tabelas_transportadora(df, user)
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
