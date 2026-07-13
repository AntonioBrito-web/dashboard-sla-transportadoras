import re
import urllib.parse

import pandas as pd

from src.config import CSV_URL, MESES_PT, SHEET_ID

_CJK_PATTERN = re.compile(r"[　-〿㐀-䶿一-鿿豈-﫿＀-￯]+")


def _strip_cjk(series: pd.Series) -> pd.Series:
    return (
        series.astype(str)
        .str.replace(_CJK_PATTERN, "", regex=True)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip(" -")
    )


# Grafias divergentes na planilha de origem que representam a mesma empresa
# real — consolidadas sob um único nome canônico (confirmado com o usuário).
TRANSPORTADORA_CANONICA = {
    "FSÃO JUDAS": "FSAO JUDAS",
    "J&amp;T EXPRESS BRAZIL LTDA": "J&T EXPRESS BRAZIL LTDA",
    "JET EXPRESS (YUNYI)": "J&T EXPRESS BRAZIL LTDA",
}

# Abreviação a exibir para o nome canônico acima, sobrepondo o cálculo por
# moda (que puxaria "YUNYI" por volume, já que a JET EXPRESS tinha mais
# viagens que a J&T EXPRESS BRAZIL LTDA antes da fusão).
ABREVIATURA_CANONICA = {
    "J&T EXPRESS BRAZIL LTDA": "JET",
}

# Aba complementar com o status de saída mais confiável (a aba "Chegada
# Real" existe na planilha mas está praticamente vazia — não é usada).
SHEET_SAIDA_REAL = "Saída real 实际发车"

# Categorias de responsabilidade já usadas pela própria planilha na aba
# Saída real — mesmas 3 categorias usadas no fluxo de aprovação do admin.
RESPONSABILIDADE_MOTIVO_SAIDA = {
    "Operações 运营 SC/DC": "Atraso saída - Operações",
    "Transp 运输公司": "Atraso saída - Transportadora",
    "Incontrolável 不可控因素": "Atraso saída - Incontrolável",
}

COL_DATA = "Data"
COL_ID_VIAGEM = "ID Viagem"
COL_STATUS = "Status"
COL_TRANSPORTADORA = "Transportadora"
COL_MOTORISTA = "Motorista 1"
COL_PLACA = "Placa do carro"
COL_MODELO_VEICULO = "Nome de modelo de veículo"
COL_NUMERO_LINHA = "Nome de linha"
COL_SECAO_ESTRADA = "Seção da estrada"
COL_ORIGEM = "ORIGEM 2"
COL_DESTINO = "DESTINO"
COL_REGIONAL = "Regional"
COL_PLAN_SAIDA = "Horário planejado de saída"
COL_REAL_SAIDA = "Horário real de saída"
COL_STATUS_SAIDA = "Status saída"
COL_MOTIVO_SAIDA = "Motivo de atraso saída"
COL_DESC_OCORRENCIA_SAIDA = "Descrição detalhada da ocorrência de saída"
COL_PLAN_CHEGADA = "Tempo de chegada planejado"
COL_PREVISTO_CHEGADA = "Horário previsto de chegada"
COL_REAL_CHEGADA = "Tempo real de chegada"
COL_STATUS_CHEGADA = "Status chegada"
COL_MOTIVO_CHEGADA_DETALHE = "Motivo do atraso chegada (motivo menor)"
COL_STATUS_TRANSIT = "Status transit time"
COL_MOTIVO_TRANSIT = "Motivo do atraso transit time (motivo maior)"
COL_RESPONSABILIDADE_CHEGADA = "Responsabilidade chegada"
COL_RESPONSABILIDADE_TRANSIT = "Responsabilidade transit time"
COL_KM = "Quilometragem"
COL_VALOR_MULTA = "Valor da multa"
COL_MES = "mês"
COL_FAIXA_ATRASO = "Faixa de atraso"


def _motivo_chegada_geral_col(columns) -> str:
    for c in columns:
        if c.startswith("Motivo do atraso chegada") and "menor" not in c:
            return c
    return COL_MOTIVO_CHEGADA_DETALHE


