from django.db import migrations

ASSUNTOS_EXTRAS = [
    ('CULTURA', 'cultura', 110),
    ('ESPORTE E LAZER', 'esporte_lazer', 115),
    ('ELOGIO', 'elogio', 120),
    ('RECLAMAÇÃO', 'reclamacao', 125),
    ('OUVIDORIA', 'ouvidoria', 130),
    ('PROCON', 'procon', 131),
    ('CONTROLADORIA', 'controladoria', 132),
    ('IPREM', 'iprem', 133),
    ('PROCURADORIA', 'procuradoria', 134),
    ('LONGEVIDADE', 'longevididade', 135),
    ('MULHER', 'mulher', 136),
    ('SERVIÇO MILITAR', 'servico_militar', 137),
]


def popular_assuntos_extras(apps, schema_editor):
    AssuntoAtendimento = apps.get_model('atendimentos', 'AssuntoAtendimento')
    for nome, codigo, ordem in ASSUNTOS_EXTRAS:
        AssuntoAtendimento.objects.get_or_create(
            codigo=codigo,
            defaults={'nome': nome, 'ordem': ordem, 'ativo': True},
        )


def reverter_assuntos_extras(apps, schema_editor):
    AssuntoAtendimento = apps.get_model('atendimentos', 'AssuntoAtendimento')
    codigos = [item[1] for item in ASSUNTOS_EXTRAS]
    AssuntoAtendimento.objects.filter(codigo__in=codigos).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('atendimentos', '0051_registrovisita_atendimento_fk'),
    ]

    operations = [
        migrations.RunPython(popular_assuntos_extras, reverter_assuntos_extras),
    ]
