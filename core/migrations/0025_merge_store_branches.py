"""Unifica os dois ramos de migração da antiga loja.

O banco em uso tinha apenas um dos ramos aplicado. Esta migração vazia torna o
grafo consistente; as migrações de criação já existentes continuam válidas e
não apagam nenhum dado.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0021_alter_categoria_id_alter_categoriacard_id_and_more"),
        ("core", "0024_remove_servico_canal_servico_telegram_ativo_and_more"),
    ]

    operations = []