def _abreviatura_col(columns) -> str | None:
    for c in columns:
        if c.startswith("Abreviatura de transportador"):
            return c
    return None


def eh_motivo_saida(motivo) -> bool:
    if pd.isna(motivo):
        return False
    motivo_lower = str(motivo).lower()
    return "saída" in motivo_lower or "saida" in motivo_lower


def _eh_responsabilidade_transportadora(serie: pd.Series) -> pd.Series:
    # As colunas de responsabilidade (saída/chegada/transit) usam o mesmo
    # padrão de texto "Transp 运输公司" / "Incontrolável 不可控因素" /
    # "Operações 运营 SC/DC" (com pequenas variações de espaçamento na
    # planilha) — checar só o prefixo "transp" cobre isso sem depender de
    # bater a string inteira.
    return serie.astype(str).str.strip().str.lower().str.startswith("transp")


def fetch_saida_real_dataframe() -> pd.DataFrame:
    url = (
        f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv&sheet="
        + urllib.parse.quote(SHEET_SAIDA_REAL)
    )
    return pd.read_csv(url, low_memory=False)


def _mapa_responsabilidade_saida() -> dict:
    # Chave só com ID viagem + Seção da estrada (sem a Data): a coluna
    # "Data" tem um deslocamento de até 1 dia entre as duas abas para a
    # mesma viagem (a Saída real registra a data de partida, que em
    # viagens noturnas cai um dia antes da data-referência da Aba
    # Principal), então incluir a data derrubava a taxa de correspondência
    # de ~99% para ~30%. ID+Seção sozinhos já são praticamente únicos nas
    # duas abas (~99,97%).
    df = fetch_saida_real_dataframe()
    chave = df["ID viagem"].astype(str) + "|" + df["Seção da estrada"].astype(str)
    responsabilidade = df["Responsabilidade"].map(RESPONSABILIDADE_MOTIVO_SAIDA)
    serie = pd.Series(responsabilidade.values, index=chave).dropna()
    return serie[~serie.index.duplicated(keep="first")].to_dict()


def _to_float_br(series: pd.Series) -> pd.Series:
    cleaned = (
        series.astype(str)
        .str.replace("%", "", regex=False)
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
        .str.strip()
    )
    return pd.to_numeric(cleaned, errors="coerce")


def fetch_raw_dataframe() -> pd.DataFrame:
    return pd.read_csv(CSV_URL, low_memory=False)


def clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    motivo_chegada_col = _motivo_chegada_geral_col(df.columns)

    out = pd.DataFrame()
    out["data"] = pd.to_datetime(df[COL_DATA], format="%Y/%m/%d", errors="coerce")
    out["id_viagem"] = df[COL_ID_VIAGEM]
    out["status"] = _strip_cjk(df[COL_STATUS])
    out["transportadora"] = df[COL_TRANSPORTADORA].astype(str).str.strip().replace(TRANSPORTADORA_CANONICA)
    abrev_col = _abreviatura_col(df.columns)
    out["abreviatura"] = _strip_cjk(df[abrev_col]) if abrev_col else out["transportadora"]
    out["abreviatura"] = out["transportadora"].map(ABREVIATURA_CANONICA).fillna(out["abreviatura"])
    out["motorista"] = df[COL_MOTORISTA]
    out["placa"] = df[COL_PLACA]
    out["modelo_veiculo"] = df.get(COL_MODELO_VEICULO)
    out["numero_linha"] = df.get(COL_NUMERO_LINHA)
    out["secao_estrada"] = df.get(COL_SECAO_ESTRADA)
    out["origem"] = df.get(COL_ORIGEM, df.get("Origem"))
    out["destino"] = df.get(COL_DESTINO)
    out["regional"] = df[COL_REGIONAL]

    out["planejado_saida"] = pd.to_datetime(df[COL_PLAN_SAIDA], dayfirst=True, errors="coerce")
    out["real_saida"] = pd.to_datetime(df[COL_REAL_SAIDA], dayfirst=True, errors="coerce")
    status_saida_raw = df[COL_STATUS_SAIDA].astype(str)
    out["no_prazo_saida"] = status_saida_raw.str.contains("No prazo", case=False, na=False)
    out["fora_prazo_saida"] = status_saida_raw.str.contains("Fora do prazo", case=False, na=False)
    out["motivo_atraso_saida"] = df[COL_MOTIVO_SAIDA]
    out["descricao_ocorrencia_saida"] = _strip_cjk(df[COL_DESC_OCORRENCIA_SAIDA]) if COL_DESC_OCORRENCIA_SAIDA in df.columns else pd.NA

    out["planejado_chegada"] = pd.to_datetime(df[COL_PLAN_CHEGADA], dayfirst=True, errors="coerce")
    out["previsto_chegada"] = pd.to_datetime(df.get(COL_PREVISTO_CHEGADA), dayfirst=True, errors="coerce")
    out["real_chegada"] = pd.to_datetime(df[COL_REAL_CHEGADA], dayfirst=True, errors="coerce")
    status_chegada_raw = df[COL_STATUS_CHEGADA].astype(str)
    out["no_prazo_chegada"] = status_chegada_raw.str.contains("No prazo", case=False, na=False)
    out["fora_prazo_chegada"] = status_chegada_raw.str.contains("Fora do prazo", case=False, na=False)
    out["status_chegada"] = _strip_cjk(df[COL_STATUS_CHEGADA])
    out["motivo_atraso_chegada"] = _strip_cjk(df[motivo_chegada_col])
    out.loc[df[motivo_chegada_col].isna(), "motivo_atraso_chegada"] = pd.NA
    out["motivo_chegada_menor"] = _strip_cjk(df[COL_MOTIVO_CHEGADA_DETALHE]) if COL_MOTIVO_CHEGADA_DETALHE in df.columns else pd.NA
    out.loc[df[COL_MOTIVO_CHEGADA_DETALHE].isna(), "motivo_chegada_menor"] = pd.NA
    out["responsabilidade_chegada"] = df.get(COL_RESPONSABILIDADE_CHEGADA)

    status_transit_raw = df.get(COL_STATUS_TRANSIT, pd.Series(dtype=str)).astype(str)
    out["no_prazo_transit"] = status_transit_raw.str.contains("No prazo", case=False, na=False)
    out["fora_prazo_transit"] = status_transit_raw.str.contains("Fora do prazo", case=False, na=False)
    out["status_transit"] = (
        _strip_cjk(df[COL_STATUS_TRANSIT]) if COL_STATUS_TRANSIT in df.columns else pd.NA
    )
    out["motivo_transit"] = _strip_cjk(df[COL_MOTIVO_TRANSIT]) if COL_MOTIVO_TRANSIT in df.columns else pd.NA
    if COL_MOTIVO_TRANSIT in df.columns:
        out.loc[df[COL_MOTIVO_TRANSIT].isna(), "motivo_transit"] = pd.NA
    out["responsabilidade_transit"] = df.get(COL_RESPONSABILIDADE_TRANSIT)

    out["km"] = _to_float_br(df[COL_KM]) if COL_KM in df.columns else pd.NA
    out["valor_multa"] = _to_float_br(df[COL_VALOR_MULTA]) if COL_VALOR_MULTA in df.columns else pd.NA
    # TT planejado/real reaproveitam o horário previsto/real de chegada (por
    # pedido do usuário) — os campos originais "TT PLANEJADO"/"TT REAL" da
    # planilha não são mais usados aqui.
    out["tt_planejado"] = out["previsto_chegada"]
    out["tt_real"] = out["real_chegada"]
    out["faixa_atraso"] = df.get(COL_FAIXA_ATRASO)

    mes_num = pd.to_numeric(df[COL_MES], errors="coerce")
    out["mes"] = mes_num
    out["mes_nome"] = mes_num.map(MESES_PT)
    out["ano"] = out["data"].dt.year

    out["quinzena"] = out["data"].dt.day.map(lambda d: "1ª quinzena" if pd.notna(d) and d <= 15 else ("2ª quinzena" if pd.notna(d) else pd.NA))

    out["concluido"] = out["status"].str.contains("Conclu", case=False, na=False)

    # Chave estável por viagem (id viagem + data + seção da estrada) — usada
    # para vincular justificativas/anexos escritos pelas transportadoras.
    out["chave_viagem"] = (
        out["id_viagem"].astype(str)
        + "|"
        + out["data"].dt.strftime("%Y-%m-%d").fillna("")
        + "|"
        + out["secao_estrada"].astype(str)
    )

    # Substitui os motivos de atraso de saída (que na Aba Principal vêm só
    # como texto genérico "Resp. Operação"/"Resp.Transp.") pela
    # responsabilidade mais precisa da aba Saída real, quando disponível.
    chave_id_secao = out["id_viagem"].astype(str) + "|" + out["secao_estrada"].astype(str)
    mapa_resp_saida = _mapa_responsabilidade_saida()
    out["responsabilidade_saida_real"] = chave_id_secao.map(mapa_resp_saida)
    mask_saida = out["motivo_atraso_chegada"].apply(eh_motivo_saida) & out["responsabilidade_saida_real"].notna()
    out.loc[mask_saida, "motivo_atraso_chegada"] = out.loc[mask_saida, "responsabilidade_saida_real"]

    return out


