# Make PerfilMunicipe.categoria NOT NULL - use default for any remaining nulls
from django.db import migrations, models
import django.db.models.deletion


def preencher_categorias_nulas(apps, schema_editor):
    PerfilMunicipe = apps.get_model('atendimentos', 'PerfilMunicipe')
    CategoriaContato = apps.get_model('atendimentos', 'CategoriaContato')

    cat, _ = CategoriaContato.objects.get_or_create(
        nome="_A DEFINIR",
        defaults={'ativa': True}
    )
    n = PerfilMunicipe.objects.filter(categoria__isnull=True).update(categoria=cat)
    if n:
        print(f"[0041] {n} perfis com categoria nula preenchidos com '_A DEFINIR'")


class Migration(migrations.Migration):

    dependencies = [
        ('atendimentos', '0040_migrar_categoria_para_perfil'),
    ]

    operations = [
        migrations.RunPython(preencher_categorias_nulas, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='perfilmunicipe',
            name='categoria',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='perfis_municipe',
                to='atendimentos.categoriacontato',
                verbose_name='Categoria do Contato'
            ),
        ),
    ]
