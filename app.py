from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from src.auth import (
    authenticate,
    list_internal_users,
    list_transportadora_users,
    set_email,
    set_password,
    verify_password,
)
from src.config import CACHE_TTL_SECONDS
from src.data import (
    clean_dataframe,
    compute_kpis,
    detalhe_categoria,
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
from src.seed import (
    criar_acesso_interno,
    ensure_usuarios_internos,
    reset_transportadora_password,
    reset_user_password,
    seed_all,
    senha_padrao,
    senha_padrao_legivel,
)
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
            print(f"[seed] Falha ao redefinir senha do admin: {e}", flush=True)
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
        print(f"[seed] Falha ao padronizar usernames automaticamente: {e}", flush=True)


init_db()  # roda em todo rerun — barato, e garante que o esquema fica sempre atualizado
preparar_seed()
aplicar_padronizacao_usernames()
verificar_reset_admin()

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


def trocar_senha_obrigatoria_screen(user: dict) -> None:
    # Conta criada/recriada pelo seed usa uma senha padrão previsível
    # (mesma fórmula pra todo mundo) — por segurança, força a troca antes
    # de liberar o dashboard. Não se aplica ao usuário "admin" (nunca tem
    # essa flag marcada, ver src/seed.py).
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
            st.title("Defina uma nova senha")
            email_cadastrado = (user.get("email") or "").strip()
            if email_cadastrado:
                st.info(
                    "Sua conta está usando a senha padrão temporária. Por segurança, "
                    "defina uma senha só sua antes de continuar.",
                    icon="🔐",
                )
            else:
                st.info(
                    "Sua conta está usando a senha padrão temporária e ainda não tem "
                    "e-mail cadastrado. Cadastre seu e-mail e defina uma senha só sua "
                    "antes de continuar.",
                    icon="🔐",
                )
            with st.form("trocar_senha_obrigatoria_form"):
                novo_email = "" if email_cadastrado else st.text_input("Seu e-mail")
                nova = st.text_input("Nova senha", type="password")
                confirma = st.text_input("Confirmar nova senha", type="password")
                submitted = st.form_submit_button("Definir senha e entrar", width="stretch")
            if submitted:
                if not email_cadastrado and ("@" not in novo_email or "." not in novo_email):
                    st.error("Informe um e-mail válido.")
                elif not nova or nova != confirma:
                    st.error("As senhas não conferem.")
                elif len(nova) < 6:
                    st.error("A nova senha deve ter pelo menos 6 caracteres.")
                else:
                    if not email_cadastrado:
                        set_email(user["username"], novo_email)
                    set_password(user["username"], nova, deve_trocar_senha=False)
                    novo_user = dict(user)
                    novo_user["deve_trocar_senha"] = False
                    novo_user["email"] = email_cadastrado or novo_email
                    st.session_state["user"] = novo_user
                    st.success("Senha definida!")
                    st.rerun()
            if st.button("Sair", key="sair_troca_obrigatoria"):
                del st.session_state["user"]
                st.rerun()


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


def render_motivos_chart(df: pd.DataFrame, colors: dict) -> None:
    motivos = motivos_atraso_chegada(df)
    if motivos.empty:
        st.info("Sem atrasos de chegada registrados no período filtrado.")
        return
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
    )
    labels = base.mark_text(align="left", dx=4, fontWeight="bold").encode(
        x="ocorrencias:Q", y=alt.Y("motivo:N", sort="-x"), text="ocorrencias_fmt:N",
        color=alt.value(colors["ink_primary"]),
    )
    chart = (
        alt.layer(bars, labels)
        .properties(height=320, background="transparent")
        .configure_view(strokeWidth=0)
    )
    st.altair_chart(chart, width="stretch", theme=None)


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