def load_transportadoras() -> list[str]:
    df = fetch_raw_dataframe()
    valores = df[COL_TRANSPORTADORA].dropna().astype(str).str.strip().replace(TRANSPORTADORA_CANONICA)
    valores = valores[valores != ""]
    return sorted(valores.unique().tolist())


def load_transportadoras_com_abreviatura() -> dict:
    # Leve de propósito (sem parse de datas nem a mesclagem com a aba Saída
    # real) — usada só para padronizar nomes de usuário, não pro dashboard.
    df = fetch_raw_dataframe()
    transportadora = df[COL_TRANSPORTADORA].astype(str).str.strip().replace(TRANSPORTADORA_CANONICA)
    abrev_col = _abreviatura_col(df.columns)
    abreviatura = _strip_cjk(df[abrev_col]) if abrev_col else transportadora
    abreviatura = transportadora.map(ABREVIATURA_CANONICA).fillna(abreviatura)
    tmp = pd.DataFrame({"transportadora": transportadora, "abreviatura": abreviatura})
    tmp = tmp[(tmp["transportadora"] != "") & tmp["abreviatura"].notna() & (tmp["abreviatura"] != "")]
    moda = tmp.groupby("transportadora")["abreviatura"].agg(lambda s: s.value_counts().idxmax())
    return moda.to_dict()


def compute_kpis(df: pd.DataFrame) -> dict:
    total = len(df)
    com_saida = df["no_prazo_saida"].sum() + df["fora_prazo_saida"].sum()
    com_chegada = df["no_prazo_chegada"].sum() + df["fora_prazo_chegada"].sum()
    return {
        "total_viagens": total,
        "pct_no_prazo_saida": (df["no_prazo_saida"].sum() / com_saida * 100) if com_saida else 0.0,
        "pct_no_prazo_chegada": (df["no_prazo_chegada"].sum() / com_chegada * 100) if com_chegada else 0.0,
        "qtd_fora_prazo_chegada": int(df["fora_prazo_chegada"].sum()),
        "valor_total_multa": float(df["valor_multa"].sum(skipna=True)) if "valor_multa" in df else 0.0,
        "km_total": float(df["km"].sum(skipna=True)) if "km" in df else 0.0,
    }


