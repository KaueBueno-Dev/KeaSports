from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from .services.categorias import (
    MENSAGEM_IDADE_MINIMA_INSCRICAO,
    calcular_categoria_por_idade,
    calcular_idade,
    idade_permitida_para_inscricao,
)
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
    categoria = models.CharField(max_length=20, blank=True, null=True)

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
        return calcular_categoria_por_idade(self.idade)

    def save(self, *args, **kwargs):

        if self.data_nascimento:
            referencia = getattr(self, "_categoria_referencia", None)
            self.idade = calcular_idade(self.data_nascimento, referencia=referencia)
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
    limite_inscritos = models.PositiveIntegerField(null=True, blank=True)
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

    @property
    def vagas_ocupadas(self):
        vagas_anotadas = getattr(self, "vagas_ocupadas_pagas", None)
        if vagas_anotadas is not None:
            return vagas_anotadas
        if not self.pk:
            return 0
        return self.inscricoes.filter(pago=True).count()

    @property
    def vagas_restantes(self):
        if self.limite_inscritos is None:
            return None
        return max(self.limite_inscritos - self.vagas_ocupadas, 0)

    @property
    def esta_esgotada(self):
        if self.limite_inscritos is None:
            return False
        return self.vagas_ocupadas >= self.limite_inscritos


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
        on_delete=models.CASCADE,
        related_name="resultados",
        null=True,
        blank=True,
    )
    percurso = models.OneToOneField(
        PercursoCorrida,
        on_delete=models.CASCADE,
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
    enviada_cronometragem = models.BooleanField(default=False, db_index=True)
    data_envio_cronometragem = models.DateTimeField(null=True, blank=True)
    erro_envio_cronometragem = models.TextField(null=True, blank=True)
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
        if (
            self.participante_id
            and self.corrida_id
            and self.participante.data_nascimento
            and not idade_permitida_para_inscricao(
                self.participante.data_nascimento,
                referencia=self.corrida.data,
            )
        ):
            raise ValidationError({
                "participante": MENSAGEM_IDADE_MINIMA_INSCRICAO,
            })

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


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