def render_tabela_detalhe(
    detalhe: pd.DataFrame, colunas: dict, user: dict, titulo: str, key_sufixo: str, mostrar_titulo: bool = True
) -> None:
    if mostrar_titulo:
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
    pode_aprovar = user["role"] == "admin"
    # "interno" enxerga a mesma tela do admin (todas as transportadoras,
    # coluna Decisão, anexos), mas nunca edita nada — nem justificativa nem
    # a decisão de aprovação, só o admin de fato faz isso.
    ve_como_admin = user["role"] in ("admin", "interno")

    colunas_exibir = list(colunas.values()) + ["Justificativa", "Anexo"]
    if ve_como_admin:
        detalhe["Decisão"] = detalhe["_status"].map(STATUS_APROVACAO_LABEL).fillna("Pendente")
        colunas_exibir = colunas_exibir + ["Decisão"]

    if pode_editar:
        # A Justificativa não é mais editável direto na tabela — escrever e
        # anexar viram formulários dedicados abaixo, cada um restrito às
        # viagens no estágio certo (sem justificativa / com justificativa
        # mas sem anexo). Isso é o que garante o bloqueio: uma vez escrita,
        # só o admin mexe nela de novo (reprovando).
        desabilitadas = colunas_exibir
    elif pode_aprovar:
        # só a Decisão é editável, e só quando existe justificativa pra avaliar
        desabilitadas = [c for c in colunas_exibir if c != "Decisão"]
    else:
        desabilitadas = colunas_exibir

    config_colunas = {"Data": st.column_config.DateColumn(format="DD/MM/YYYY")}
    for col_datahora in ("Previsto chegada", "Real chegada", "Planejado saída", "Real saída"):
        if col_datahora in colunas_exibir:
            config_colunas[col_datahora] = st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm")
    if pode_aprovar:
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

    if ve_como_admin:
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
                    if caminho_anexo.suffix.lower() in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
                        st.image(str(caminho_anexo), width="stretch")
                    else:
                        st.caption("Pré-visualização não disponível para este tipo de arquivo — use o botão abaixo.")
                    st.download_button(
                        f"Baixar {detalhe.loc[escolha_anexo, 'Anexo']}",
                        data=caminho_anexo.read_bytes(),
                        file_name=detalhe.loc[escolha_anexo, "Anexo"],
                        key=f"ver_anexo_botao_{key_sufixo}",
                    )
                else:
                    st.error("Arquivo não encontrado no servidor (pode ter sido perdido num reinício do app).")

    if pode_aprovar:
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


def render_notificacao_reprovacao(user: dict) -> None:
    chaves = chaves_reprovadas(user["transportadora"])
    if chaves:
        st.error(
            f"⚠️ {len(chaves)} justificativa(s) sua(s) foram reprovadas pelo admin. "
            "Refaça a justificativa e/ou o anexo nas tabelas abaixo para que a notificação suma.",
            icon="🚫",
        )


def render_tabelas_fixas(df: pd.DataFrame, user: dict) -> None:
    # Usado tanto pra transportadora quanto pro admin: garante acesso direto
    # às 3 dimensões de atraso (saída/chegada/transit), sem depender de
    # navegar bar a bar no gráfico — o que pode "esconder" justificativas
    # quando a Aba Principal e a aba Saída real divergem sobre uma viagem
    # específica (uma diz que atrasou, a outra diz que não).
    detalhe_saida, colunas_saida = detalhe_categoria(df, "saida")
    detalhe_chegada, colunas_chegada = detalhe_categoria(df, "chegada")
    detalhe_transit, colunas_transit = detalhe_categoria(df, "transit")

    if user["role"] in ("admin", "interno"):
        # No admin/interno, as 3 viram abas clicáveis em vez de empilhadas —
        # menos rolagem pra achar a que interessa.
        aba_saida, aba_chegada, aba_transit = st.tabs(
            ["Detalhe Atraso Saída", "Detalhe Atraso Chegada", "Detalhe Atraso Transit time"]
        )
        with aba_saida:
            render_tabela_detalhe(detalhe_saida, colunas_saida, user, "Detalhe Atraso Saída", "fixo_saida", mostrar_titulo=False)
        with aba_chegada:
            render_tabela_detalhe(detalhe_chegada, colunas_chegada, user, "Detalhe Atraso Chegada", "fixo_chegada", mostrar_titulo=False)
        with aba_transit:
            render_tabela_detalhe(detalhe_transit, colunas_transit, user, "Detalhe Atraso Transit time", "fixo_transit", mostrar_titulo=False)
    else:
        render_tabela_detalhe(detalhe_saida, colunas_saida, user, "Detalhe Atraso Saída", "fixo_saida")
        render_tabela_detalhe(detalhe_chegada, colunas_chegada, user, "Detalhe Atraso Chegada", "fixo_chegada")
        render_tabela_detalhe(detalhe_transit, colunas_transit, user, "Detalhe Atraso Transit time", "fixo_transit")