def monthly_sla(df: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        df.dropna(subset=["mes"])
        .groupby(["mes", "mes_nome"], as_index=False)
        .agg(
            viagens=("id_viagem", "count"),
            no_prazo_saida=("no_prazo_saida", "sum"),
            fora_prazo_saida=("fora_prazo_saida", "sum"),
            no_prazo_chegada=("no_prazo_chegada", "sum"),
            fora_prazo_chegada=("fora_prazo_chegada", "sum"),
        )
        .sort_values("mes")
    )
    grouped["pct_no_prazo_saida"] = (
        grouped["no_prazo_saida"] / (grouped["no_prazo_saida"] + grouped["fora_prazo_saida"]).replace(0, pd.NA) * 100
    )
    grouped["pct_no_prazo_chegada"] = (
        grouped["no_prazo_chegada"] / (grouped["no_prazo_chegada"] + grouped["fora_prazo_chegada"]).replace(0, pd.NA) * 100
    )
    return grouped


def transportadora_abreviatura_map(df: pd.DataFrame) -> dict:
    moda = (
        df.dropna(subset=["abreviatura"])
        .groupby("transportadora")["abreviatura"]
        .agg(lambda s: s.value_counts().idxmax())
    )
    return moda.to_dict()


def ranking_transportadoras(df: pd.DataFrame) -> pd.DataFrame:
    grouped = df.groupby("transportadora", as_index=False).agg(
        viagens=("id_viagem", "count"),
        no_prazo_chegada=("no_prazo_chegada", "sum"),
        fora_prazo_chegada=("fora_prazo_chegada", "sum"),
        valor_multa=("valor_multa", "sum"),
    )
    denom = (grouped["no_prazo_chegada"] + grouped["fora_prazo_chegada"]).replace(0, pd.NA)
    grouped["pct_no_prazo_chegada"] = grouped["no_prazo_chegada"] / denom * 100
    abrev_map = transportadora_abreviatura_map(df)
    grouped["abreviatura"] = grouped["transportadora"].map(abrev_map).fillna(grouped["transportadora"])
    return grouped.sort_values("viagens", ascending=False)


def motivos_atraso_chegada(df: pd.DataFrame, top: int = 10) -> pd.DataFrame:
    serie = df.loc[df["fora_prazo_chegada"], "motivo_atraso_chegada"].dropna()
    contagem = serie.value_counts().head(top).reset_index()
    contagem.columns = ["motivo", "ocorrencias"]
    return contagem


def regional_dist(df: pd.DataFrame) -> pd.DataFrame:
    contagem = df["regional"].dropna().value_counts().reset_index()
    contagem.columns = ["regional", "viagens"]
    return contagem


# Colunas do detalhe de atraso: variam conforme o motivo se referir à saída
# (origem/horário de saída) ou à chegada/transit time (destino/horário de
# chegada) — ambas mapeiam para os mesmos rótulos amigáveis de exibição.
COLS_DETALHE_CHEGADA = {
    "data": "Data",
    "id_viagem": "ID Viagem",
    "numero_linha": "Nº linha",
    "secao_estrada": "Seção da estrada",
    "placa": "Placa",
    "modelo_veiculo": "Modelo do veículo",
    "abreviatura": "Transportadora",
    "destino": "Destino",
    "previsto_chegada": "Previsto chegada",
    "real_chegada": "Real chegada",
    "status_chegada": "Status chegada",
    "motivo_chegada_menor": "Motivo do atraso chegada (motivo menor)",
}

COLS_DETALHE_SAIDA = {
    "data": "Data",
    "id_viagem": "ID Viagem",
    "numero_linha": "Nº linha",
    "secao_estrada": "Seção da estrada",
    "placa": "Placa",
    "modelo_veiculo": "Modelo do veículo",
    "abreviatura": "Transportadora",
    "origem": "Origem",
    "planejado_saida": "Planejado saída",
    "real_saida": "Real saída",
    "descricao_ocorrencia_saida": "Descrição detalhada da ocorrência saída",
}

COLS_DETALHE_TRANSIT = {
    "data": "Data",
    "id_viagem": "ID Viagem",
    "numero_linha": "Nº linha",
    "secao_estrada": "Seção da estrada",
    "placa": "Placa",
    "modelo_veiculo": "Modelo do veículo",
    "abreviatura": "Transportadora",
    "origem": "Origem",
    "destino": "Destino",
    "tt_planejado": "TT planejado",
    "tt_real": "TT real",
    "status_transit": "Status transit time",
    "motivo_transit": "Motivo do atraso transit time",
}


