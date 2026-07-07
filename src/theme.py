# Paleta validada com scripts/validate_palette.js da skill dataviz.
# Vermelho de marca (J&T) no slot categórico 1; slots 2-8 seguem o tema
# padrão da skill, reordenados para maximizar separação CVD adjacente.
# Ambos os modos (claro/escuro) foram validados: node validate_palette.js
# "<paleta>" --mode light|dark -> ALL PASS (CVD adjacente pior caso 24.2
# claro / 10.3 escuro — floor band, ok com rótulo direto, que já usamos).

BRAND_RED = "#ED2024"  # mesmo hex nos dois modos: L=0.605 cai nas duas faixas OKLCH
SECONDARY_GRAY = "#D8D2D2"  # croma ~0 (neutro) — cor secundária padrão do app.
# Usada como 2ª série nos gráficos (ex.: linha "No prazo saída"). Contraste
# baixo no fundo claro (1.45:1) é aceitável aqui só porque cada ponto já tem
# rótulo de valor direto (a "válvula de alívio" exigida pela skill dataviz
# para cor abaixo de 3:1) — sem os rótulos, essa cor não deveria ser usada
# como marca de dado sozinha.

CATEGORICAL_LIGHT = [BRAND_RED, "#2a78d6", "#1baf7a", "#eda100", "#008300", "#4a3aa7", "#e87ba4", "#eb6834"]
CATEGORICAL_DARK = [BRAND_RED, "#3987e5", "#199e70", "#c98500", "#008300", "#9085e9", "#d55181", "#d95926"]

# Status é fixo — nunca segue o tema da marca (evita que a cor de marca seja
# confundida com "atrasado"/crítico). Mesmos hex nos dois modos.
STATUS_GOOD = "#0ca30c"
STATUS_WARNING = "#fab219"
STATUS_SERIOUS = "#ec835a"
STATUS_CRITICAL = "#d03b3b"

CHROME = {
    "light": {
        "surface": "#fcfcfb",
        "ink_primary": "#0b0b0b",
        "ink_secondary": "#52514e",
        "ink_muted": "#898781",
        "gridline": SECONDARY_GRAY,
        "categorical": CATEGORICAL_LIGHT,
        "cor_secundaria": SECONDARY_GRAY,
    },
    "dark": {
        "surface": "#1a1a19",
        "ink_primary": "#ffffff",
        "ink_secondary": "#c3c2b7",
        "ink_muted": "#898781",
        "gridline": "#2c2c2a",
        "categorical": CATEGORICAL_DARK,
        "cor_secundaria": SECONDARY_GRAY,
    },
}


def chart_colors(mode: str) -> dict:
    return CHROME.get(mode, CHROME["light"])
