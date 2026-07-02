from datetime import date


IDADE_MINIMA_INSCRICAO = 14
MENSAGEM_IDADE_MINIMA_INSCRICAO = (
    "Nao e permitido realizar inscricao para participantes menores de 14 anos."
)

CATEGORIAS_POR_IDADE = (
    (17, "Menor de 18"),
    (24, "18-24"),
    (29, "25-29"),
    (34, "30-34"),
    (39, "35-39"),
    (44, "40-44"),
    (49, "45-49"),
    (54, "50-54"),
    (59, "55-59"),
    (64, "60-64"),
    (69, "65-69"),
    (None, "65+"),
)


def calcular_idade(data_nascimento, referencia=None):
    if not data_nascimento:
        return None

    referencia = referencia or date.today()
    if data_nascimento > referencia:
        return None

    return referencia.year - data_nascimento.year - (
        (referencia.month, referencia.day)
        < (data_nascimento.month, data_nascimento.day)
    )


def calcular_categoria_por_idade(idade):
    if idade is None:
        return ""
    if idade < IDADE_MINIMA_INSCRICAO:
        return ""

    for idade_maxima, categoria in CATEGORIAS_POR_IDADE:
        if idade_maxima is None or idade <= idade_maxima:
            return categoria

    return ""


def calcular_categoria_por_data_nascimento(data_nascimento, referencia=None):
    idade = calcular_idade(data_nascimento, referencia=referencia)
    return calcular_categoria_por_idade(idade)


def idade_permitida_para_inscricao(data_nascimento, referencia=None):
    idade = calcular_idade(data_nascimento, referencia=referencia)
    return idade is not None and idade >= IDADE_MINIMA_INSCRICAO
