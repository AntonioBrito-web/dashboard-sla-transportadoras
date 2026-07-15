from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from src.auth import (
    authenticate,
    list_all_users,
    list_internal_users,
    list_transportadora_users,
    set_password,
    set_user_role,
    verify_password,
)
from src.config import CACHE_TTL_SECONDS
from src.data import (
    clean_dataframe,
    compute_kpis,
    detalhe_categoria,
    fetch_raw_dataframe,
    load_transportadoras,
    monthly_sla,
    motivos_atraso_chegada,
    motoristas_ofensores,
    ranking_transportadoras,
    regional_dist,
    transportadora_abreviatura_map,
)
from src.db import CATEGORIAS_APROVACAO, get_meta, init_db, set_meta
from src.turso_db import (
    aprovar_justificativa,
    chaves_reprovadas,
    excluir_justificativa,
    get_anexo,
    get_anexo_por_id,
    get_email,
    get_justificativas,
    init_justificativas_db,
    init_usuarios_db,
    listar_anexos,
    reprovar_justificativa,
    salvar_anexos,
    salvar_justificativa_texto,
    set_email,
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


def _campo_clicado(chave_widget: str, nome_selecao: str, campo: str) -> str | None:
    # Lê o estado de um clique em gráfico (Altair, on_select="rerun") a
    # partir do session_state — funciona porque o Streamlit já atualiza
    # esse estado ANTES do script rodar de novo, então dá pra ler aqui,
    # no topo do dashboard, antes até de o widget ser desenhado outra vez.
    estado = st.session_state.get(chave_widget)
    if not estado:
        return None
    pontos = estado.get("selection", {}).get(nome_selecao)
    if pontos:
        return pontos[0].get(campo)
    return None


def _linha_selecionada(chave_widget: str) -> int | None:
    # Mesma ideia, mas pra clique de linha em st.dataframe(on_select="rerun").
    estado = st.session_state.get(chave_widget)
    if not estado:
        return None
    linhas = estado.get("selection", {}).get("rows")
    return linhas[0] if linhas else None


st.set_page_config(page_title="Dashboard SLA Transportadoras", layout="wide")


@st.cache_resource(show_spinner="Carregando transportadoras...")
def preparar_seed() -> str | None:
    # Só a semeadura fica em cache (rede + Google Sheets, é cara). O
    # init_db() NÃO pode ficar aqui dentro: se ele só rodasse uma vez por
    # processo, uma migração de esquema nova (ex.: colunas de aprovação)
    # nunca chegaria a rodar num processo que já estava de pé antes do
    # deploy — foi exatamente isso que quebrou a tabela de justificativas.
    # Usuários agora vivem no Turso (persistente) — se essa chamada falhar
    # (Turso fora do ar), não pode derrubar o app inteiro: só significa que
    # ninguém consegue logar até o banco voltar, o que já fica claro na
    # tela de login via TURSO_DISPONIVEL.
    try:
        return seed_all()
    except Exception as e:
        print(f"[seed] Falha ao semear contas: {e}", flush=True)
        return None


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


@st.cache_resource(show_spinner=False)
def _preparar_turso() -> bool:
    # Usuários (login), justificativas e anexos vivem todos no Turso
    # (externo, persistente) — ver src/turso_db.py. Isso precisa rodar
    # ANTES de qualquer semeadura de conta (preparar_seed) ou tentativa de
    # login: sem o Turso disponível, não tem onde guardar/ler usuário
    # nenhum. Se os secrets TURSO_DATABASE_URL/TURSO_AUTH_TOKEN não
    # estiverem configurados (ou o Turso estiver fora do ar), o app mostra
    # uma mensagem clara na tela de login em vez de estourar uma exceção.
    try:
        init_justificativas_db()
        init_usuarios_db()
        print("[turso] Conectado com sucesso — usuários/justificativas/anexos disponíveis.", flush=True)
        return True
    except Exception as e:
        print(f"[turso] Turso indisponível: {e}", flush=True)
        return False


init_db()  # roda em todo rerun — barato, e garante que o esquema fica sempre atualizado
TURSO_DISPONIVEL = _preparar_turso()
if TURSO_DISPONIVEL:
    preparar_seed()
    aplicar_padronizacao_usernames()
    verificar_reset_admin()


def email_atual(username: str) -> str:
    # O e-mail cadastrado mora só no Turso agora (persistente) — a coluna
    # email da tabela users local não é mais usada pra isso, porque some a
    # cada wipe do disco efêmero junto com a conta recriada.
    if not TURSO_DISPONIVEL:
        return ""
    try:
        return get_email(username)
    except Exception:
        return ""


def definir_email(username: str, email: str) -> bool:
    if not TURSO_DISPONIVEL:
        return False
    try:
        set_email(username, email)
        return True
    except Exception:
        return False

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
LOGO_PATH = ASSETS_DIR / "Logo-JT-Express-Red.png"
MASCOTE_PATH = ASSETS_DIR / "mao mao.png"


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
            if not TURSO_DISPONIVEL:
                st.error(
                    "Sistema temporariamente indisponível — não foi possível conectar ao "
                    "banco de contas. Tente novamente em alguns instantes ou avise o admin.",
                    icon="🚫",
                )
                return
            st.subheader("Login")
            with st.form("login_form"):
                username = st.text_input("Usuário")
                password = st.text_input("Senha", type="password")
                submitted = st.form_submit_button("Entrar", width="stretch")
            if submitted:
                try:
                    user = authenticate(username.strip(), password)
                except Exception as e:
                    st.error(f"Falha ao verificar login: {e}")
                    return
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
            # Só pede e-mail se realmente não tiver um cadastrado E o Turso
            # estiver disponível pra salvar — não faz sentido travar o
            # acesso pedindo e-mail se não há como persistir agora.
            pedir_email = not email_cadastrado and TURSO_DISPONIVEL
            if email_cadastrado:
                st.info(
                    "Sua conta está usando a senha padrão temporária. Por segurança, "
                    "defina uma senha só sua antes de continuar.",
                    icon="🔐",
                )
            elif pedir_email:
                st.info(
                    "Sua conta está usando a senha padrão temporária e ainda não tem "
                    "e-mail cadastrado. Cadastre seu e-mail e defina uma senha só sua "
                    "antes de continuar.",
                    icon="🔐",
                )
            else:
                st.info(
                    "Sua conta está usando a senha padrão temporária. Por segurança, "
                    "defina uma senha só sua antes de continuar.",
                    icon="🔐",
                )
            with st.form("trocar_senha_obrigatoria_form"):
                novo_email = st.text_input("Seu e-mail") if pedir_email else ""
                nova = st.text_input("Nova senha", type="password")
                confirma = st.text_input("Confirmar nova senha", type="password")
                submitted = st.form_submit_button("Definir senha e entrar", width="stretch")
            if submitted:
                if pedir_email and ("@" not in novo_email or "." not in novo_email):
                    st.error("Informe um e-mail válido.")
                elif not nova or nova != confirma:
                    st.error("As senhas não conferem.")
                elif len(nova) < 6:
                    st.error("A nova senha deve ter pelo menos 6 caracteres.")
                else:
                    if pedir_email and not definir_email(user["username"], novo_email):
                        st.warning(
                            "Não foi possível salvar o e-mail agora — tente de novo depois "
                            "em \"Alterar minha senha\" na lateral.",
                            icon="⚠️",
                        )
                    set_password(user["username"], nova, deve_trocar_senha=False)
                    novo_user = dict(user)
                    novo_user["deve_trocar_senha"] = False
                    novo_user["email"] = email_cadastrado or (novo_email if pedir_email else "")
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
    selecao = alt.selection_point(name="sel_motivo", fields=["motivo"], on="click", clear="dblclick")
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
        opacity=alt.condition(selecao, alt.value(1), alt.value(0.35)),
    ).add_params(selecao)
    labels = base.mark_text(align="left", dx=4, fontWeight="bold").encode(
        x="ocorrencias:Q", y=alt.Y("motivo:N", sort="-x"), text="ocorrencias_fmt:N",
        color=alt.value(colors["ink_primary"]),
    )
    chart = (
        alt.layer(bars, labels)
        .properties(height=320, background="transparent")
        .configure_view(strokeWidth=0)
    )
    st.altair_chart(chart, width="stretch", theme=None, on_select="rerun", key="chart_motivos")


