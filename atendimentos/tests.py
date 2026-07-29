import uuid
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from .models import (
    Atendimento,
    AssuntoAtendimento,
    CategoriaContato,
    Conta,
    LogDeAtividade,
    Municipe,
    PerfilMunicipe,
    PerfilUsuario,
)
from .services.busca_textual import (
    filtrar_queryset_atendimento,
    filtrar_queryset_municipe,
    normalizar_texto,
    registro_corresponde_termo,
    token_corresponde_palavra,
    tokenizar_busca_nome,
)
from .throttles import EnrichmentPreviewThrottle


def _itens_lista_api(data):
    if isinstance(data, list):
        return data
    return data.get("results", [])


class EnrichmentHitlApiTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.group = Group.objects.create(name="Secretária")
        self.user = User.objects.create_user(username="tester", password="123456")
        self.user.groups.add(self.group)

        self.conta = Conta.objects.create(nome="Gabinete Teste", nome_sigla="GT")
        self.perfil_user = PerfilUsuario.objects.create(usuario=self.user)
        self.perfil_user.contas.add(self.conta)

        self.mun = Municipe.objects.create(
            nome_completo="Contato Teste",
            emails=[{"email": "contato@teste.gov.br"}],
            telefones=[{"numero": "(11) 1111-1111"}],
            endereco={"texto_livre": "Rua A, 10 - Centro, São Paulo, 01000-000"},
            dados_etiqueta="Sr.\nContato Teste",
        )
        self.mun.contas.add(self.conta)
        self.categoria = CategoriaContato.objects.create(nome="MUNÍCIPE", ativa=True)
        self.perfil_mun = PerfilMunicipe.objects.create(
            municipe=self.mun,
            conta=self.conta,
            categoria=self.categoria,
            cargo="Assessor",
            instituicao="Gabinete",
            ativo=True,
        )
        self.client.force_authenticate(user=self.user)

    @patch("atendimentos.enrichment_api.enrichment_orchestrator.run_llm_react_once")
    @patch("atendimentos.enrichment_api.enrichment_orchestrator._default_model_for_provider", return_value="fake-model")
    @patch("atendimentos.enrichment_api.enrichment_orchestrator._build_llm_client", return_value=("groq", object()))
    @patch("atendimentos.enrichment_api.enrichment_orchestrator._maybe_load_dotenv")
    @patch("atendimentos.enrichment_api.siga_mcp_server.configure_runtime")
    def test_preview_success(
        self, _cfg_runtime, _dotenv, _build, _model, run_once
    ):
        run_once.return_value = {
            "llm_provider": "groq",
            "model": "fake-model",
            "final_text": "",
            "trace": [
                {
                    "tool": "update_contact_profile",
                    "input": {
                        "enriched_data": {
                            "emails": ["novo@teste.gov.br"],
                            "telefones": ["(11) 9999-0000"],
                            "endereco": "Av. Paulista, 1000",
                            "cargo": "Secretário",
                            "orgao": "Casa Civil",
                            "etiqueta_mala_direta": "Sr.\nContato Teste\nSecretário - Casa Civil",
                        }
                    },
                }
            ],
        }
        url = f"/api/contatos/{self.mun.id}/enrich/"
        resp = self.client.post(url, data={}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data.get("ok"))
        self.assertEqual(resp.data.get("contact_id"), self.mun.id)

    @patch("atendimentos.enrichment_api.siga_mcp_server.update_contact_profile", return_value={"ok": True})
    @patch("atendimentos.enrichment_api.siga_mcp_server.configure_runtime")
    def test_apply_success_and_audit(self, _cfg_runtime, _update_profile):
        url = f"/api/contatos/{self.mun.id}/enrich/apply/"
        payload = {
            "enriched_data": {
                "emails": ["novo@teste.gov.br"],
                "telefones": ["(11) 90000-0000"],
                "endereco": "Rua Nova, 123",
                "cargo": "Diretor",
                "orgao": "Secretaria X",
                "etiqueta_mala_direta": "Sr.\nContato Teste\nDiretor - Secretaria X",
            },
            "apply_fields": {
                "emails": True,
                "telefones": True,
                "endereco": True,
                "cargo": True,
                "orgao": True,
                "etiqueta_mala_direta": True,
            },
            "profile_mode": "existing",
            "profile_id": self.perfil_mun.id,
            "suggestion_source": "orchestrator",
        }
        resp = self.client.post(url, data=payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data.get("ok"))

        self.mun.refresh_from_db()
        audit = self.mun.auditoria_ia or {}
        self.assertIn("enrichment_events", audit)
        self.assertGreaterEqual(len(audit["enrichment_events"]), 1)

    @override_settings(ENRICHMENT_HITL_ENABLED=False)
    def test_feature_flag_blocks_preview(self):
        url = f"/api/contatos/{self.mun.id}/enrich/"
        resp = self.client.post(url, data={}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    @patch("atendimentos.enrichment_api.siga_mcp_server.update_contact_profile", return_value={"ok": True})
    @patch("atendimentos.enrichment_api.siga_mcp_server.configure_runtime")
    def test_apply_idempotency_key_replay(self, _cfg_runtime, update_profile):
        url = f"/api/contatos/{self.mun.id}/enrich/apply/"
        payload = {
            "enriched_data": {"emails": ["novo@teste.gov.br"]},
            "apply_fields": {"emails": True, "telefones": False, "endereco": False, "cargo": False, "orgao": False, "etiqueta_mala_direta": False},
            "suggestion_source": "orchestrator",
        }
        headers = {"HTTP_IDEMPOTENCY_KEY": "abc-123"}
        first = self.client.post(url, data=payload, format="json", **headers)
        second = self.client.post(url, data=payload, format="json", **headers)
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertTrue(second.data.get("idempotent_replay"))
        self.assertEqual(update_profile.call_count, 1)

    @patch("atendimentos.enrichment_api.enrichment_orchestrator.run_llm_react_once", return_value={"llm_provider": "groq", "model": "m", "final_text": "", "trace": []})
    @patch("atendimentos.enrichment_api.enrichment_orchestrator._default_model_for_provider", return_value="m")
    @patch("atendimentos.enrichment_api.enrichment_orchestrator._build_llm_client", return_value=("groq", object()))
    @patch("atendimentos.enrichment_api.enrichment_orchestrator._maybe_load_dotenv")
    @patch("atendimentos.enrichment_api.siga_mcp_server.configure_runtime")
    def test_preview_rate_limit(
        self, _cfg_runtime, _dotenv, _build, _model, _run_once
    ):
        cache.clear()
        original_rate = getattr(EnrichmentPreviewThrottle, "rate", None)
        try:
            EnrichmentPreviewThrottle.rate = "1/minute"
            url = f"/api/contatos/{self.mun.id}/enrich/"
            first = self.client.post(url, data={}, format="json")
            second = self.client.post(url, data={}, format="json")
        finally:
            if original_rate is None:
                delattr(EnrichmentPreviewThrottle, "rate")
            else:
                EnrichmentPreviewThrottle.rate = original_rate
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


class BuscaTextualTests(APITestCase):
    def setUp(self):
        self.group = Group.objects.create(name="Secretária")
        self.user = User.objects.create_user(username="busca_tester", password="123456")
        self.user.groups.add(self.group)

        self.conta = Conta.objects.create(nome="Gabinete Busca", nome_sigla="GB")
        self.perfil_user = PerfilUsuario.objects.create(usuario=self.user)
        self.perfil_user.contas.add(self.conta)

        self.categoria = CategoriaContato.objects.create(nome="SERVIDOR", ativa=True)

        self.joao = Municipe.objects.create(
            nome_completo="João da Silva",
            cpf="123.456.789-01",
            matricula_rh="45678-X",
        )
        self.joao.contas.add(self.conta)
        PerfilMunicipe.objects.create(
            municipe=self.joao,
            conta=self.conta,
            categoria=self.categoria,
            cargo="Assessor Parlamentar",
            instituicao="Secretaria da Educação",
            ativo=True,
        )

        self.maria = Municipe.objects.create(
            nome_completo="Maria José Santos",
            cpf="987.654.321-00",
            matricula_rh="99887-A",
        )
        self.maria.contas.add(self.conta)

        self.nilson = Municipe.objects.create(
            nome_completo="Nilson Pereira",
            telefones=[{"numero": "123"}],
            emails=[{"email": "email-invalido"}],
        )
        self.nilson.contas.add(self.conta)

        self.categoria_servidor_escala = CategoriaContato.objects.create(
            nome="SERVIDOR(A)", ativa=True
        )
        self.neusa = Municipe.objects.create(
            nome_completo="Neusa Marial da Costa",
            cargo="Analista",
        )
        self.neusa.contas.add(self.conta)
        PerfilMunicipe.objects.create(
            municipe=self.neusa,
            conta=self.conta,
            categoria=self.categoria_servidor_escala,
            cargo="Analista Administrativo",
            instituicao="Secretaria de Governo",
            ativo=True,
        )

        self.atendimento_joao = Atendimento.objects.create(
            protocolo="2026-TEST-001",
            titulo="Demanda de educação",
            descricao="Teste",
            conta=self.conta,
            municipe=self.joao,
            responsavel=self.user,
            created_by=self.user,
        )

        self.grupo_duplicata = uuid.uuid4()
        self.carlos_dup = Municipe.objects.create(
            nome_completo="Carlos Alberto Duarte",
            grupo_duplicado=self.grupo_duplicata,
        )
        self.carlos_dup.contas.add(self.conta)
        self.carlos_dup2 = Municipe.objects.create(
            nome_completo="Carlos A. Duarte",
            grupo_duplicado=self.grupo_duplicata,
        )
        self.carlos_dup2.contas.add(self.conta)

        self.client.force_authenticate(user=self.user)

    def test_normalizar_texto_remove_acentos(self):
        self.assertEqual(normalizar_texto("José"), "jose")
        self.assertEqual(normalizar_texto("João"), "joao")

    def test_tokenizar_busca_nome_remove_preposicoes(self):
        self.assertEqual(tokenizar_busca_nome("joão da silva"), ["joao", "silva"])
        self.assertEqual(tokenizar_busca_nome("joão silva"), ["joao", "silva"])

    def test_registro_corresponde_nome_sem_acento(self):
        item = {"nome_completo": "João da Silva", "nome_de_guerra": ""}
        self.assertTrue(registro_corresponde_termo(item, "joao silva"))
        self.assertTrue(registro_corresponde_termo(item, "silva joao"))
        self.assertFalse(registro_corresponde_termo(item, "jose silva"))

    def test_nao_confunde_substring_em_nomes_compostos(self):
        item = {"nome_completo": "ADENILSON CARPINA DE SOUZA", "nome_de_guerra": ""}
        self.assertFalse(registro_corresponde_termo(item, "nilson"))
        self.assertFalse(registro_corresponde_termo(item, "nilson carva"))

        nilson = {"nome_completo": "NILSON CARVALHO DE MORAES", "nome_de_guerra": ""}
        self.assertTrue(registro_corresponde_termo(nilson, "nilson"))
        self.assertTrue(registro_corresponde_termo(nilson, "nilson carva"))
        self.assertTrue(registro_corresponde_termo(nilson, "nilson moraes"))
        self.assertFalse(registro_corresponde_termo(nilson, "carva"))

    def test_token_corresponde_palavra_prefixo_sobrenome(self):
        self.assertTrue(token_corresponde_palavra("carva", "carvalho"))
        self.assertFalse(token_corresponde_palavra("carva", "cavalcante"))

    def test_registro_corresponde_cpf_e_matricula(self):
        item = {
            "nome_completo": "João da Silva",
            "cpf": "123.456.789-01",
            "matricula_rh": "45678-X",
        }
        self.assertTrue(registro_corresponde_termo(item, "12345678901"))
        self.assertTrue(registro_corresponde_termo(item, "45678"))

    def test_registro_corresponde_perfil(self):
        item = {
            "nome_completo": "João da Silva",
            "perfis__categoria__nome": "SERVIDOR",
            "perfis__cargo": "Assessor Parlamentar",
            "perfis__instituicao": "Secretaria da Educação",
        }
        self.assertTrue(registro_corresponde_termo(item, "assessor"))
        self.assertTrue(registro_corresponde_termo(item, "educacao"))

    def test_filtrar_queryset_municipe(self):
        qs = Municipe.objects.filter(contas=self.conta)
        encontrados = filtrar_queryset_municipe(qs, "joao silva")
        self.assertEqual(set(encontrados.values_list("id", flat=True)), {self.joao.id})

        por_cpf = filtrar_queryset_municipe(qs, "123.456.789-01")
        self.assertEqual(set(por_cpf.values_list("id", flat=True)), {self.joao.id})

        por_matricula = filtrar_queryset_municipe(qs, "45678")
        self.assertEqual(set(por_matricula.values_list("id", flat=True)), {self.joao.id})

    def test_api_municipes_busca_tolerante(self):
        resp = self.client.get("/api/municipes/", {"q": "joao silva"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = {item["id"] for item in _itens_lista_api(resp.data)}
        self.assertIn(self.joao.id, ids)
        self.assertNotIn(self.maria.id, ids)

    def test_api_municipes_lookup_mesma_logica(self):
        resp_lista = self.client.get("/api/municipes/", {"q": "45678"})
        resp_lookup = self.client.get("/api/municipes/lookup/", {"q": "45678"})
        self.assertEqual(resp_lista.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_lookup.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {item["id"] for item in _itens_lista_api(resp_lista.data)},
            {item["id"] for item in resp_lookup.data},
        )

    def test_filtrar_queryset_atendimento_por_municipe(self):
        qs = Atendimento.objects.filter(conta=self.conta)
        encontrados = filtrar_queryset_atendimento(qs, "joao silva")
        self.assertEqual(set(encontrados.values_list("id", flat=True)), {self.atendimento_joao.id})

    def test_api_atendimentos_busca_tolerante(self):
        resp = self.client.get("/api/atendimentos/", {"q": "joao silva"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = {item["id"] for item in _itens_lista_api(resp.data)}
        self.assertIn(self.atendimento_joao.id, ids)

    def test_api_saneamento_busca_nilson(self):
        resp = self.client.get(
            "/api/municipes/saneamento-dados/",
            {
                "problema": ["telefone_invalido", "email_invalido", "cpf_ausente"],
                "q": "nilson",
            },
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = {item["id"] for item in resp.data}
        self.assertIn(self.nilson.id, ids)
        self.assertNotIn(self.joao.id, ids)

    def test_api_escalas_servidores_lookup_neusa_marial(self):
        resp = self.client.get("/api/escalas/servidores/lookup/", {"q": "neusa marial"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = {item["id"] for item in resp.data}
        self.assertIn(self.neusa.id, ids)
        self.assertNotIn(self.joao.id, ids)

    def test_api_busca_global_tolerante(self):
        resp = self.client.get("/api/busca/", {"q": "joao silva"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        tipos = {(item["tipo"], item["id"]) for item in resp.data}
        self.assertIn(("municipe", self.joao.id), tipos)
        self.assertIn(("atendimento", self.atendimento_joao.id), tipos)

    @patch("atendimentos.services.ia_intelligence.buscar_municipes_semantico")
    @patch("atendimentos.services.ia_intelligence.buscar_atendimentos_semantico_otimizado")
    def test_api_busca_global_ia(self, mock_atendimentos_ia, mock_municipes_ia):
        mock_atendimentos_ia.return_value = [{
            "atendimento": self.atendimento_joao,
            "score": 0.91,
            "score_percentual": 91.0,
            "snippet": "Demanda de educação municipal",
        }]
        mock_municipes_ia.return_value = [{
            "municipe": self.joao,
            "score": 0.88,
            "score_percentual": 88.0,
        }]

        resp = self.client.get("/api/busca/", {"q": "educacao municipal", "ia": "1"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("resultados", resp.data)
        self.assertEqual(resp.data.get("modo_busca"), "ia")
        self.assertFalse(resp.data.get("ia_fallback"))
        ids = {(item["tipo"], item["id"]) for item in resp.data["resultados"]}
        self.assertIn(("atendimento", self.atendimento_joao.id), ids)
        self.assertIn(("municipe", self.joao.id), ids)
        self.assertTrue(
            any(item.get("score_match") is not None for item in resp.data["resultados"])
        )

    @patch(
        "atendimentos.services.ia_intelligence.buscar_municipes_semantico",
        side_effect=RuntimeError("IA offline"),
    )
    @patch(
        "atendimentos.services.ia_intelligence.buscar_atendimentos_semantico_otimizado",
        side_effect=RuntimeError("IA offline"),
    )
    def test_api_busca_global_ia_fallback_textual(self, _mock_at, _mock_mun):
        resp = self.client.get("/api/busca/", {"q": "joao silva", "ia": "1"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data.get("ia_fallback"))
        self.assertEqual(resp.data.get("modo_busca"), "textual")
        tipos = {(item["tipo"], item["id"]) for item in resp.data["resultados"]}
        self.assertIn(("municipe", self.joao.id), tipos)

    def test_api_duplicatas_busca_tolerante(self):
        resp = self.client.get(
            "/api/municipes/",
            {"tem_grupo_duplicado": "true", "q": "carlos alberto"},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = {item["id"] for item in _itens_lista_api(resp.data)}
        self.assertIn(self.carlos_dup.id, ids)
        self.assertIn(self.carlos_dup2.id, ids)
        self.assertNotIn(self.joao.id, ids)

    def test_api_municipes_paginacao(self):
        resp = self.client.get("/api/municipes/", {"page": 1, "page_size": 2})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("count", resp.data)
        self.assertIn("results", resp.data)
        self.assertLessEqual(len(resp.data["results"]), 2)

    def test_api_atendimentos_paginacao(self):
        resp = self.client.get("/api/atendimentos/", {"page": 1, "page_size": 1})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("count", resp.data)
        self.assertIn("results", resp.data)
        self.assertEqual(len(resp.data["results"]), 1)

    def test_api_duplicatas_sem_paginacao(self):
        resp = self.client.get("/api/municipes/", {"tem_grupo_duplicado": "true"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIsInstance(resp.data, list)


class PerfilMunicipeValidationTests(APITestCase):
    def setUp(self):
        self.group = Group.objects.create(name="Secretária")
        self.user = User.objects.create_user(username="perfil_tester", password="123456")
        self.user.groups.add(self.group)

        self.conta_a = Conta.objects.create(nome="Gabinete A", nome_sigla="GA")
        self.conta_b = Conta.objects.create(nome="Gabinete B", nome_sigla="GB")
        self.perfil_user = PerfilUsuario.objects.create(usuario=self.user)
        self.perfil_user.contas.add(self.conta_a)

        self.categoria = CategoriaContato.objects.create(nome="SERVIDOR", ativa=True)

        self.municipe = Municipe.objects.create(
            nome_completo="Ana Duplicata Teste",
            telefones=[{"numero": "(11) 99999-0000"}],
            emails=[{"email": "ana@teste.gov.br"}],
        )
        self.municipe.contas.add(self.conta_a, self.conta_b)

        self.perfil_a = PerfilMunicipe.objects.create(
            municipe=self.municipe,
            conta=self.conta_a,
            categoria=self.categoria,
            cargo="Assessor",
            instituicao="Sec A",
        )
        self.perfil_b = PerfilMunicipe.objects.create(
            municipe=self.municipe,
            conta=self.conta_b,
            categoria=self.categoria,
            cargo="Assessor",
            instituicao="Sec B",
        )
        self.perfil_dup = PerfilMunicipe.objects.create(
            municipe=self.municipe,
            conta=self.conta_a,
            categoria=self.categoria,
            cargo="Assessor",
            instituicao="Sec A Repetida",
        )

        self.client.force_authenticate(user=self.user)

    def _payload_base(self):
        return {
            "nome_completo": self.municipe.nome_completo,
            "telefones": self.municipe.telefones,
            "emails": self.municipe.emails,
            "contas": [self.conta_a.id],
        }

    def test_bloqueia_perfil_duplicado_no_payload(self):
        payload = self._payload_base()
        payload["perfis"] = [
            {"conta": self.conta_a.id, "categoria": self.categoria.id, "cargo": "Diretor"},
            {"conta": self.conta_a.id, "categoria": self.categoria.id, "cargo": "diretor"},
        ]
        resp = self.client.put(f"/api/municipes/{self.municipe.id}/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("perfis", resp.data)

    def test_usuario_nao_ve_perfis_de_outra_conta(self):
        resp = self.client.get(f"/api/municipes/{self.municipe.id}/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        contas_perfis = {p["conta"] for p in resp.data["perfis"]}
        self.assertIn(self.conta_a.id, contas_perfis)
        self.assertNotIn(self.conta_b.id, contas_perfis)

    def test_usuario_nao_pode_editar_perfil_outra_conta(self):
        payload = self._payload_base()
        payload["perfis"] = [
            {
                "id": self.perfil_b.id,
                "conta": self.conta_b.id,
                "categoria": self.categoria.id,
                "cargo": "Hacker",
            }
        ]
        resp = self.client.put(f"/api/municipes/{self.municipe.id}/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_preserva_perfis_fora_escopo(self):
        payload = self._payload_base()
        payload["perfis"] = [
            {
                "id": self.perfil_a.id,
                "conta": self.conta_a.id,
                "categoria": self.categoria.id,
                "cargo": "Assessor Atualizado",
            }
        ]
        resp = self.client.put(f"/api/municipes/{self.municipe.id}/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(PerfilMunicipe.objects.filter(id=self.perfil_b.id).exists())
        self.perfil_a.refresh_from_db()
        self.assertEqual(self.perfil_a.cargo, "Assessor Atualizado")

    def test_tem_perfis_duplicados_com_cargo_vazio(self):
        PerfilMunicipe.objects.create(
            municipe=self.municipe,
            conta=self.conta_a,
            categoria=self.categoria,
            cargo="",
            instituicao="Sec vazio",
        )
        resp = self.client.get(f"/api/municipes/{self.municipe.id}/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("tem_perfis_duplicados", resp.data)

    def test_tem_perfis_duplicados_flag_na_lista(self):
        resp = self.client.get("/api/municipes/", {"q": "Ana Duplicata"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        itens = _itens_lista_api(resp.data)
        ana = next(item for item in itens if item["id"] == self.municipe.id)
        self.assertTrue(ana.get("tem_perfis_duplicados"))


class RelatorioPerfilFiltroTests(APITestCase):
    def setUp(self):
        self.group = Group.objects.create(name="Secretária")
        self.user = User.objects.create_user(username="relatorio_tester", password="123456")
        self.user.groups.add(self.group)

        self.conta = Conta.objects.create(nome="Gabinete Rel", nome_sigla="GR")
        self.perfil_user = PerfilUsuario.objects.create(usuario=self.user)
        self.perfil_user.contas.add(self.conta)

        self.categoria_servidor = CategoriaContato.objects.create(nome="SERVIDOR REL", ativa=True)
        self.categoria_municipe = CategoriaContato.objects.create(nome="MUNICIPE REL", ativa=True)

        self.municipe_servidor = Municipe.objects.create(
            nome_completo="Pedro Servidor",
            telefones=[{"numero": "(11) 90000-0001"}],
            emails=[{"email": "pedro@teste.gov.br"}],
        )
        self.municipe_servidor.contas.add(self.conta)
        PerfilMunicipe.objects.create(
            municipe=self.municipe_servidor,
            conta=self.conta,
            categoria=self.categoria_servidor,
            cargo="ASSESSOR PARLAMENTAR",
            ativo=True,
        )

        self.municipe_outro = Municipe.objects.create(
            nome_completo="Lucia Municipe",
            telefones=[{"numero": "(11) 90000-0002"}],
            emails=[{"email": "lucia@teste.gov.br"}],
        )
        self.municipe_outro.contas.add(self.conta)
        PerfilMunicipe.objects.create(
            municipe=self.municipe_outro,
            conta=self.conta,
            categoria=self.categoria_municipe,
            cargo="APOSENTADO",
            ativo=True,
        )

        self.at_servidor = Atendimento.objects.create(
            protocolo="REL-001",
            titulo="Demanda servidor",
            descricao="Teste",
            conta=self.conta,
            municipe=self.municipe_servidor,
            responsavel=self.user,
            created_by=self.user,
        )
        self.at_municipe = Atendimento.objects.create(
            protocolo="REL-002",
            titulo="Demanda munícipe",
            descricao="Teste",
            conta=self.conta,
            municipe=self.municipe_outro,
            responsavel=self.user,
            created_by=self.user,
        )

        self.client.force_authenticate(user=self.user)

    def test_filtro_por_categoria_contato(self):
        resp = self.client.get(
            "/api/relatorios/atendimentos-por-assunto/",
            {"categoria_contato_id": self.categoria_servidor.id},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        total = sum(item["total"] for item in resp.data)
        self.assertEqual(total, 1)

    def test_filtro_por_cargo(self):
        resp = self.client.get(
            "/api/relatorios/atendimentos-por-assunto/",
            {"cargo": "assessor parlamentar"},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        total = sum(item["total"] for item in resp.data)
        self.assertEqual(total, 1)

    def test_filtro_categoria_e_cargo_combinados(self):
        resp = self.client.get(
            "/api/relatorios/atendimentos-por-assunto/",
            {
                "categoria_contato_id": self.categoria_servidor.id,
                "cargo": "ASSESSOR PARLAMENTAR",
            },
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        total = sum(item["total"] for item in resp.data)
        self.assertEqual(total, 1)

        resp_vazio = self.client.get(
            "/api/relatorios/atendimentos-por-assunto/",
            {
                "categoria_contato_id": self.categoria_municipe.id,
                "cargo": "ASSESSOR PARLAMENTAR",
            },
        )
        self.assertEqual(resp_vazio.status_code, status.HTTP_200_OK)
        self.assertEqual(sum(item["total"] for item in resp_vazio.data), 0)

    def test_endpoint_opcoes_filtro_perfil(self):
        resp = self.client.get("/api/relatorios/filtros-perfil/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("categorias", resp.data)
        self.assertIn("cargos", resp.data)
        self.assertIn("ASSESSOR PARLAMENTAR", resp.data["cargos"])


class LogCrmAuditoriaTests(APITestCase):
    """Épico 11 — auditoria CRM (munícipes, perfis, categorias, mesclagem)."""

    def setUp(self):
        self.group_sec = Group.objects.create(name="Secretária")
        self.group_membro = Group.objects.create(name="Membro do Gabinete")
        self.user = User.objects.create_user(username="sec_logs", password="123456")
        self.user.groups.add(self.group_sec)
        self.membro = User.objects.create_user(username="membro_logs", password="123456")
        self.membro.groups.add(self.group_membro)

        self.conta = Conta.objects.create(nome="Gab Logs", nome_sigla="GL")
        perfil_sec = PerfilUsuario.objects.create(usuario=self.user)
        perfil_sec.contas.add(self.conta)
        perfil_membro = PerfilUsuario.objects.create(usuario=self.membro)
        perfil_membro.contas.add(self.conta)

        self.categoria = CategoriaContato.objects.create(nome="LOG TEST CAT", ativa=True)
        self.client.force_authenticate(user=self.user)

    def _criar_municipe(self, nome, sufixo=""):
        mun = Municipe.objects.create(
            nome_completo=nome,
            telefones=[{"numero": f"(11) 9000-00{sufixo or '00'}"}],
            emails=[{"email": f"log{sufixo or ''}@test.gov.br"}],
        )
        mun.contas.add(self.conta)
        PerfilMunicipe.objects.create(
            municipe=mun,
            conta=self.conta,
            categoria=self.categoria,
            cargo="Cargo Teste",
            ativo=True,
        )
        return mun

    def test_edicao_municipe_gera_log_com_payload(self):
        mun = self._criar_municipe("Antes Log", "01")
        LogDeAtividade.objects.filter(acao='MUNICIPE_EDICAO', object_id=mun.id).delete()

        resp = self.client.patch(
            f"/api/municipes/{mun.id}/",
            {
                "nome_completo": "Depois Log",
                "telefones": [{"numero": "(11) 9000-0001"}],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        log = LogDeAtividade.objects.filter(acao='MUNICIPE_EDICAO', object_id=mun.id).latest('timestamp')
        self.assertEqual(log.usuario, self.user)
        self.assertEqual(log.conta_id, self.conta.id)
        self.assertIn('alteracoes', log.payload)
        self.assertEqual(log.payload['alteracoes']['nome_completo']['depois'], 'DEPOIS LOG')

    def test_mesclagem_gera_log_mesclagem(self):
        principal = self._criar_municipe("Principal Merge", "02")
        duplicado = self._criar_municipe("Duplicado Merge", "03")
        duplicado_id = duplicado.id

        resp = self.client.post(
            "/api/municipes/mesclar-duplicatas/",
            {"id_principal": principal.id, "id_duplicado": duplicado_id},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        log = LogDeAtividade.objects.filter(acao='MESCLAGEM', object_id=principal.id).latest('timestamp')
        self.assertEqual(log.usuario, self.user)
        self.assertEqual(log.payload['id_duplicado'], duplicado_id)
        self.assertEqual(log.payload['id_principal'], principal.id)
        self.assertFalse(Municipe.objects.filter(pk=duplicado_id).exists())

    def test_categoria_desativacao_gera_log(self):
        cat = CategoriaContato.objects.create(nome="CAT DESATIVAR", ativa=True)
        LogDeAtividade.objects.filter(object_id=cat.id, acao__startswith='CATEGORIA').delete()

        resp = self.client.patch(
            f"/api/contatos/categorias/{cat.id}/",
            {"nome": "CAT DESATIVAR", "ativa": False},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        log = LogDeAtividade.objects.filter(
            acao='CATEGORIA_DESATIVACAO', object_id=cat.id
        ).latest('timestamp')
        self.assertEqual(log.usuario, self.user)
        self.assertIn('alteracoes', log.payload)

    def test_api_logs_crm_list_secretaria(self):
        mun = self._criar_municipe("Listagem Log", "04")
        LogDeAtividade.objects.filter(object_id=mun.id).delete()
        self.client.patch(
            f"/api/municipes/{mun.id}/",
            {"nome_completo": "Listagem Log Editado", "telefones": [{"numero": "(11) 9000-0004"}]},
            format="json",
        )

        resp = self.client.get("/api/logs-crm/", {"acao": "MUNICIPE_EDICAO", "entidade": "municipe"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        itens = _itens_lista_api(resp.data)
        self.assertTrue(any(item["object_id"] == mun.id for item in itens))

    def test_api_logs_crm_membro_apenas_contatos(self):
        self.client.force_authenticate(user=self.membro)
        resp = self.client.get("/api/logs-crm/", {"grupo": "contatos"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)


class SlaAtendimentoTests(APITestCase):
    def setUp(self):
        from datetime import timedelta

        self.timedelta = timedelta
        self.group = Group.objects.create(name="Secretária")
        self.user = User.objects.create_user(username="sla_tester", password="123456")
        self.user.groups.add(self.group)
        self.conta = Conta.objects.create(nome="Gab SLA", nome_sigla="GS")
        self.perfil_user = PerfilUsuario.objects.create(usuario=self.user)
        self.perfil_user.contas.add(self.conta)
        self.categoria = CategoriaContato.objects.create(nome="MUN SLA", ativa=True)
        self.municipe = Municipe.objects.create(
            nome_completo="Munícipe SLA",
            telefones=[{"numero": "(11) 8888-0000"}],
        )
        self.municipe.contas.add(self.conta)
        PerfilMunicipe.objects.create(
            municipe=self.municipe,
            conta=self.conta,
            categoria=self.categoria,
            ativo=True,
        )
        self.assunto = AssuntoAtendimento.objects.create(
            nome="SAÚDE SLA", codigo="saude_sla", ativo=True
        )
        self.client.force_authenticate(user=self.user)

    def _criar_atendimento(self, protocolo="SLA-001"):
        return Atendimento.objects.create(
            protocolo=protocolo,
            titulo="Atendimento SLA",
            descricao="Teste SLA",
            conta=self.conta,
            municipe=self.municipe,
            assunto=self.assunto,
            responsavel=self.user,
            created_by=self.user,
        )

    def test_novo_atendimento_recebe_prazos(self):
        at = self._criar_atendimento()
        at.refresh_from_db()
        self.assertIsNotNone(at.prazo_resposta)
        self.assertIsNotNone(at.prazo_conclusao)
        self.assertLess(at.prazo_resposta, at.prazo_conclusao)

    def test_filtro_sla_vencido(self):
        from django.utils import timezone

        at = self._criar_atendimento("SLA-VENC")
        Atendimento.objects.filter(pk=at.pk).update(
            prazo_conclusao=timezone.now() - self.timedelta(days=1),
            status="ABERTO",
        )
        resp = self.client.get("/api/atendimentos/", {"sla_status": "VENCIDO"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = [item["id"] for item in _itens_lista_api(resp.data)]
        self.assertIn(at.id, ids)

    def test_api_listagem_expoe_sla_status(self):
        at = self._criar_atendimento("SLA-LIST")
        resp = self.client.get("/api/atendimentos/", {"q": "SLA-LIST"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        item = next(i for i in _itens_lista_api(resp.data) if i["id"] == at.id)
        self.assertIn(item["sla_status"], ("NO_PRAZO", "EM_RISCO", "VENCIDO", None))
        self.assertTrue(item.get("sla_status_display"))

    def test_relatorio_sla(self):
        self._criar_atendimento("SLA-REL")
        resp = self.client.get("/api/relatorios/sla/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("resumo", resp.data)
        self.assertIn("por_conta", resp.data)
        self.assertIn("por_assunto", resp.data)
        self.assertGreaterEqual(resp.data["resumo"]["com_sla"], 1)


class DuplicatasContadorTests(APITestCase):
    def setUp(self):
        self.group = Group.objects.create(name="Secretária")
        self.user = User.objects.create_user(username="sec_dup_cnt", password="123456")
        self.user.groups.add(self.group)
        self.conta = Conta.objects.create(nome="Gab Dup Cnt", nome_sigla="GDC")
        perfil = PerfilUsuario.objects.create(usuario=self.user)
        perfil.contas.add(self.conta)
        self.client.force_authenticate(user=self.user)
        self.grupo_uuid = uuid.uuid4()

    def _criar_municipe(self, nome, grupo=None):
        mun = Municipe.objects.create(nome_completo=nome, grupo_duplicado=grupo)
        mun.contas.add(self.conta)
        return mun

    def test_contador_grupos_validos(self):
        self._criar_municipe("Dup A", self.grupo_uuid)
        self._criar_municipe("Dup B", self.grupo_uuid)
        self._criar_municipe("Orfao", uuid.uuid4())

        resp = self.client.get("/api/municipes/duplicatas/contador/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["total_grupos"], 1)
        self.assertEqual(resp.data["total_contatos"], 2)

    def test_contador_respeita_escopo_conta(self):
        outra_conta = Conta.objects.create(nome="Outra", nome_sigla="OUT")
        grupo2 = uuid.uuid4()
        self._criar_municipe("Meu 1", self.grupo_uuid)
        self._criar_municipe("Meu 2", self.grupo_uuid)
        fora = Municipe.objects.create(nome_completo="Fora 1", grupo_duplicado=grupo2)
        fora.contas.add(outra_conta)
        fora2 = Municipe.objects.create(nome_completo="Fora 2", grupo_duplicado=grupo2)
        fora2.contas.add(outra_conta)

        resp = self.client.get("/api/municipes/duplicatas/contador/")
        self.assertEqual(resp.data["total_grupos"], 1)
        self.assertEqual(resp.data["total_contatos"], 2)


class AdminContadoresTests(APITestCase):
    """Épico 9 — contadores no Django Admin."""

    def setUp(self):
        self.categoria_a = CategoriaContato.objects.create(nome="ADMIN CAT A", ativa=True)
        self.categoria_b = CategoriaContato.objects.create(nome="ADMIN CAT B", ativa=True)
        self.conta = Conta.objects.create(nome="Gab Admin", nome_sigla="GA")

        self.m1 = Municipe.objects.create(nome_completo="Municipe Admin 1")
        self.m2 = Municipe.objects.create(nome_completo="Municipe Admin 2")
        self.m3 = Municipe.objects.create(nome_completo="Municipe Admin 3")
        for m in (self.m1, self.m2, self.m3):
            m.contas.add(self.conta)

        PerfilMunicipe.objects.create(
            municipe=self.m1, conta=self.conta, categoria=self.categoria_a, cargo="A1", ativo=True,
        )
        PerfilMunicipe.objects.create(
            municipe=self.m2, conta=self.conta, categoria=self.categoria_a, cargo="A2", ativo=True,
        )
        PerfilMunicipe.objects.create(
            municipe=self.m3, conta=self.conta, categoria=self.categoria_b, cargo="B1", ativo=True,
        )

    def test_categoria_contato_admin_anota_total_municipes(self):
        from atendimentos.admin import CategoriaContatoAdmin
        from django.contrib.admin.sites import AdminSite

        admin_obj = CategoriaContatoAdmin(CategoriaContato, AdminSite())
        qs = admin_obj.get_queryset(request=None)
        por_nome = {c.nome: c._total_municipes for c in qs}
        self.assertEqual(por_nome["ADMIN CAT A"], 2)
        self.assertEqual(por_nome["ADMIN CAT B"], 1)

    def test_resumo_categorias_municipes(self):
        from atendimentos.admin_utils import resumo_categorias_municipes

        qs = Municipe.objects.filter(id__in=[self.m1.id, self.m2.id, self.m3.id])
        resumo = resumo_categorias_municipes(qs)
        por_nome = {item['nome']: item['total'] for item in resumo}
        self.assertEqual(por_nome["ADMIN CAT A"], 2)
        self.assertEqual(por_nome["ADMIN CAT B"], 1)


class UnificarMunicipesPreviewTests(APITestCase):
    """Épico 4 — dry-run de unificação de munícipes."""

    def setUp(self):
        self.group_sec = Group.objects.create(name="Secretária")
        self.user = User.objects.create_user(username="sec_unif", password="123456")
        self.user.groups.add(self.group_sec)

        self.conta = Conta.objects.create(nome="Gab Unif", nome_sigla="GU")
        perfil_sec = PerfilUsuario.objects.create(usuario=self.user)
        perfil_sec.contas.add(self.conta)

        self.categoria = CategoriaContato.objects.create(nome="UNIF CAT", ativa=True)
        self.client.force_authenticate(user=self.user)

    def _criar_municipe(self, nome, cpf=None, sufixo=""):
        mun = Municipe.objects.create(
            nome_completo=nome,
            cpf=cpf,
            telefones=[{"numero": f"(11) 9100-00{sufixo or '00'}"}],
            emails=[{"email": f"unif{sufixo or ''}@test.gov.br"}],
        )
        mun.contas.add(self.conta)
        PerfilMunicipe.objects.create(
            municipe=mun,
            conta=self.conta,
            categoria=self.categoria,
            cargo="Cargo Unif",
            ativo=True,
        )
        return mun

    def test_preview_nao_persiste_e_nao_gera_log(self):
        principal = self._criar_municipe("Principal Preview", cpf="11111111111", sufixo="01")
        duplicado = self._criar_municipe("Duplicado Preview", cpf="22222222222", sufixo="02")
        PerfilMunicipe.objects.filter(municipe=duplicado).update(cargo="Cargo Duplicado")
        duplicado_id = duplicado.id
        LogDeAtividade.objects.all().delete()

        resp = self.client.post(
            "/api/municipes/unificar/preview/",
            {"id_principal": principal.id, "id_duplicado": duplicado_id},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["pode_unificar"])
        self.assertTrue(resp.data["duplicado_sera_excluido"])
        self.assertGreaterEqual(resp.data["links_migrados"], 1)
        self.assertTrue(Municipe.objects.filter(pk=duplicado_id).exists())
        self.assertFalse(LogDeAtividade.objects.filter(acao="MESCLAGEM").exists())

    def test_preview_detecta_conflito_perfil(self):
        principal = self._criar_municipe("Principal Conflito", sufixo="03")
        duplicado = self._criar_municipe("Duplicado Conflito", sufixo="04")

        resp = self.client.post(
            "/api/municipes/unificar/preview/",
            {"id_principal": principal.id, "id_duplicado": duplicado.id},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["pode_unificar"])
        self.assertEqual(len(resp.data["vinculos_descartar"]), 1)
        self.assertEqual(resp.data["vinculos_descartar"][0]["quantidade"], 1)

    def test_preview_mesmo_id_bloqueia(self):
        principal = self._criar_municipe("Mesmo ID", sufixo="05")
        resp = self.client.post(
            "/api/municipes/unificar/preview/",
            {"id_principal": principal.id, "id_duplicado": principal.id},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

