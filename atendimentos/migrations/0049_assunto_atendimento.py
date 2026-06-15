from django.db import migrations, models
import django.db.models.deletion


ASSUNTOS_INICIAIS = [
    ("SAÚDE", "saude", 10),
    ("EDUCAÇÃO", "educacao", 20),
    ("OBRAS E INFRAESTRUTURA", "obras", 30),
    ("HABITAÇÃO", "habitacao", 40),
    ("ASSISTÊNCIA SOCIAL", "assistencia_social", 50),
    ("SEGURANÇA", "seguranca", 60),
    ("MEIO AMBIENTE", "meio_ambiente", 70),
    ("TRANSPORTE E MOBILIDADE", "transporte", 80),
    ("ADMINISTRAÇÃO E GABINETE", "administracao", 90),
    ("VISITA / RECEPÇÃO", "visita_recepcao", 100),
    ("OUTROS", "outros", 999),
]


def popular_assuntos_iniciais(apps, schema_editor):
    AssuntoAtendimento = apps.get_model("atendimentos", "AssuntoAtendimento")
    for nome, codigo, ordem in ASSUNTOS_INICIAIS:
        AssuntoAtendimento.objects.get_or_create(
            codigo=codigo,
            defaults={"nome": nome, "ordem": ordem, "ativo": True},
        )


def reverter_assuntos_iniciais(apps, schema_editor):
    AssuntoAtendimento = apps.get_model("atendimentos", "AssuntoAtendimento")
    codigos = [item[1] for item in ASSUNTOS_INICIAIS]
    AssuntoAtendimento.objects.filter(codigo__in=codigos).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("atendimentos", "0048_automacao_relatorio_diario_conta"),
    ]

    operations = [
        migrations.CreateModel(
            name="AssuntoAtendimento",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nome", models.CharField(max_length=100, unique=True, verbose_name="Nome do Assunto")),
                ("codigo", models.SlugField(help_text="Identificador estável (ex.: saude, educacao) para IA e integrações.", max_length=50, unique=True, verbose_name="Código")),
                ("descricao", models.TextField(blank=True, null=True, verbose_name="Descrição")),
                ("ativo", models.BooleanField(default=True, verbose_name="Ativo?")),
                ("ordem", models.PositiveSmallIntegerField(default=0, help_text="Menor valor aparece primeiro nas listas.", verbose_name="Ordem de exibição")),
            ],
            options={
                "verbose_name": "Assunto de Atendimento",
                "verbose_name_plural": "Assuntos de Atendimento",
                "ordering": ["ordem", "nome"],
            },
        ),
        migrations.AddField(
            model_name="atendimento",
            name="assunto",
            field=models.ForeignKey(
                blank=True,
                help_text="Classificação principal do tema (saúde, educação, visita, etc.).",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="atendimentos",
                to="atendimentos.assuntoatendimento",
                verbose_name="Assunto",
            ),
        ),
        migrations.AddField(
            model_name="atendimento",
            name="assunto_ia_status",
            field=models.CharField(
                blank=True,
                choices=[
                    ("PENDENTE", "Pendente"),
                    ("APLICADO", "Aplicado automaticamente"),
                    ("REVISADO", "Revisado manualmente"),
                    ("ERRO", "Erro"),
                ],
                help_text="Preenchido quando a rotina de IA processar o atendimento.",
                max_length=20,
                null=True,
                verbose_name="Status classificação IA (assunto)",
            ),
        ),
        migrations.AddField(
            model_name="atendimento",
            name="assunto_ia_sugerido",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="atendimentos_sugestao_ia",
                to="atendimentos.assuntoatendimento",
                verbose_name="Assunto sugerido pela IA",
            ),
        ),
        migrations.RunPython(popular_assuntos_iniciais, reverter_assuntos_iniciais),
    ]