_CATEGORIAS_DETALHE = {
    "saida": ("fora_prazo_saida", COLS_DETALHE_SAIDA, None),
    "chegada": ("fora_prazo_chegada", COLS_DETALHE_CHEGADA, "responsabilidade_chegada"),
    "transit": ("fora_prazo_transit", COLS_DETALHE_TRANSIT, "responsabilidade_transit"),
}


def detalhe_categoria(df: pd.DataFrame, categoria: str) -> tuple[pd.DataFrame, dict]:
    # As 3 tabelas de detalhe mostram só atrasos de responsabilidade da
    # transportadora (colunas "Responsabilidade chegada"/"Responsabilidade
    # transit time" da planilha, ou o equivalente vindo da aba Saída real
    # pra saída) — atrasos de Operações/Incontrolável não entram aqui.
    if categoria == "saida":
        return detalhe_saida_real(df)
    coluna_flag, colunas, coluna_responsabilidade = _CATEGORIAS_DETALHE[categoria]
    filtrado = df[df[coluna_flag]].copy()
    if coluna_responsabilidade:
        filtrado = filtrado[_eh_responsabilidade_transportadora(filtrado[coluna_responsabilidade])]
    campos = list(colunas.keys())
    detalhe = filtrado[["chave_viagem", "transportadora"] + campos].rename(columns=colunas)
    return detalhe.sort_values("Data", ascending=False), colunas


COLS_DETALHE_SAIDA_REAL = {
    "data": "Data",
    "id_viagem": "ID Viagem",
    "numero_linha": "Nº linha",
    "secao_estrada": "Seção da estrada",
    "placa": "Placa",
    "modelo_veiculo": "Modelo do veículo",
    "abreviatura": "Transportadora",
    "origem": "Origem",
    "planejado_saida": "Planejado saída",
    "real_saida": "Real saída",
    "motivo_saida_real": "Motivo do atraso saída",
    "descricao_ocorrencia_saida_real": "Descrição detalhada da ocorrência saída",
    "responsabilidade_saida_real": "Responsabilidade",
}