def render_regional_chart(df: pd.DataFrame, colors: dict) -> None:
    regional = regional_dist(df)
    if regional.empty:
        st.info("Sem dados regionais para o período filtrado.")
        return
    selecao = alt.selection_point(name="sel_regional", fields=["regional"], on="click", clear="dblclick")
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
        opacity=alt.condition(selecao, alt.value(1), alt.value(0.35)),
    ).add_params(selecao)
    labels = base.mark_text(dy=-6, fontWeight="bold").encode(
        x=alt.X("regional:N", sort="-y"), y="viagens:Q", text="viagens_fmt:N",
        color=alt.value(colors["ink_primary"]),
    )
    chart = alt.layer(bars, labels).properties(height=320, background="transparent").configure_view(strokeWidth=0)
    st.altair_chart(chart, width="stretch", theme=None, on_select="rerun", key="chart_regional")


def render_ranking(df: pd.DataFrame) -> None:
    ranking = ranking_transportadoras(df).reset_index(drop=True)
    if ranking.empty:
        st.info("Sem dados suficientes para ranking.")
        return
    exibir = ranking.copy()
    exibir["viagens"] = exibir["viagens"].apply(milhar_str)
    st.dataframe(
        exibir.rename(
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
        on_select="rerun",
        selection_mode="single-row",
        key="tabela_ranking",
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
    if not TURSO_DISPONIVEL:
        st.error(
            "Justificativas e anexos estão indisponíveis no momento — o banco "
            "persistente (Turso) não está configurado. Configure TURSO_DATABASE_URL "
            "e TURSO_AUTH_TOKEN em Settings → Secrets no Streamlit Cloud.",
            icon="🚫",
        )
        return

    chaves = detalhe["chave_viagem"].tolist()
    try:
        justificativas = get_justificativas(chaves)
    except Exception as e:
        st.error(f"Falha ao carregar justificativas do banco: {e}", icon="🚫")
        return
    # reset_index é obrigatório aqui: o data_editor rastreia edições pela
    # posição da linha, e sem um índice 0..n contíguo (o "detalhe" chega
    # ordenado por Data, com índice espalhado vindo do df original) a
    # edição registrada podia cair na linha errada — inclusive fazendo o
    # popup de aprovação nunca aparecer pra linha que o usuário de fato
    # marcou como "Aprovado".
    detalhe = detalhe.reset_index(drop=True).copy()
    detalhe["Justificativa"] = detalhe["chave_viagem"].map(
        lambda k: justificativas.get(k, {}).get("justificativa", "")
    )
    detalhe["Anexo"] = detalhe["chave_viagem"].map(
        lambda k: (lambda q: f"{q} anexo(s)" if q else "—")(justificativas.get(k, {}).get("qtd_anexos", 0))
    )
    detalhe["_tem_anexo"] = detalhe["chave_viagem"].map(
        lambda k: justificativas.get(k, {}).get("qtd_anexos", 0) > 0
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
    for col_datahora in ("Previsto chegada", "Real chegada", "Planejado saída", "Real saída", "TT planejado", "TT real"):
        if col_datahora in colunas_exibir:
            config_colunas[col_datahora] = st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm")
    if pode_aprovar:
        config_colunas["Decisão"] = st.column_config.SelectboxColumn(
            options=["Pendente", "Aprovado", "Reprovado", "Excluir"]
        )

    editado = st.data_editor(
        detalhe[colunas_exibir],
        width="stretch",
        hide_index=True,
        disabled=desabilitadas,
        column_config=config_colunas,
        row_height=80,
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
                # A opção do selectbox é a própria chave_viagem, não a
                # posição da linha: se a lista de viagens mudar (ex.: o
                # admin troca o filtro de transportadora/mês na lateral),
                # uma posição antiga selecionada podia coincidir com uma
                # posição válida do novo recorte e apontar pra outra viagem
                # sem o usuário perceber. Com a chave como valor, uma
                # seleção que não existe mais no novo recorte simplesmente
                # volta pro padrão em vez de apontar pra viagem errada.
                mapa_justif = {detalhe.loc[idx, "chave_viagem"]: idx for idx in sem_justificativa}
                escolha_j_chave = st.selectbox(
                    "Viagem",
                    options=list(mapa_justif.keys()),
                    format_func=lambda chave_v: (
                        f"{detalhe.loc[mapa_justif[chave_v], 'ID Viagem']} — "
                        f"{formatar_data_br(detalhe.loc[mapa_justif[chave_v], 'Data'])}"
                    ),
                    key=f"justif_sel_{key_sufixo}_{gen_justif}",
                )
                escolha_j = mapa_justif[escolha_j_chave]
                texto = st.text_area("Justificativa", key=f"justif_texto_{key_sufixo}_{gen_justif}")
                if st.button("Salvar justificativa", key=f"justif_botao_{key_sufixo}_{gen_justif}"):
                    if not texto.strip():
                        st.warning("Escreva um texto antes de salvar.", icon="⚠️")
                    else:
                        try:
                            salvar_justificativa_texto(escolha_j_chave, user["transportadora"], texto.strip(), user["username"])
                        except Exception as e:
                            st.error(f"Falha ao salvar a justificativa: {e}")
                        else:
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
                # Mesma correção do bloco de justificativa acima: opção
                # pela chave_viagem, não pela posição da linha.
                mapa_anexo = {detalhe.loc[idx, "chave_viagem"]: idx for idx in com_justificativa}
                escolha_chave = st.selectbox(
                    "Viagem",
                    options=list(mapa_anexo.keys()),
                    format_func=lambda chave_v: (
                        f"{detalhe.loc[mapa_anexo[chave_v], 'ID Viagem']} — "
                        f"{formatar_data_br(detalhe.loc[mapa_anexo[chave_v], 'Data'])}"
                    ),
                    key=f"anexo_sel_{key_sufixo}_{gen_anexo}",
                )
                escolha = mapa_anexo[escolha_chave]
                arquivos = st.file_uploader(
                    "Arquivos (pode selecionar vários de uma vez)",
                    key=f"anexo_upload_{key_sufixo}_{gen_anexo}",
                    accept_multiple_files=True,
                )
                if st.button("Salvar anexo(s)", key=f"anexo_botao_{key_sufixo}_{gen_anexo}"):
                    if not arquivos:
                        st.warning("Selecione ao menos um arquivo antes de salvar.", icon="⚠️")
                    else:
                        try:
                            salvar_anexos(
                                escolha_chave,
                                user["transportadora"],
                                [(a.name, a.getvalue()) for a in arquivos],
                                user["username"],
                            )
                        except Exception as e:
                            st.error(f"Falha ao salvar o(s) anexo(s): {e}")
                        else:
                            st.session_state[f"anexo_gen_{key_sufixo}"] = gen_anexo + 1
                            st.success(
                                f"{len(arquivos)} anexo(s) salvo(s) para {detalhe.loc[escolha, 'ID Viagem']}.",
                                icon="📎",
                            )
                            st.rerun()

    if ve_como_admin:
        com_anexo = [idx for idx in detalhe.index if detalhe.loc[idx, "_tem_anexo"]]
        with st.expander(f"Ver anexos ({len(com_anexo)} viagem(ns) com anexo)"):
            if not com_anexo:
                st.caption("Nenhum anexo nesta tabela.")
            else:
                # Mesma correção: opção pela chave_viagem, não pela posição
                # da linha — trocar o filtro de transportadora/mês na
                # lateral não pode fazer esse combo continuar "selecionado"
                # numa posição que agora aponta pra viagem de outra
                # transportadora.
                mapa_ver_anexo = {detalhe.loc[idx, "chave_viagem"]: idx for idx in com_anexo}
                chave_anexo = st.selectbox(
                    "Viagem",
                    options=list(mapa_ver_anexo.keys()),
                    format_func=lambda chave_v: (
                        f"{detalhe.loc[mapa_ver_anexo[chave_v], 'ID Viagem']} — "
                        f"{detalhe.loc[mapa_ver_anexo[chave_v], 'Anexo']}"
                    ),
                    key=f"ver_anexo_sel_{key_sufixo}",
                )
                try:
                    lista_anexos = listar_anexos(chave_anexo)
                except Exception as e:
                    lista_anexos = []
                    st.error(f"Falha ao carregar a lista de anexos: {e}")
                if not lista_anexos:
                    st.caption("Nenhum anexo encontrado pra essa viagem.")
                # Galeria em grade de 3 colunas (imagens menores, não mais
                # uma por linha em tamanho cheio) — quebra a lista em
                # grupos de 3 e desenha uma linha de colunas por grupo.
                COLUNAS_GALERIA = 3
                for inicio in range(0, len(lista_anexos), COLUNAS_GALERIA):
                    grupo = lista_anexos[inicio:inicio + COLUNAS_GALERIA]
                    colunas_grade = st.columns(COLUNAS_GALERIA)
                    for coluna, item in zip(colunas_grade, grupo):
                        with coluna:
                            st.caption(item["nome"])
                            try:
                                resultado_anexo = (
                                    get_anexo(chave_anexo) if item["id"] is None else get_anexo_por_id(item["id"])
                                )
                            except Exception as e:
                                resultado_anexo = None
                                st.error(f"Falha ao carregar: {e}")
                            if resultado_anexo:
                                nome_anexo, bytes_anexo = resultado_anexo
                                if Path(nome_anexo).suffix.lower() in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
                                    st.image(bytes_anexo, width="stretch")
                                else:
                                    st.caption("Sem pré-visualização — use o botão abaixo.")
                                st.download_button(
                                    "Baixar",
                                    data=bytes_anexo,
                                    file_name=nome_anexo,
                                    key=f"ver_anexo_botao_{key_sufixo}_{item['id']}",
                                )
                            else:
                                st.error("Anexo não encontrado no banco.")

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
                try:
                    reprovar_justificativa(chave, user["username"])
                except Exception as e:
                    st.error(f"Falha ao reprovar: {e}")
                else:
                    st.warning(f"Justificativa de {detalhe.loc[idx, 'ID Viagem']} reprovada.", icon="🚫")
                    st.rerun()
            elif decisao_nova == "Aprovado":
                st.session_state[f"aprovando_{chave_id}"] = True
            elif decisao_nova == "Excluir":
                st.session_state[f"excluindo_{chave_id}"] = True
            elif decisao_nova == "Pendente":
                st.session_state.pop(f"aprovando_{chave_id}", None)
                st.session_state.pop(f"excluindo_{chave_id}", None)

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
                observacao = st.text_area(
                    "Observação (opcional)", key=f"obs_{key_sufixo}_{chave_id}"
                )
                confirmar = st.form_submit_button("Confirmar aprovação")
            if confirmar:
                try:
                    aprovar_justificativa(chave, categoria, user["username"], observacao.strip())
                except Exception as e:
                    st.error(f"Falha ao aprovar: {e}")
                else:
                    st.session_state.pop(f"aprovando_{chave_id}", None)
                    st.success("Justificativa aprovada.", icon="✅")
                    st.rerun()

        excluindo_categoria = [
            idx for idx in detalhe.index
            if st.session_state.get(f"excluindo_{detalhe.loc[idx, 'chave_viagem'].replace('|', '_').replace('/', '-')}")
        ]
        for idx in excluindo_categoria:
            chave = detalhe.loc[idx, "chave_viagem"]
            chave_id = chave.replace("|", "_").replace("/", "-")
            st.error(
                f"Excluir de vez a justificativa/anexo de {detalhe.loc[idx, 'ID Viagem']}? "
                "Isso não pode ser desfeito.",
                icon="🗑️",
            )
            col_confirma, col_cancela = st.columns(2)
            if col_confirma.button("Confirmar exclusão", key=f"excluir_confirma_{key_sufixo}_{chave_id}"):
                try:
                    excluir_justificativa(chave)
                except Exception as e:
                    st.error(f"Falha ao excluir: {e}")
                else:
                    st.session_state.pop(f"excluindo_{chave_id}", None)
                    st.success("Registro excluído.", icon="🗑️")
                    st.rerun()
            if col_cancela.button("Cancelar", key=f"excluir_cancela_{key_sufixo}_{chave_id}"):
                st.session_state.pop(f"excluindo_{chave_id}", None)
                st.rerun()


def render_notificacao_reprovacao(user: dict) -> None:
    if not TURSO_DISPONIVEL:
        return
    try:
        chaves = chaves_reprovadas(user["transportadora"])
    except Exception:
        return
    if chaves:
        st.error(
            f"⚠️ {len(chaves)} justificativa(s) sua(s) foram reprovadas pelo admin. "
            "Refaça a justificativa e/ou o anexo nas tabelas abaixo para que a notificação suma.",
            icon="🚫",
        )


CORTE_JUSTIFICATIVA = pd.Timestamp("2026-07-01")


def resumo_justificativa(detalhe: pd.DataFrame) -> dict:
    # Viagens anteriores a 01/07/2026 contam como já justificadas
    # (independente de terem justificativa escrita ou não) — só a partir
    # dessa data que a pendência de fato é cobrada.
    if detalhe.empty:
        return {"total": 0, "justificado": 0, "pendente": 0}
    chaves = detalhe["chave_viagem"].tolist()
    try:
        justificativas = get_justificativas(chaves) if TURSO_DISPONIVEL else {}
    except Exception:
        justificativas = {}
    tem_justificativa = detalhe["chave_viagem"].map(
        lambda k: bool(justificativas.get(k, {}).get("justificativa", ""))
    )
    dentro_do_escopo = detalhe["Data"] >= CORTE_JUSTIFICATIVA
    pendente = int((dentro_do_escopo & ~tem_justificativa).sum())
    total = len(detalhe)
    return {"total": total, "justificado": total - pendente, "pendente": pendente}


def render_resumo_categoria(detalhe: pd.DataFrame) -> None:
    info = resumo_justificativa(detalhe)
    col1, col2, col3 = st.columns(3)
    col1.metric("Total", milhar_str(info["total"]))
    col2.metric("Justificado", milhar_str(info["justificado"]))
    col3.metric("Pendente", milhar_str(info["pendente"]))


def render_tabelas_fixas(df: pd.DataFrame, user: dict) -> None:
    # Usado tanto pra transportadora quanto pro admin: garante acesso direto
    # às 3 dimensões de atraso (saída/chegada/transit), sem depender de
    # navegar bar a bar no gráfico — o que pode "esconder" justificativas
    # quando a Aba Principal e a aba Saída real divergem sobre uma viagem
    # específica (uma diz que atrasou, a outra diz que não).
    detalhe_saida, colunas_saida = detalhe_categoria(df, "saida")
    detalhe_chegada, colunas_chegada = detalhe_categoria(df, "chegada")
    detalhe_transit, colunas_transit = detalhe_categoria(df, "transit")

    st.markdown("###### DETALHAMENTO DE JUSTIFICATIVAS DE ATRASOS")

    if user["role"] in ("admin", "interno"):
        # No admin/interno, as 3 viram abas clicáveis em vez de empilhadas —
        # menos rolagem pra achar a que interessa. O resumo (Total/
        # Justificado/Pendente) fica dentro de cada aba, então mostra só os
        # números da tabela que está selecionada no momento.
        aba_saida, aba_chegada, aba_transit = st.tabs(
            ["Detalhe Atraso Saída", "Detalhe Atraso Chegada", "Detalhe Atraso Transit time"]
        )
        with aba_saida:
            render_resumo_categoria(detalhe_saida)
            render_tabela_detalhe(detalhe_saida, colunas_saida, user, "Detalhe Atraso Saída", "fixo_saida", mostrar_titulo=False)
        with aba_chegada:
            render_resumo_categoria(detalhe_chegada)
            render_tabela_detalhe(detalhe_chegada, colunas_chegada, user, "Detalhe Atraso Chegada", "fixo_chegada", mostrar_titulo=False)
        with aba_transit:
            render_resumo_categoria(detalhe_transit)
            render_tabela_detalhe(detalhe_transit, colunas_transit, user, "Detalhe Atraso Transit time", "fixo_transit", mostrar_titulo=False)
    else:
        # Transportadora vê as 3 tabelas empilhadas — o resumo fica logo
        # acima de cada tabela respectiva, não um bloco único no topo.
        render_resumo_categoria(detalhe_saida)
        render_tabela_detalhe(detalhe_saida, colunas_saida, user, "Detalhe Atraso Saída", "fixo_saida")
        render_resumo_categoria(detalhe_chegada)
        render_tabela_detalhe(detalhe_chegada, colunas_chegada, user, "Detalhe Atraso Chegada", "fixo_chegada")
        render_resumo_categoria(detalhe_transit)
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


def render_motoristas_ofensores(df: pd.DataFrame) -> None:
    tabela = motoristas_ofensores(df)
    if tabela.empty:
        st.info("Sem ocorrências de atraso (responsabilidade da transportadora) no período filtrado.")
        return
    tabela = tabela.copy()
    tabela["Quantidade"] = tabela["Quantidade"].astype(int)
    st.dataframe(
        tabela,
        width="stretch",
        hide_index=True,
        height=350,
        column_config={"Quantidade": st.column_config.NumberColumn(format="%d")},
    )


def render_gerenciar_senhas() -> None:
    with st.sidebar.expander("Gerenciar senhas de transportadoras"):
        try:
            usuarios = list_transportadora_users()
        except Exception as e:
            st.error(f"Falha ao carregar contas: {e}")
            return
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
                try:
                    nova_senha = reset_transportadora_password(usuario_selecionado["username"])
                except Exception as e:
                    st.error(f"Falha ao gerar nova senha: {e}")
                else:
                    st.success(f"Nova senha para `{usuario_selecionado['username']}`: `{nova_senha}`")
        st.caption(
            "\"Ver senha padrão\" só calcula (não altera nada). \"Gerar nova senha\" troca de "
            "verdade — se o banco for zerado num redeploy, a conta volta com a senha padrão."
        )

        st.caption("E-mail cadastrado (usado para a transportadora trocar a própria senha).")
        novo_email = st.text_input(
            "E-mail",
            value=email_atual(usuario_selecionado["username"]),
            key=f"email_transp_{usuario_selecionado['username']}",
        )
        if st.button("Salvar e-mail", key="salvar_email_transp_botao"):
            if definir_email(usuario_selecionado["username"], novo_email):
                st.success("E-mail atualizado.")
                st.rerun()
            else:
                st.error("Não foi possível salvar o e-mail agora (banco persistente indisponível).")

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
            "Essas contas são criadas uma única vez (senha padrão determinística) e "
            "depois persistem — o botão abaixo só cria as que ainda faltarem."
        )
        if st.button("Criar contas padrão da lista", key="seed_internos_botao"):
            try:
                novos = ensure_usuarios_internos()
            except Exception as e:
                st.error(f"Falha ao criar contas: {e}")
                novos = []
            if novos:
                st.success(f"{len(novos)} conta(s) criada(s):")
                for reg in novos:
                    st.code(f"{reg['nome']} — usuário: {reg['usuario']} — senha: {reg['senha']} — {reg['role']}")
                st.caption("Copie agora — essas senhas não ficam salvas em nenhuma tela depois de sair daqui.")
            else:
                st.info("Todas as contas da lista padrão já existem.")

        st.divider()
        try:
            usuarios = list_internal_users()
        except Exception as e:
            st.error(f"Falha ao carregar contas: {e}")
            usuarios = []
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
                    try:
                        nova_senha = reset_user_password(usuario_selecionado["username"])
                    except Exception as e:
                        st.error(f"Falha ao gerar nova senha: {e}")
                    else:
                        st.success(f"Nova senha para `{usuario_selecionado['username']}`: `{nova_senha}`")
            st.caption(
                "\"Ver senha padrão\" só calcula (não altera nada). \"Gerar nova senha\" troca de "
                "verdade — se o banco for zerado num redeploy, a conta volta com a senha padrão."
            )

            novo_email = st.text_input(
                "E-mail",
                value=email_atual(usuario_selecionado["username"]),
                key=f"email_interno_{usuario_selecionado['username']}",
            )
            if st.button("Salvar e-mail", key="salvar_email_interno_botao"):
                if definir_email(usuario_selecionado["username"], novo_email):
                    st.success("E-mail atualizado.")
                    st.rerun()
                else:
                    st.error("Não foi possível salvar o e-mail agora (banco persistente indisponível).")

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
                try:
                    registro = criar_acesso_interno(nome_novo.strip(), role, email_novo)
                except Exception as e:
                    st.error(f"Falha ao criar acesso: {e}")
                else:
                    if email_novo.strip():
                        definir_email(registro["usuario"], email_novo)
                    st.success(
                        f"Conta criada — usuário: `{registro['usuario']}` — senha: `{registro['senha']}`"
                    )
                    st.caption("Copie agora — essa senha não fica salva em nenhuma tela depois de sair daqui.")


def render_alterar_perfil(user: dict) -> None:
    with st.sidebar.expander("Alterar perfil de usuário"):
        st.caption(
            "Troca o perfil (transportadora, interno ou admin) de uma conta já "
            "existente. Por segurança, só aplica com a senha padrão do admin "
            "gerada hoje — a mesma exibida em \"Gerenciar senhas\"/\"Gerenciar "
            "acessos internos\"."
        )
        try:
            usuarios = list_all_users()
        except Exception as e:
            st.error(f"Falha ao carregar contas: {e}")
            return
        if not usuarios:
            st.caption("Nenhuma conta encontrada.")
            return

        opcoes = {
            f"{u['username']} ({u['role']}" + (f" — {u['transportadora']})" if u["transportadora"] else ")"): u
            for u in usuarios
        }
        escolha_label = st.selectbox("Conta", list(opcoes.keys()), key="alterar_perfil_sel")
        usuario_selecionado = opcoes[escolha_label]

        papel_atual_idx = {"transportadora": 0, "interno": 1, "admin": 2}.get(usuario_selecionado["role"], 0)
        novo_papel_label = st.selectbox(
            "Novo perfil",
            ["Transportadora", "Interno", "Admin"],
            index=papel_atual_idx,
            key="alterar_perfil_novo_role",
        )
        novo_role = novo_papel_label.lower()

        nova_transportadora = None
        if novo_role == "transportadora":
            nomes_transportadoras = load_transportadoras()
            idx_atual = (
                nomes_transportadoras.index(usuario_selecionado["transportadora"])
                if usuario_selecionado["transportadora"] in nomes_transportadoras
                else 0
            )
            nova_transportadora = st.selectbox(
                "Transportadora vinculada",
                nomes_transportadoras,
                index=idx_atual,
                key="alterar_perfil_transportadora",
            )

        senha_confirma = st.text_input(
            "Senha padrão do admin (hoje)", type="password", key="alterar_perfil_senha_confirma"
        )

        if usuario_selecionado["username"] == user["username"] and novo_role != "admin":
            st.warning(
                "Você não pode remover o próprio acesso admin por aqui (evita ficar "
                "sem acesso). Peça pra outro admin fazer essa troca.",
                icon="⚠️",
            )
        elif st.button("Aplicar alteração de perfil", key="alterar_perfil_botao"):
            if senha_confirma != senha_padrao("admin", 14):
                st.error("Senha padrão do admin incorreta.")
            elif novo_role == "transportadora" and not nova_transportadora:
                st.error("Selecione a transportadora vinculada.")
            else:
                try:
                    set_user_role(usuario_selecionado["username"], novo_role, nova_transportadora)
                except Exception as e:
                    st.error(f"Falha ao alterar perfil: {e}")
                else:
                    st.success(
                        f"Perfil de `{usuario_selecionado['username']}` alterado para `{novo_role}`."
                    )
                    for chave in (
                        "alterar_perfil_sel",
                        "alterar_perfil_novo_role",
                        "alterar_perfil_transportadora",
                        "alterar_perfil_senha_confirma",
                    ):
                        st.session_state.pop(chave, None)
                    st.rerun()


def render_alterar_senha(user: dict) -> None:
    with st.sidebar.expander("Alterar minha senha"):
        email_cadastrado = (user.get("email") or "").strip().lower()
        if not email_cadastrado:
            if not TURSO_DISPONIVEL:
                st.caption(
                    "Cadastro de e-mail indisponível no momento (banco persistente fora "
                    "do ar) — tente novamente mais tarde."
                )
                return
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
                elif not definir_email(user["username"], novo_email_cadastro):
                    st.error("Não foi possível salvar o e-mail agora. Tente de novo.")
                else:
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
        render_alterar_perfil(user)

    render_alterar_senha(user)

    colors = chart_colors(get_theme_mode())

    if user["role"] in ("admin", "interno"):
        mapa_abrev = transportadora_abreviatura_map(df)
        nomes_disponiveis = sorted(df["transportadora"].dropna().unique().tolist())
        opcoes_rotulo = {"Todas": "Todas"}
        for nome in nomes_disponiveis:
            abrev = mapa_abrev.get(nome)
            rotulo = f"({abrev}) - {nome}" if abrev else nome
            opcoes_rotulo[rotulo] = nome
        escolha_rotulo = st.sidebar.selectbox("Transportadora", list(opcoes_rotulo.keys()))
        selecionada = opcoes_rotulo[escolha_rotulo]
        if selecionada != "Todas":
            df = df[df["transportadora"] == selecionada]
            titulo = selecionada
        else:
            titulo = "Geral das Transportadoras Parceiras"
    else:
        df = df[df["transportadora"] == user["transportadora"]]
        titulo = user["transportadora"] or "Transportadora"

    anos_disponiveis = sorted(df["ano"].dropna().unique().tolist())
    if anos_disponiveis:
        anos_selecionados = st.sidebar.multiselect("Ano", anos_disponiveis, default=anos_disponiveis)
        if anos_selecionados:
            df = df[df["ano"].isin(anos_selecionados)]

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

    regionais_disponiveis = sorted(df["regional"].dropna().unique().tolist())
    if regionais_disponiveis:
        regionais_selecionadas = st.sidebar.multiselect("Regional", regionais_disponiveis, default=regionais_disponiveis)
        if regionais_selecionadas:
            df = df[df["regional"].isin(regionais_selecionadas)]

    origens_disponiveis = sorted(df["origem"].dropna().unique().tolist())
    if origens_disponiveis:
        origens_selecionadas = st.sidebar.multiselect("Origem", origens_disponiveis, default=origens_disponiveis)
        if origens_selecionadas:
            df = df[df["origem"].isin(origens_selecionadas)]

    destinos_disponiveis = sorted(df["destino"].dropna().unique().tolist())
    if destinos_disponiveis:
        destinos_selecionados = st.sidebar.multiselect("Destino", destinos_disponiveis, default=destinos_disponiveis)
        if destinos_selecionados:
            df = df[df["destino"].isin(destinos_selecionados)]

    if st.sidebar.button("Sair"):
        del st.session_state["user"]
        st.rerun()

    render_hero(titulo, df)

    if user["role"] == "transportadora":
        render_notificacao_reprovacao(user)

    # Cross-filter por clique: clicar numa barra (Regional/Motivos) ou numa
    # linha do Ranking filtra os outros gráficos/tabelas da seção analítica
    # (não afeta as 3 tabelas fixas de justificativa, que seguem só os
    # filtros da lateral — clicar num gráfico não pode esconder uma viagem
    # que ainda precisa ser justificada). O gráfico/tabela que originou o
    # clique continua mostrando tudo (com o item clicado destacado), só os
    # outros ficam restritos — senão sumiriam as outras barras/linhas e
    # ficaria impossível trocar a seleção.
    regional_clicada = _campo_clicado("chart_regional", "sel_regional", "regional")
    motivo_clicado = _campo_clicado("chart_motivos", "sel_motivo", "motivo")
    transportadora_clicada = None
    linha_ranking = _linha_selecionada("tabela_ranking")
    if linha_ranking is not None:
        ranking_atual = ranking_transportadoras(df).reset_index(drop=True)
        if linha_ranking < len(ranking_atual):
            transportadora_clicada = ranking_atual.iloc[linha_ranking]["abreviatura"]

    def com_filtro_clique(base_df: pd.DataFrame, excluir: str | None = None) -> pd.DataFrame:
        resultado = base_df
        if regional_clicada and excluir != "regional":
            resultado = resultado[resultado["regional"] == regional_clicada]
        if motivo_clicado and excluir != "motivo":
            resultado = resultado[resultado["motivo_atraso_chegada"] == motivo_clicado]
        if transportadora_clicada and excluir != "transportadora":
            resultado = resultado[resultado["abreviatura"] == transportadora_clicada]
        return resultado

    if regional_clicada or motivo_clicado or transportadora_clicada:
        chips = [
            texto
            for ativo, texto in [
                (regional_clicada, f"Regional: {regional_clicada}"),
                (motivo_clicado, f"Motivo: {motivo_clicado}"),
                (transportadora_clicada, f"Transportadora: {transportadora_clicada}"),
            ]
            if ativo
        ]
        col_info, col_botao = st.columns([4, 1])
        col_info.info("Filtro por clique ativo — " + " | ".join(chips), icon="🔎")
        if col_botao.button("Limpar seleção", width="stretch"):
            for chave in ("chart_regional", "chart_motivos", "tabela_ranking"):
                st.session_state.pop(chave, None)
            st.rerun()

    st.divider()
    ALTURA_PAR_GRAFICOS = 380
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Evolução mensal do SLA")
        with st.container(height=ALTURA_PAR_GRAFICOS, border=False):
            render_monthly_chart(com_filtro_clique(df), colors)
    with col2:
        st.subheader("Principais motivos de atraso")
        with st.container(height=ALTURA_PAR_GRAFICOS, border=False):
            render_motivos_chart(com_filtro_clique(df, excluir="motivo"), colors)

    st.divider()
    if user["role"] in ("admin", "interno"):
        col3, col4 = st.columns(2)
        with col3:
            st.subheader("Viagens por regional")
            with st.container(height=ALTURA_PAR_GRAFICOS, border=False):
                render_regional_chart(com_filtro_clique(df, excluir="regional"), colors)
        with col4:
            st.subheader("Ranking de transportadoras")
            with st.container(height=ALTURA_PAR_GRAFICOS, border=False):
                render_ranking(com_filtro_clique(df, excluir="transportadora"))
        st.divider()

    st.subheader("Viagens")
    render_table(com_filtro_clique(df))

    st.divider()
    st.subheader("Motoristas ofensores")
    render_motoristas_ofensores(com_filtro_clique(df))

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
