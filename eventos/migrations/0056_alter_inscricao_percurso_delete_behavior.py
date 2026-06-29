from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("api_s", "0055_inscricao_numero_chip_resultadoinscricao"),
    ]

    operations = [
        migrations.AlterField(
            model_name="inscricao",
            name="percurso",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="inscricoes",
                to="api_s.percursocorrida",
            ),
        ),
    ]