def detalhe_saida_real(df_escopo: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    # "Detalhe Atraso Saída" usa a aba Saída real como fonte (por pedido do
    # usuário) — inclusive o filtro de atraso é o "Status saída" DESSA aba,
    # não o da Aba Principal. df_escopo é o dataframe já filtrado por
    # transportadora/mês/quinzena na tela; usamos as chaves dele (id viagem
    # + seção da estrada, sem a data por causa do deslocamento de 1 dia
    # entre as abas) só para restringir a Saída real ao mesmo recorte, e
    # para reaproveitar a chave_viagem canônica (mesma usada nas
    # justificativas em qualquer outro lugar do app).
    bruto = fetch_saida_real_dataframe()

    transportadora = bruto[COL_TRANSPORTADORA].astype(str).str.strip().replace(TRANSPORTADORA_CANONICA)
    abrev_col = _abreviatura_col(bruto.columns)
    abreviatura = _strip_cjk(bruto[abrev_col]) if abrev_col else transportadora
    abreviatura = transportadora.map(ABREVIATURA_CANONICA).fillna(abreviatura)

    status_saida_raw = bruto["Status saída"].astype(str)
    fora_prazo = status_saida_raw.str.contains("Fora do prazo", case=False, na=False)

    id_viagem = bruto["ID viagem"].astype(str)
    secao_estrada = bruto["Seção da estrada"].astype(str)
    chave_id_secao = id_viagem + "|" + secao_estrada

    chave_lookup = pd.Series(
        df_escopo["chave_viagem"].values,
        index=df_escopo["id_viagem"].astype(str) + "|" + df_escopo["secao_estrada"].astype(str),
    )
    chave_lookup = chave_lookup[~chave_lookup.index.duplicated(keep="first")]
    dentro_do_escopo = chave_id_secao.isin(chave_lookup.index)

    responsabilidade_transportadora = (
        _eh_responsabilidade_transportadora(bruto["Responsabilidade"])
        if "Responsabilidade" in bruto.columns
        else pd.Series(False, index=bruto.index)
    )
    mascara = fora_prazo & dentro_do_escopo & responsabilidade_transportadora

    out = pd.DataFrame()
    out["transportadora"] = transportadora
    out["abreviatura"] = abreviatura
    out["data"] = pd.to_datetime(bruto["Data"], format="%Y/%m/%d", errors="coerce")
    out["id_viagem"] = id_viagem
    out["numero_linha"] = bruto.get("Nome de linha")
    out["secao_estrada"] = secao_estrada
    out["placa"] = bruto.get("Placa do carro")
    out["modelo_veiculo"] = bruto.get("Nome de modelo de veículo")
    out["origem"] = bruto.get("Origem")
    # Formato "AAAA/MM/DD HH:MM:SS" nesta aba (diferente da Aba Principal) —
    # dayfirst=True inverteria mês e dia aqui.
    out["planejado_saida"] = pd.to_datetime(bruto.get("Horário planejado de saída"), dayfirst=False, errors="coerce")
    out["real_saida"] = pd.to_datetime(bruto.get("Horário real de saída"), dayfirst=False, errors="coerce")
    out["motivo_saida_real"] = _strip_cjk(bruto.get("Motivos do atraso saída"))
    out["descricao_ocorrencia_saida_real"] = _strip_cjk(bruto.get("Descrição detalhada da ocorrência saída"))
    if "Responsabilidade" in bruto.columns:
        out["responsabilidade_saida_real"] = bruto["Responsabilidade"].map(RESPONSABILIDADE_MOTIVO_SAIDA)
    else:
        out["responsabilidade_saida_real"] = pd.NA
    out["chave_viagem"] = chave_id_secao.map(chave_lookup)

    out = out[mascara].copy()
    campos = list(COLS_DETALHE_SAIDA_REAL.keys())
    detalhe = out[["chave_viagem", "transportadora"] + campos].rename(columns=COLS_DETALHE_SAIDA_REAL)
    return detalhe.sort_values("Data", ascending=False), COLS_DETALHE_SAIDA_REAL


def motoristas_ofensores(df: pd.DataFrame) -> pd.DataFrame:
    # "Ofensa" = viagem com atraso (saída, chegada ou transit) de
    # responsabilidade da Transportadora — mesma base das 3 tabelas fixas
    # de detalhe, pra manter consistência com o resto do app (Operações e
    # Incontrolável não contam pro motorista).
    ocorrencias = []
    for categoria in ("saida", "chegada", "transit"):
        detalhe, _ = detalhe_categoria(df, categoria)
        if not detalhe.empty:
            ocorrencias.append(detalhe[["chave_viagem", "Placa", "Seção da estrada"]])

    colunas_saida = ["Motorista", "Placa", "Seção da estrada", "Reincidência", "Status", "Quantidade"]
    if not ocorrencias:
        return pd.DataFrame(columns=colunas_saida)

    todas = pd.concat(ocorrencias, ignore_index=True)
    todas = todas.merge(df[["chave_viagem", "motorista"]], on="chave_viagem", how="left")
    todas = todas.dropna(subset=["motorista"])
    if todas.empty:
        return pd.DataFrame(columns=colunas_saida)

    agrupado = todas.groupby(["motorista", "Placa", "Seção da estrada"], as_index=False).agg(
        Quantidade=("chave_viagem", "count")
    )
    agrupado = agrupado.rename(columns={"motorista": "Motorista"})
    agrupado["Reincidência"] = agrupado["Quantidade"].apply(lambda q: "Reincidente" if q > 1 else "Não reincidente")
    agrupado["Status"] = agrupado["Quantidade"].apply(
        lambda q: "Crítico" if q >= 3 else ("Atenção" if q == 2 else "Regular")
    )
    return agrupado.sort_values("Quantidade", ascending=False)[colunas_saida]
