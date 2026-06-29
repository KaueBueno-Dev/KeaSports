from django.db import models
from datetime import date
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from .validators import validate_excel_extension, validate_file_size, validate_image_extension


class Participante(models.Model):

    usuario = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="participante"
    )

    nome = models.CharField(max_length=100, db_index=True)

    data_nascimento = models.DateField()
    idade = models.IntegerField(blank=True, null=True)
    categoria = models.CharField(max_length=8, blank=True, null=True)

    cpf = models.CharField(max_length=11, unique=True)

    equipe = models.CharField(max_length=100, null=True, blank=True)
    cidade = models.CharField(max_length=100, null=True, blank=True, db_index=True)

    TAMANHO_CAMISA = [
        ('P', 'P'),
        ('M', 'M'),
        ('G', 'G'),
    ]

    tamanho_camisa = models.CharField(max_length=1, choices=TAMANHO_CAMISA, default='M', null=True, blank=True)

    SEXO_OPCOES = [
        ('M', 'Masculino'),
        ('F', 'Feminino'),
    ]

    sexo = models.CharField(max_length=1, choices=SEXO_OPCOES, default='M', null=True, blank=True)

    def calcular_categoria(self):

        if self.idade is None:
            return ""

        if self.idade <= 19:
            return "15-19"
        elif 20 <= self.idade <= 24:
            return "20-24"
        elif 25 <= self.idade <= 29:
            return "25-29"
        elif 30 <= self.idade <= 39:
            return "30-39"
        elif 40 <= self.idade <= 44:
            return "40-44"
        elif 45 <= self.idade <= 49:
            return "45-49"
        elif 50 <= self.idade <= 54:
            return "50-54"
        elif 55 <= self.idade <= 59:
            return "55-59"
        elif 60 <= self.idade <= 64:
            return "60-64"
        else:
            return "65+"

    def save(self, *args, **kwargs):

        if self.data_nascimento:
            hoje = date.today()

            self.idade = hoje.year - self.data_nascimento.year - (
                (hoje.month, hoje.day) <
                (self.data_nascimento.month, self.data_nascimento.day)
            )

            self.categoria = self.calcular_categoria()

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.nome} ({self.idade} anos) - {self.categoria} - {self.sexo}"


class Corrida(models.Model):

    nome = models.CharField(max_length=100)
    local = models.CharField(max_length=100)
    local_evento = models.CharField(
        max_length=150,
        blank=True,
        null=True,
        verbose_name="Local do evento",
    )
    data = models.DateField(db_index=True)
    imagem = models.ImageField(
        upload_to='corridas/',
        blank=True,
        null=True,
        validators=[validate_file_size, validate_image_extension],
    )

    class Meta:
        verbose_name = "Corrida"
        verbose_name_plural = "Corridas"

    def __str__(self):
        return self.nome


class PercursoCorrida(models.Model):
    corrida = models.ForeignKey(
        Corrida,
        on_delete=models.CASCADE,
        related_name="percursos",
    )
    nome = models.CharField(max_length=50)
    distancia_km = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        db_index=True,
    )
    ativo = models.BooleanField(default=True, db_index=True)
    ordem = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Percurso da corrida"
        verbose_name_plural = "Percursos da corrida"
        constraints = [
            models.UniqueConstraint(
                fields=["corrida", "nome"],
                name="unique_percurso_por_corrida",
            ),
        ]
        indexes = [
            models.Index(fields=["corrida", "ativo", "ordem"]),
        ]
        ordering = ["ordem", "distancia_km", "nome"]

    def __str__(self):
        return f"{self.corrida} - {self.nome}"


