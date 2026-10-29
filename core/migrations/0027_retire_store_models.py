"""Retira os modelos da loja do estado das migrações sem apagar tabelas/dados.

As tabelas legadas permanecem intactas no banco, junto com todos os registros
comerciais existentes. Apenas o estado do Django deixa de conhecê-las, para que
o código do POS não dependa do comércio eletrônico. Uma eventual remoção física
das tabelas exige decisão separada e backup.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0026_invoice"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(name="Servico"),
                migrations.DeleteModel(name="Produto"),
                migrations.DeleteModel(name="Categoria"),
                migrations.DeleteModel(name="Profile"),
                migrations.DeleteModel(name="ConfiguracaoWhatsApp"),
                migrations.DeleteModel(name="ConfiguracaoTelegram"),
                migrations.DeleteModel(name="ConfiguracaoRodape"),
                migrations.DeleteModel(name="ConfiguracaoPix"),
                migrations.DeleteModel(name="CategoriaCard"),
                migrations.DeleteModel(name="MacroCategoria"),
                migrations.DeleteModel(name="HomeConfig"),
                migrations.DeleteModel(name="SiteConfig"),
                migrations.DeleteModel(name="CotacaoBitcoin"),
            ],
        ),
    ]
