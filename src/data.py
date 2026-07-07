import re

import pandas as pd

from src.config import CSV_URL, MESES_PT

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
COL_KM = "Quilometragem"
COL_VALOR_MULTA = "Valor da multa"
COL_MES = "mês"
COL_TT_PLANEJADO = "TT PLANEJADO"
COL_TT_REAL = "TT REAL"
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
    out["motivo_atraso_chegada"] = _strip_cjk(df[motivo_chegada_col])
    out.loc[df[motivo_chegada_col].isna(), "motivo_atraso_chegada"] = pd.NA
    out["motivo_chegada_menor"] = _strip_cjk(df[COL_MOTIVO_CHEGADA_DETALHE]) if COL_MOTIVO_CHEGADA_DETALHE in df.columns else pd.NA
    out.loc[df[COL_MOTIVO_CHEGADA_DETALHE].isna(), "motivo_chegada_menor"] = pd.NA

    out["km"] = _to_float_br(df[COL_KM]) if COL_KM in df.columns else pd.NA
    out["valor_multa"] = _to_float_br(df[COL_VALOR_MULTA]) if COL_VALOR_MULTA in df.columns else pd.NA
    out["tt_planejado"] = df.get(COL_TT_PLANEJADO)
    out["tt_real"] = df.get(COL_TT_REAL)
    out["faixa_atraso"] = df.get(COL_FAIXA_ATRASO)

    mes_num = pd.to_numeric(df[COL_MES], errors="coerce")
    out["mes"] = mes_num
    out["mes_nome"] = mes_num.map(MESES_PT)

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

    return out


def load_transportadoras() -> list[str]:
    df = fetch_raw_dataframe()
    valores = df[COL_TRANSPORTADORA].dropna().astype(str).str.strip().replace(TRANSPORTADORA_CANONICA)
    valores = valores[valores != ""]
    return sorted(valores.unique().tolist())


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


def eh_motivo_saida(motivo: str) -> bool:
    motivo_lower = (motivo or "").lower()
    return "saída" in motivo_lower or "saida" in motivo_lower


def detalhe_atraso(df: pd.DataFrame, motivo: str) -> tuple[pd.DataFrame, dict]:
    filtrado = df[df["fora_prazo_chegada"] & (df["motivo_atraso_chegada"] == motivo)].copy()
    colunas = COLS_DETALHE_SAIDA if eh_motivo_saida(motivo) else COLS_DETALHE_CHEGADA
    campos = list(colunas.keys())
    detalhe = filtrado[["chave_viagem", "transportadora"] + campos].rename(columns=colunas)
    return detalhe.sort_values("Data", ascending=False), colunas