def render_table(df: pd.DataFrame) -> None:
    exibir = df[
        ["data", "id_viagem", "status", "abreviatura", "motorista", "placa", "origem", "destino", "regional"]
    ].copy()
    exibir["no_prazo_saida"] = df["no_prazo_saida"].map({True: "No prazo", False: "Fora do prazo"})
    exibir["no_prazo_chegada"] = df["no_prazo_chegada"].map({True: "No prazo", False: "Fora do prazo"})
    exibir["no_prazo_transit"] = df["no_prazo_transit"].map({True: "No prazo", False: "Fora do prazo"})
    exibir = exibir.rename(
        columns={
            "data": "Data",
            "id_viagem": "ID Viagem",
            "status": "Status",
            "abreviatura": "Transportadora",
            "motorista": "Motorista",
            "placa": "Placa",
            "origem": "Origem",
            "destino": "Destino",
            "regional": "Regional",
            "no_prazo_saida": "Saída",
            "no_prazo_chegada": "Chegada",
            "no_prazo_transit": "Transit time",
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
        opcoes = {f"{u['transportadora']} ({u['username']})": u for u in usuarios}
        escolha_label = st.selectbox("Transportadora", list(opcoes.keys()), key="reset_senha_transp_sel")
        usuario_selecionado = opcoes[escolha_label]
        col_padrao, col_nova = st.columns(2)
        with col_padrao:
            if st.button("Ver senha padrão", key="ver_senha_padrao_transp_botao"):
                st.info(f"Senha padrão de `{usuario_selecionado['username']}`: `{senha_padrao(usuario_selecionado['username'])}`")
        with col_nova:
            if st.button("Gerar nova senha", key="reset_senha_transp_botao"):
                nova_senha = reset_transportadora_password(usuario_selecionado["username"])
                st.success(f"Nova senha para `{usuario_selecionado['username']}`: `{nova_senha}`")
        st.caption(
            "\"Ver senha padrão\" só calcula (não altera nada). \"Gerar nova senha\" troca de "
            "verdade — se o banco for zerado num redeploy, a conta volta com a senha padrão."
        )

        st.caption("E-mail cadastrado (usado para a transportadora trocar a própria senha).")
        novo_email = st.text_input(
            "E-mail", value=usuario_selecionado["email"], key=f"email_transp_{usuario_selecionado['username']}"
        )
        if st.button("Salvar e-mail", key="salvar_email_transp_botao"):
            set_email(usuario_selecionado["username"], novo_email)
            st.success("E-mail atualizado.")
            st.rerun()

        st.divider()
        st.caption("Renomeia contas antigas para o padrão abreviatura_logistica (mantém a senha).")
        if st.button("Padronizar nomes de usuário", key="padronizar_usernames_botao"):
            from src.seed import padronizar_usernames_transportadora

            qtd = padronizar_usernames_transportadora()
            if qtd:
                st.success(f"{qtd} nome(s) de usuário padronizado(s).")
            else:
                st.info("Todos os nomes de usuário já estão padronizados.")


def render_gerenciar_acessos_internos() -> None:
    with st.sidebar.expander("Gerenciar acessos internos"):
        st.caption(
            "Contas de uso interno (não-transportadora): admin pleno ou "
            "visualização completa sem editar justificativas nem gerenciar senhas. "
            "Essas contas já são recriadas sozinhas a cada boot do app (senha padrão "
            "determinística) — o botão abaixo é só um atalho manual, se quiser forçar."
        )
        if st.button("Criar contas padrão da lista", key="seed_internos_botao"):
            novos = ensure_usuarios_internos()
            if novos:
                st.success(f"{len(novos)} conta(s) criada(s):")
                for reg in novos:
                    st.code(f"{reg['nome']} — usuário: {reg['usuario']} — senha: {reg['senha']} — {reg['role']}")
                st.caption("Copie agora — essas senhas não ficam salvas em nenhuma tela depois de sair daqui.")
            else:
                st.info("Todas as contas da lista padrão já existem.")

        st.divider()
        usuarios = list_internal_users()
        if usuarios:
            opcoes = {f"{u['username']} ({u['role']})": u for u in usuarios}
            escolha_label = st.selectbox("Conta", list(opcoes.keys()), key="reset_senha_interno_sel")
            usuario_selecionado = opcoes[escolha_label]
            col_padrao, col_nova = st.columns(2)
            with col_padrao:
                if st.button("Ver senha padrão", key="ver_senha_padrao_interno_botao"):
                    st.info(f"Senha padrão de `{usuario_selecionado['username']}`: `{senha_padrao_legivel(usuario_selecionado['username'])}`")
            with col_nova:
                if st.button("Gerar nova senha", key="reset_senha_interno_botao"):
                    nova_senha = reset_user_password(usuario_selecionado["username"])
                    st.success(f"Nova senha para `{usuario_selecionado['username']}`: `{nova_senha}`")
            st.caption(
                "\"Ver senha padrão\" só calcula (não altera nada). \"Gerar nova senha\" troca de "
                "verdade — se o banco for zerado num redeploy, a conta volta com a senha padrão."
            )

            novo_email = st.text_input(
                "E-mail", value=usuario_selecionado["email"], key=f"email_interno_{usuario_selecionado['username']}"
            )
            if st.button("Salvar e-mail", key="salvar_email_interno_botao"):
                set_email(usuario_selecionado["username"], novo_email)
                st.success("E-mail atualizado.")
                st.rerun()

        st.divider()
        st.caption("Cadastro avulso de um novo acesso interno.")
        nome_novo = st.text_input("Nome completo", key="novo_interno_nome")
        email_novo = st.text_input("E-mail", key="novo_interno_email")
        role_novo = st.selectbox(
            "Nível de acesso",
            ["Interno (visualização, sem editar/gerenciar senhas)", "Admin (acesso pleno)"],
            key="novo_interno_role",
        )
        if st.button("Criar acesso", key="novo_interno_botao"):
            if not nome_novo.strip():
                st.warning("Informe o nome completo.", icon="⚠️")
            else:
                role = "admin" if role_novo.startswith("Admin") else "interno"
                registro = criar_acesso_interno(nome_novo.strip(), role, email_novo)
                st.success(
                    f"Conta criada — usuário: `{registro['usuario']}` — senha: `{registro['senha']}`"
                )
                st.caption("Copie agora — essa senha não fica salva em nenhuma tela depois de sair daqui.")


def render_alterar_senha(user: dict) -> None:
    with st.sidebar.expander("Alterar minha senha"):
        email_cadastrado = (user.get("email") or "").strip().lower()
        if not email_cadastrado:
            st.caption(
                "Nenhum e-mail cadastrado nesta conta ainda. Cadastre um e-mail — "
                "ele vira o padrão usado pra confirmar trocas de senha por aqui."
            )
            novo_email_cadastro = st.text_input("Seu e-mail", key="cadastro_email_input")
            senha_atual_cadastro = st.text_input(
                "Senha atual (confirma que é você)", type="password", key="cadastro_email_senha"
            )
            if st.button("Cadastrar e-mail", key="cadastro_email_botao"):
                if "@" not in novo_email_cadastro or "." not in novo_email_cadastro:
                    st.error("Informe um e-mail válido.")
                elif not verify_password(senha_atual_cadastro, user["password_hash"]):
                    st.error("Senha atual incorreta.")
                else:
                    set_email(user["username"], novo_email_cadastro)
                    st.success("E-mail cadastrado! Abra este menu de novo para trocar a senha.")
                    st.rerun()
            return
        st.caption("Por segurança, confirme o e-mail cadastrado nesta conta.")
        email_confirma = st.text_input("E-mail cadastrado", key="chsenha_email")
        senha_atual = st.text_input("Senha atual", type="password", key="chsenha_atual")
        nova = st.text_input("Nova senha", type="password", key="chsenha_nova")
        confirma = st.text_input("Confirmar nova senha", type="password", key="chsenha_confirma")
        if st.button("Alterar senha", key="chsenha_botao"):
            if email_confirma.strip().lower() != email_cadastrado:
                st.error("E-mail não confere com o cadastrado.")
            elif not verify_password(senha_atual, user["password_hash"]):
                st.error("Senha atual incorreta.")
            elif not nova or nova != confirma:
                st.error("Nova senha e confirmação não conferem.")
            elif len(nova) < 6:
                st.error("Nova senha deve ter pelo menos 6 caracteres.")
            else:
                set_password(user["username"], nova, deve_trocar_senha=False)
                st.success("Senha alterada com sucesso! Use a nova senha no próximo login.")


def dashboard_screen(user: dict) -> None:
    df = load_data()

    st.sidebar.title("Dashboard SLA")
    st.sidebar.caption(f"Usuário: {user['username']} ({user['role']})")

    if user["role"] == "admin":
        render_gerenciar_senhas()
        render_gerenciar_acessos_internos()

    render_alterar_senha(user)

    colors = chart_colors(get_theme_mode())

    if user["role"] in ("admin", "interno"):
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
    with col2:
        st.subheader("Principais motivos de atraso")
        render_motivos_chart(df, colors)

    st.divider()
    if user["role"] in ("admin", "interno"):
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

    if user["role"] in ("transportadora", "admin", "interno"):
        st.divider()
        render_tabelas_fixas(df, user)


def main() -> None:
    if "user" not in st.session_state:
        login_screen()
    else:
        user = st.session_state["user"]
        if user.get("deve_trocar_senha"):
            trocar_senha_obrigatoria_screen(user)
        else:
            dashboard_screen(user)


if __name__ == "__main__":
    main()
