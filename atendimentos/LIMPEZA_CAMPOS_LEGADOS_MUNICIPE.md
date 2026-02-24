# Limpeza posterior: remover campos legados do Municipe

Execute **apenas** quando todo o código já ler cargo/órgão/tratamento de `PerfilMunicipe` (e não mais de `Municipe.cargo`, `.orgao`, `.tratamento`).

## 1. Conferir o código

Garanta que não restem referências a:

- `municipe.cargo`
- `municipe.orgao`
- `municipe.tratamento`

(Exceto em migrações ou em código que só popula esses campos para migração.)

## 2. Criar a migração de remoção

```bash
cd /caminho/do/backend
python manage.py makemigrations atendimentos --empty --name remover_campos_legados_municipe
```

Edite o arquivo gerado em `atendimentos/migrations/` e deixe as `operations` assim:

```python
operations = [
    migrations.RemoveField(model_name='municipe', name='cargo'),
    migrations.RemoveField(model_name='municipe', name='orgao'),
    migrations.RemoveField(model_name='municipe', name='tratamento'),
]
```

Ajuste o `dependencies` para apontar para a última migração do app (ex.: `0034_perfil_municipe_legado_completo`).

## 3. Remover os campos do model

Em `atendimentos/models.py`, na classe `Municipe`, apague as linhas dos campos:

- `cargo`
- `orgao`
- `tratamento`

(Se ainda estiverem comentados, remova o comentário e o campo de uma vez.)

## 4. Aplicar a migração

```bash
python manage.py migrate atendimentos
```

Compatível com MariaDB/MySQL.