class ArquivoExcel(models.Model):

    corrida = models.ForeignKey(
        Corrida,
        on_delete=models.PROTECT,
        related_name="resultados",
        null=True,
        blank=True,
    )
    percurso = models.OneToOneField(
        PercursoCorrida,
        on_delete=models.PROTECT,
        related_name="resultado",
        null=True,
        blank=True,
    )
    nome = models.CharField(max_length=50, db_index=True)
    data_corrida = models.CharField(max_length=15, null=True, blank=True)
    local = models.CharField(max_length=60, null=True, blank=True)
    arquivo = models.FileField(
        upload_to='uploads/',
        validators=[validate_file_size, validate_excel_extension],
    )
    criado_em = models.DateTimeField(auto_now_add=True, db_index=True)
    imagem = models.ImageField(
        upload_to='imagens/',
        blank=True,
        null=True,
        validators=[validate_file_size, validate_image_extension],
    )

    class Meta:
        verbose_name = "Resultado"
        verbose_name_plural = "Resultados"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(corrida__isnull=False) | models.Q(percurso__isnull=False),
                name="resultado_exige_corrida_ou_percurso",
            ),
        ]

    def __str__(self):
        return self.nome

    def clean(self):
        super().clean()
        if not self.corrida_id and not self.percurso_id:
            raise ValidationError({
                "corrida": "Informe a corrida ou o percurso do resultado.",
            })
        if self.percurso_id:
            percurso_corrida_id = self.percurso.corrida_id
            if self.corrida_id and self.corrida_id != percurso_corrida_id:
                raise ValidationError({
                    "percurso": "Percurso invalido para esta corrida.",
                })
            self.corrida_id = percurso_corrida_id

    def save(self, *args, **kwargs):
        if self.percurso_id:
            self.corrida_id = self.percurso.corrida_id
        super().save(*args, **kwargs)


class Corredor(models.Model):

    participante = models.ForeignKey(
        Participante,
        on_delete=models.CASCADE,
        related_name="resultados",
        null=True,
        blank=True
    )

    arquivo = models.ForeignKey(ArquivoExcel, on_delete=models.CASCADE, null=True, blank=True)

    colocacao = models.IntegerField(db_index=True)
    numero = models.CharField(max_length=10)
    nome = models.CharField(max_length=100, db_index=True)
    categoria = models.CharField(max_length=50, db_index=True)
    distancia = models.CharField(max_length=50, blank=True, default="Geral", db_index=True)
    equipe = models.CharField(max_length=100, blank=True, null=True)

    tempo_segundos = models.FloatField(null=True, blank=True, db_index=True)
    tempo_formatado = models.CharField(max_length=20, null=True, blank=True)

    Vel_media = models.CharField(max_length=20)

    class Meta:
        verbose_name = "Classificacao"
        verbose_name_plural = "Classificacoes"
        indexes = [
            models.Index(fields=["arquivo", "colocacao"]),
            models.Index(fields=["arquivo", "categoria", "tempo_segundos"]),
            models.Index(fields=["arquivo", "distancia", "colocacao"]),
        ]

    def __str__(self):
        return self.nome


class Inscricao(models.Model):
    participante = models.ForeignKey(
        Participante,
        on_delete=models.CASCADE,
        related_name="inscricoes",
    )
    corrida = models.ForeignKey(
        Corrida,
        on_delete=models.CASCADE,
        related_name="inscricoes",
    )
    percurso = models.ForeignKey(
        PercursoCorrida,
        on_delete=models.CASCADE,
        related_name="inscricoes",
    )
    pago = models.BooleanField(default=False, db_index=True)
    numero_chip = models.CharField(max_length=30, null=True, blank=True, db_index=True)
    criada_em = models.DateTimeField(auto_now_add=True, db_index=True)
    atualizada_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Inscrição"
        verbose_name_plural = "Inscrições"
        constraints = [
            models.UniqueConstraint(
                fields=["participante", "corrida"],
                name="unique_participante_corrida",
            ),
        ]
        indexes = [
            models.Index(fields=["corrida", "criada_em"]),
            models.Index(fields=["participante", "criada_em"]),
        ]

    def __str__(self):
        percurso = f" - {self.percurso.nome}" if self.percurso_id else ""
        return f"{self.participante} - {self.corrida}{percurso}"

    def clean(self):
        super().clean()
        if self.corrida_id and self.percurso_id and self.percurso.corrida_id != self.corrida_id:
            raise ValidationError({
                "percurso": "Percurso invalido para esta corrida.",
            })


class ResultadoInscricao(models.Model):
    inscricao = models.OneToOneField(
        Inscricao,
        on_delete=models.CASCADE,
        related_name="resultado_cronometragem",
    )
    tempo = models.CharField(max_length=30)
    posicao_geral = models.PositiveIntegerField()
    colocacao_categoria = models.PositiveIntegerField()
    criado_em = models.DateTimeField(auto_now_add=True, db_index=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Resultado de inscrição"
        verbose_name_plural = "Resultados de inscrições"
        indexes = [
            models.Index(fields=["posicao_geral"]),
            models.Index(fields=["colocacao_categoria"]),
        ]

    def __str__(self):
        return f"{self.inscricao} - {self.tempo}"


class Resultados(models.Model):

    nome = models.CharField(max_length=100)
    local = models.CharField(max_length=100)
    data = models.DateField()

    def __str__(self):
        return self.nome

