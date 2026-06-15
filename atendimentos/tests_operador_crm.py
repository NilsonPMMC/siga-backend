from django.contrib.auth.models import Group, User
from rest_framework import status
from rest_framework.test import APITestCase

from .models import CategoriaContato, Conta, Municipe, PerfilMunicipe, PerfilUsuario
from .tests import _itens_lista_api


class OperadorCrmEscopoTests(APITestCase):
    """Perfil Operador CRM — escopo por categoria e CRUD parcial."""

    def setUp(self):
        self.grupo, _ = Group.objects.get_or_create(name="Operador CRM")
        self.user = User.objects.create_user(username="operador_crm", password="123456")
        self.user.groups.add(self.grupo)
        self.conta = Conta.objects.create(nome="Gab Operador", nome_sigla="GOP")
        self.cat_permitida = CategoriaContato.objects.create(nome="CAT PERMITIDA", ativa=True)
        self.cat_bloqueada = CategoriaContato.objects.create(nome="CAT BLOQUEADA", ativa=True)
        self.perfil = PerfilUsuario.objects.create(usuario=self.user)
        self.perfil.contas.add(self.conta)
        self.perfil.categorias_contato.add(self.cat_permitida)
        self.client.force_authenticate(user=self.user)

    def _criar_municipe(self, nome, categoria, cargo="CARGO"):
        mun = Municipe.objects.create(nome_completo=nome)
        mun.contas.add(self.conta)
        PerfilMunicipe.objects.create(
            municipe=mun,
            conta=self.conta,
            categoria=categoria,
            cargo=cargo,
            ativo=True,
        )
        return mun

    def test_listagem_apenas_categoria_permitida(self):
        visivel = self._criar_municipe("Visivel CRM", self.cat_permitida)
        self._criar_municipe("Oculto CRM", self.cat_bloqueada)

        resp = self.client.get("/api/municipes/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = {item["id"] for item in _itens_lista_api(resp.data)}
        self.assertIn(visivel.id, ids)
        self.assertEqual(len(ids), 1)

    def test_municipe_multiplas_categorias_visao_parcial(self):
        mun = self._criar_municipe("Multi Cat", self.cat_permitida, cargo="A")
        PerfilMunicipe.objects.create(
            municipe=mun,
            conta=self.conta,
            categoria=self.cat_bloqueada,
            cargo="B",
            ativo=True,
        )

        resp = self.client.get(f"/api/municipes/{mun.id}/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data["perfis"]), 1)
        self.assertEqual(resp.data["perfis"][0]["categoria"], self.cat_permitida.id)

    def test_nao_cria_categoria(self):
        resp = self.client.post(
            "/api/contatos/categorias/",
            {"nome": "NOVA CAT", "ativa": True},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_lista_categorias_apenas_permitidas(self):
        resp = self.client.get("/api/contatos/categorias/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = {item["id"] for item in resp.data}
        self.assertEqual(ids, {self.cat_permitida.id})

    def test_duplicatas_contador_negado(self):
        resp = self.client.get("/api/municipes/duplicatas/contador/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_export_excel_respeita_escopo(self):
        import io

        import openpyxl

        self._criar_municipe("Exp Visivel", self.cat_permitida)
        self._criar_municipe("Exp Oculto", self.cat_bloqueada)

        resp = self.client.get("/api/municipes/export/excel/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        workbook = openpyxl.load_workbook(io.BytesIO(resp.content))
        nomes = [row[0] for row in workbook.active.iter_rows(min_row=2, values_only=True)]
        self.assertIn("EXP VISIVEL", nomes)
        self.assertNotIn("EXP OCULTO", nomes)

    def test_patch_nao_altera_perfil_fora_escopo(self):
        mun = self._criar_municipe("Edit Parcial", self.cat_permitida, cargo="EDIT")
        perfil_bloq = PerfilMunicipe.objects.create(
            municipe=mun,
            conta=self.conta,
            categoria=self.cat_bloqueada,
            cargo="SECRET",
            ativo=True,
        )
        perfil_ok = mun.perfis.get(categoria=self.cat_permitida)

        resp = self.client.patch(
            f"/api/municipes/{mun.id}/",
            {
                "perfis": [
                    {"id": perfil_ok.id, "conta": self.conta.id, "categoria": self.cat_permitida.id, "cargo": "NOVO"},
                ]
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        perfil_bloq.refresh_from_db()
        self.assertEqual(perfil_bloq.cargo, "SECRET")
        perfil_ok.refresh_from_db()
        self.assertEqual(perfil_ok.cargo, "NOVO")
