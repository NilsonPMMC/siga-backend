# Migration segura: adiciona colunas IA apenas se não existirem
# Resolve conflito quando 0035 foi aplicada parcialmente ou colunas já existiam

from django.db import migrations, connection


def column_exists(table, column):
    with connection.cursor() as cursor:
        if connection.vendor == "mysql":
            cursor.execute(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s
                """,
                [table, column],
            )
        elif connection.vendor == "sqlite":
            cursor.execute("PRAGMA table_info(%s)" % table)
            cols = cursor.fetchall()
            # PRAGMA table_info: cid, name, type, notnull, dflt_value, pk
            return any((len(c) > 1 and c[1] == column) for c in cols)
        else:
            cursor.execute(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema() AND table_name = %s AND column_name = %s
                """,
                [table, column],
            )
        return cursor.fetchone() is not None


def add_missing_columns(apps, schema_editor):
    table = "atendimentos_atendimento"
    vendor = connection.vendor

    qn = connection.ops.quote_name
    with connection.cursor() as cursor:
        if not column_exists(table, "resumo_ia_local"):
            if vendor == "mysql":
                cursor.execute("ALTER TABLE %s ADD COLUMN resumo_ia_local LONGTEXT NULL" % qn(table))
            else:
                cursor.execute("ALTER TABLE %s ADD COLUMN resumo_ia_local TEXT NULL" % qn(table))
        if not column_exists(table, "vetor_ia_atendimento"):
            if vendor == "mysql":
                cursor.execute("ALTER TABLE %s ADD COLUMN vetor_ia_atendimento JSON NULL" % qn(table))
            elif vendor == "sqlite":
                cursor.execute("ALTER TABLE %s ADD COLUMN vetor_ia_atendimento TEXT NULL" % qn(table))
            else:
                cursor.execute("ALTER TABLE %s ADD COLUMN vetor_ia_atendimento JSONB NULL" % qn(table))
        if not column_exists(table, "auditoria_ia_status"):
            cursor.execute(
                "ALTER TABLE %s ADD COLUMN auditoria_ia_status VARCHAR(20) NOT NULL DEFAULT 'PENDENTE'" % qn(table)
            )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("atendimentos", "0035_atendimento_ia_fields"),
    ]

    operations = [
        migrations.RunPython(add_missing_columns, noop),
    ]
