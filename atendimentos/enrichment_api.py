from __future__ import annotations

import json
import logging
import os
import sys
import time
import ast
import re
from pathlib import Path
from typing import Any, Dict, List

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from django.utils import timezone

from .models import CategoriaContato, Municipe, PerfilMunicipe
from .permissions import CanAccessContacts, CanEditMunicipeDetails, is_in_group
from .throttles import EnrichmentApplyThrottle, EnrichmentPreviewThrottle


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import enrichment_orchestrator  # type: ignore  # noqa: E402
import enrichment_agent  # type: ignore  # noqa: E402
import siga_mcp_server  # type: ignore  # noqa: E402

logger = logging.getLogger(__name__)


def _is_enrichment_enabled_for_user(user: Any) -> bool:
    if not bool(getattr(settings, "ENRICHMENT_HITL_ENABLED", True)):
        return False
    if user.is_superuser:
        return True

    allowed_groups = set(getattr(settings, "ENRICHMENT_ALLOWED_GROUPS", []) or [])
    if allowed_groups and not user.groups.filter(name__in=allowed_groups).exists():
        return False

    allowed_conta_ids = set(getattr(settings, "ENRICHMENT_ALLOWED_CONTA_IDS", []) or [])
    if allowed_conta_ids:
        user_contas = set()
        if hasattr(user, "perfil"):
            user_contas = set(user.perfil.contas.values_list("id", flat=True))
        if user_contas.isdisjoint(allowed_conta_ids):
            return False
    return True


def _log_event(event: str, **kwargs: Any) -> None:
    payload = {"event": event, **kwargs}
    logger.info("enrichment_event=%s", json.dumps(payload, ensure_ascii=False))


def _record_metric(name: str, increment: int = 1) -> int:
    key = f"enrichment:metric:{name}"
    try:
        current = cache.get(key) or 0
        current = int(current) + increment
        cache.set(key, current, timeout=24 * 3600)
        return current
    except Exception:
        return 0


def _maybe_alert_error_spike() -> None:
    errors = _record_metric("errors_total", 0)
    if errors and errors % 10 == 0:
        logger.warning("enrichment_alert=high_error_volume total_errors=%s", errors)


def _municipe_queryset_for_user(user: Any):
    from .services.escopo_operador_crm import aplicar_escopo_municipes_queryset

    qs = Municipe.objects.prefetch_related("contas", "perfis", "perfis__categoria")
    return aplicar_escopo_municipes_queryset(qs, user)


def _extract_current_data(mun: Municipe) -> Dict[str, Any]:
    emails = []
    for item in (mun.emails or []):
        if isinstance(item, dict):
            val = str(item.get("email", "")).strip()
            if val:
                emails.append(val)
        elif isinstance(item, str) and item.strip():
            emails.append(item.strip())

    telefones = []
    for item in (mun.telefones or []):
        if isinstance(item, dict):
            val = str(item.get("numero", "")).strip()
            if val:
                telefones.append(val)
        elif isinstance(item, str) and item.strip():
            telefones.append(item.strip())

    endereco_raw = mun.endereco or {}
    if isinstance(endereco_raw, dict):
        if str(endereco_raw.get("texto_livre", "")).strip():
            endereco = str(endereco_raw.get("texto_livre", "")).strip()
        else:
            endereco = " ".join(
                [
                    str(endereco_raw.get("logradouro", "")).strip(),
                    str(endereco_raw.get("numero", "")).strip(),
                    str(endereco_raw.get("bairro", "")).strip(),
                    str(endereco_raw.get("cidade", "")).strip(),
                    str(endereco_raw.get("uf", "")).strip(),
                    str(endereco_raw.get("cep", "")).strip(),
                ]
            ).strip()
    else:
        endereco = str(endereco_raw or "").strip()

    return {
        "telefones": sorted(set(telefones)),
        "emails": sorted(set(emails)),
        "endereco": endereco or "",
        "cargo": (mun.cargo or "").strip(),
        "orgao": (mun.orgao or "").strip(),
        "etiqueta_mala_direta": (mun.dados_etiqueta or "").strip(),
    }


def _extract_profiles_for_user(mun: Municipe, user: Any) -> List[Dict[str, Any]]:
    user_conta_ids = set()
    if hasattr(user, "perfil"):
        user_conta_ids = set(user.perfil.contas.values_list("id", flat=True))
    rows: List[Dict[str, Any]] = []
    for perf in mun.perfis.filter(ativo=True).select_related("conta", "categoria"):
        conta_id = getattr(perf.conta, "id", None)
        if user_conta_ids and conta_id not in user_conta_ids and not user.is_superuser:
            continue
        rows.append(
            {
                "id": perf.id,
                "conta_id": conta_id,
                "conta_nome": getattr(perf.conta, "nome", "") if perf.conta else "",
                "categoria_id": getattr(perf.categoria, "id", None),
                "categoria_nome": getattr(perf.categoria, "nome", "") if perf.categoria else "",
                "cargo": (perf.cargo or "").strip(),
                "orgao": (perf.instituicao or "").strip(),
                "ativo": bool(perf.ativo),
            }
        )
    return rows


def _first_non_empty(*values: Any) -> str:
    for v in values:
        txt = str(v or "").strip()
        if txt:
            return txt
    return ""


def _infer_endereco_from_etiqueta(etiqueta: str) -> str:
    raw = str(etiqueta or "").strip()
    if not raw:
        return ""
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return ""
    # Formato esperado da etiqueta:
    # [Tratamento]
    # [Nome]
    # [Cargo e Órgão]
    # [Endereço linha 1]
    # [Endereço linha 2]
    if len(lines) >= 5:
        return f"{lines[3]} {lines[4]}".strip()
    if len(lines) >= 4:
        return lines[3]
    return ""


def _normalize_etiqueta_mala_direta(value: Any) -> str:
    raw = value
    if isinstance(raw, list):
        lines = [str(x or "").strip() for x in raw if str(x or "").strip()]
        raw = "\n".join(lines)
    if isinstance(raw, str):
        txt = raw.strip()
        # Suporta casos em que veio serializado como lista em string.
        if txt.startswith("[") and txt.endswith("]"):
            parsed = None
            try:
                parsed = json.loads(txt)
            except Exception:
                try:
                    parsed = ast.literal_eval(txt)
                except Exception:
                    parsed = None
            if isinstance(parsed, list):
                txt = "\n".join([str(x or "").strip() for x in parsed if str(x or "").strip()])
        txt = txt.replace("\\n", "\n")
        normalized_lines: List[str] = []
        for ln in txt.splitlines():
            line = str(ln or "").strip()
            if not line:
                continue
            line = re.sub(
                r"^\[(Tratamento|Nome|Cargo e Órgão|Cargo e Orgao|Endereço linha 1|Endereço linha 2 com CEP/Cidade/UF)\]\s*",
                "",
                line,
                flags=re.IGNORECASE,
            ).strip()
            if line:
                normalized_lines.append(line)
        return "\n".join(normalized_lines).strip()
    return str(raw or "").strip()


def _extract_suggestion_and_sources(trace: List[Dict[str, Any]]) -> Dict[str, Any]:
    fontes: List[str] = []
    observacoes: List[str] = []
    suggestion: Dict[str, Any] | None = None

    for step in trace:
        if step.get("tool") == "search_authority_web":
            result = step.get("result") or {}
            for f in result.get("fontes") or []:
                f_str = str(f).strip()
                if f_str and f_str not in fontes:
                    fontes.append(f_str)
            for o in result.get("observacoes") or []:
                o_str = str(o).strip()
                if o_str and o_str not in observacoes:
                    observacoes.append(o_str)

    for step in reversed(trace):
        if step.get("tool") == "update_contact_profile":
            inp = step.get("input") or {}
            enriched = inp.get("enriched_data")
            if isinstance(enriched, dict):
                suggestion = enriched
                break

    if suggestion is None:
        suggestion = {
            "telefones": [],
            "emails": [],
            "endereco": "",
            "cargo": "",
            "orgao": "",
            "etiqueta_mala_direta": "",
        }

    return {"suggestion": suggestion, "fontes": fontes, "observacoes": observacoes}


def _extract_suggestion_from_final_text(final_text: str) -> Dict[str, Any] | None:
    raw = str(final_text or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    keys = {"telefones", "emails", "endereco", "cargo", "orgao", "etiqueta_mala_direta"}
    if not keys.intersection(set(data.keys())):
        return None
    return {
        "telefones": data.get("telefones") or [],
        "emails": data.get("emails") or [],
        "endereco": str(data.get("endereco") or "").strip(),
        "cargo": str(data.get("cargo") or "").strip(),
        "orgao": str(data.get("orgao") or "").strip(),
        "etiqueta_mala_direta": str(data.get("etiqueta_mala_direta") or "").strip(),
    }


def _has_meaningful_novelty(current_data: Dict[str, Any], suggestion: Dict[str, Any]) -> bool:
    curr_emails = {str(x).strip().lower() for x in (current_data.get("emails") or []) if str(x).strip()}
    sug_emails = {str(x).strip().lower() for x in (suggestion.get("emails") or []) if str(x).strip()}
    curr_phones = {str(x).strip() for x in (current_data.get("telefones") or []) if str(x).strip()}
    sug_phones = {str(x).strip() for x in (suggestion.get("telefones") or []) if str(x).strip()}
    if sug_emails - curr_emails:
        return True
    if sug_phones - curr_phones:
        return True
    for key in ("endereco", "cargo", "orgao", "etiqueta_mala_direta"):
        a = str(current_data.get(key, "") or "").strip()
        b = str(suggestion.get(key, "") or "").strip()
        if b and b != a:
            return True
    return False


def _suggestion_from_enrichment_agent(contact_id: int) -> Dict[str, Any]:
    """
    Fallback determinístico: usa pipeline do enrichment_agent quando o resultado
    do orquestrador não traz novidade útil.
    """
    client = enrichment_agent.LocalMCPClient(registry=siga_mcp_server.TOOL_REGISTRY)
    planner = enrichment_agent.RegexLLMPlanner()
    orch = enrichment_agent.EnrichmentOrchestrator(mcp_client=client, planner=planner)
    result = orch.run_once(contact_id=contact_id, min_score=0.0, force_apply=False)
    data = result.get("enriched_data") or {}
    return {
        "suggestion": {
            "telefones": data.get("telefones") or [],
            "emails": data.get("emails") or [],
            "endereco": str(data.get("endereco") or "").strip(),
            "cargo": str(data.get("cargo") or "").strip(),
            "orgao": str(data.get("orgao") or "").strip(),
            "etiqueta_mala_direta": str(data.get("etiqueta_mala_direta") or "").strip(),
        },
        "fontes": result.get("fontes") or [],
        "observacoes": result.get("observacoes") or [],
    }


class ContatoEnrichPreviewView(APIView):
    permission_classes = [permissions.IsAuthenticated, CanAccessContacts]
    throttle_classes = [EnrichmentPreviewThrottle]

    def post(self, request, pk: int):
        started_at = time.monotonic()
        if not _is_enrichment_enabled_for_user(request.user):
            return Response({"detail": "Enriquecimento IA indisponível para seu perfil no momento."}, status=status.HTTP_403_FORBIDDEN)
        mun = _municipe_queryset_for_user(request.user).filter(pk=pk, ativo=True).first()
        if not mun:
            return Response({"detail": "Contato não encontrado."}, status=status.HTTP_404_NOT_FOUND)

        try:
            enrichment_orchestrator._maybe_load_dotenv()
            llm_provider, llm_client = enrichment_orchestrator._build_llm_client()
            model = enrichment_orchestrator._default_model_for_provider(llm_provider)
            max_steps = int(request.data.get("max_steps") or 6)
            max_steps = max(2, min(max_steps, 12))

            siga_mcp_server.configure_runtime(
                mode="django",
                dry_run=True,
                django_project_root="/var/www/gabinete/siga-gabinete",
            )
            tool_client = enrichment_orchestrator.LocalToolClient(registry=siga_mcp_server.TOOL_REGISTRY)
            result = enrichment_orchestrator.run_llm_react_once(
                tool_client=tool_client,
                model=model,
                max_steps=max_steps,
                contact_id=pk,
                audit_file=str(os.getenv("ENRICH_AUDIT_FILE", "")).strip(),
                llm_client=llm_client,
                llm_provider=llm_provider,
            )
        except Exception as exc:
            _record_metric("preview_error_total")
            _maybe_alert_error_spike()
            _log_event(
                "enrich_preview_error",
                user_id=getattr(request.user, "id", None),
                contact_id=pk,
                error=str(exc),
                duration_ms=int((time.monotonic() - started_at) * 1000),
            )
            return Response(
                {"detail": f"Falha no enriquecimento IA: {exc}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        parsed = _extract_suggestion_and_sources(result.get("trace") or [])
        decision_data = result.get("decision_data") if isinstance(result.get("decision_data"), dict) else None
        suggestion_source = "orchestrator"
        if not any((parsed["suggestion"] or {}).get(k) for k in ("emails", "telefones", "endereco", "cargo", "orgao", "etiqueta_mala_direta")):
            from_final = _extract_suggestion_from_final_text(result.get("final_text") or "")
            if from_final:
                parsed["suggestion"] = from_final
                suggestion_source = "orchestrator_final_text"
        current_data = _extract_current_data(mun)
        suggestion = parsed["suggestion"]
        # Fallback para UX: se IA vier vazia em algum campo, mostrar valor atual como base editável.
        suggestion = {
            "telefones": suggestion.get("telefones") or current_data["telefones"],
            "emails": suggestion.get("emails") or current_data["emails"],
            "endereco": _first_non_empty(suggestion.get("endereco"), current_data["endereco"]),
            "cargo": _first_non_empty(suggestion.get("cargo"), current_data["cargo"]),
            "orgao": _first_non_empty(suggestion.get("orgao"), current_data["orgao"]),
            "etiqueta_mala_direta": _normalize_etiqueta_mala_direta(
                _first_non_empty(suggestion.get("etiqueta_mala_direta"), current_data["etiqueta_mala_direta"])
            ),
        }
        if not suggestion["endereco"]:
            suggestion["endereco"] = _infer_endereco_from_etiqueta(suggestion.get("etiqueta_mala_direta", ""))
        if not _has_meaningful_novelty(current_data=current_data, suggestion=suggestion):
            fallback = _suggestion_from_enrichment_agent(contact_id=pk)
            fallback_suggestion = fallback["suggestion"]
            if _has_meaningful_novelty(current_data=current_data, suggestion=fallback_suggestion):
                suggestion = fallback_suggestion
                suggestion_source = "enrichment_agent_fallback"
                # mescla fontes/observações para transparência ao operador
                merged_fontes = list(dict.fromkeys([*(parsed["fontes"] or []), *(fallback["fontes"] or [])]))
                merged_obs = list(dict.fromkeys([*(parsed["observacoes"] or []), "Fallback aplicado: enrichment_agent.", *(fallback["observacoes"] or [])]))
                parsed["fontes"] = merged_fontes
                parsed["observacoes"] = merged_obs
        user_contas = []
        if hasattr(request.user, "perfil"):
            user_contas = [{"id": c.id, "nome": c.nome} for c in request.user.perfil.contas.all()]
        _record_metric("preview_success_total")
        _log_event(
            "enrich_preview_success",
            user_id=getattr(request.user, "id", None),
            contact_id=pk,
            source=suggestion_source,
            duration_ms=int((time.monotonic() - started_at) * 1000),
        )
        return Response(
            {
                "ok": True,
                "contact_id": pk,
                "dry_run": True,
                "llm_provider": result.get("llm_provider"),
                "model": result.get("model"),
                "current_data": current_data,
                "suggestion": suggestion,
                "suggestion_source": suggestion_source,
                "fontes": parsed["fontes"],
                "observacoes": parsed["observacoes"],
                "profiles_for_user": _extract_profiles_for_user(mun, request.user),
                "user_accounts": user_contas,
                "final_text": result.get("final_text") or "",
                "trace": result.get("trace") or [],
                "decision_data": decision_data,
            },
            status=status.HTTP_200_OK,
        )


class ContatoEnrichApplyView(APIView):
    permission_classes = [permissions.IsAuthenticated, CanEditMunicipeDetails]
    throttle_classes = [EnrichmentApplyThrottle]

    def post(self, request, pk: int):
        started_at = time.monotonic()
        if not _is_enrichment_enabled_for_user(request.user):
            return Response({"detail": "Enriquecimento IA indisponível para seu perfil no momento."}, status=status.HTTP_403_FORBIDDEN)
        mun = _municipe_queryset_for_user(request.user).filter(pk=pk, ativo=True).first()
        if not mun:
            return Response({"detail": "Contato não encontrado."}, status=status.HTTP_404_NOT_FOUND)

        payload = request.data.get("enriched_data", request.data)
        apply_fields = request.data.get("apply_fields") or {}
        if not isinstance(payload, dict):
            return Response(
                {"detail": "Payload inválido. Envie um objeto em `enriched_data`."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not isinstance(apply_fields, dict):
            apply_fields = {}

        current = _extract_current_data(mun)
        current_profiles = _extract_profiles_for_user(mun, request.user)
        fields = {
            "emails": bool(apply_fields.get("emails", True)),
            "telefones": bool(apply_fields.get("telefones", True)),
            "endereco": bool(apply_fields.get("endereco", True)),
            "etiqueta_mala_direta": bool(apply_fields.get("etiqueta_mala_direta", True)),
            "cargo": bool(apply_fields.get("cargo", True)),
            "orgao": bool(apply_fields.get("orgao", True)),
        }

        def _dedup(items: List[str]) -> List[str]:
            out: List[str] = []
            seen = set()
            for item in items:
                txt = str(item or "").strip()
                if not txt:
                    continue
                key = txt.lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(txt)
            return out

        merged_emails = current["emails"]
        if fields["emails"]:
            merged_emails = _dedup([*current["emails"], *(payload.get("emails") or [])])
        merged_telefones = current["telefones"]
        if fields["telefones"]:
            merged_telefones = _dedup([*current["telefones"], *(payload.get("telefones") or [])])

        merged_payload = {
            "emails": merged_emails,
            "telefones": merged_telefones,
            "endereco": payload.get("endereco", "") if fields["endereco"] else current["endereco"],
            "etiqueta_mala_direta": _normalize_etiqueta_mala_direta(
                payload.get("etiqueta_mala_direta", "") if fields["etiqueta_mala_direta"] else current["etiqueta_mala_direta"]
            ),
            "cargo": payload.get("cargo", "") if fields["cargo"] else current["cargo"],
            "orgao": payload.get("orgao", "") if fields["orgao"] else current["orgao"],
        }
        if fields["endereco"] and not str(merged_payload["endereco"] or "").strip():
            merged_payload["endereco"] = _infer_endereco_from_etiqueta(merged_payload.get("etiqueta_mala_direta", ""))

        profile_mode = str(request.data.get("profile_mode") or "existing").strip().lower()
        profile_id = request.data.get("profile_id")
        profile_conta_id = request.data.get("profile_conta_id")
        suggestion_source = str(request.data.get("suggestion_source") or "").strip() or "unknown"
        decision_data = request.data.get("decision_data") if isinstance(request.data.get("decision_data"), dict) else {}
        enforce_decision_gate = bool(request.data.get("enforce_decision_gate", False))
        if enforce_decision_gate and decision_data and not bool(decision_data.get("aprovado_para_salvar", False)):
            return Response(
                {
                    "detail": "Aplicação bloqueada pelo gate de confiança.",
                    "decision_data": decision_data,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        profile_result: Dict[str, Any] = {"updated": False, "created": False}
        idem_key = str(request.headers.get("Idempotency-Key") or request.data.get("request_id") or "").strip()

        if idem_key:
            cache_key = f"enrichment:apply:idem:{request.user.id}:{pk}:{idem_key}"
            cached = cache.get(cache_key)
            if isinstance(cached, dict):
                cached["idempotent_replay"] = True
                return Response(cached, status=status.HTTP_200_OK)

        try:
            with transaction.atomic():
                siga_mcp_server.configure_runtime(
                    mode="django",
                    dry_run=False,
                    django_project_root="/var/www/gabinete/siga-gabinete",
                )
                save_result = siga_mcp_server.update_contact_profile(contact_id=pk, enriched_data=merged_payload)

                # Cargo/órgão devem refletir no PerfilMunicipe vinculado à conta do usuário.
                if fields["cargo"] or fields["orgao"]:
                    target_profile = None
                    user_conta_ids = set()
                    if hasattr(request.user, "perfil"):
                        user_conta_ids = set(request.user.perfil.contas.values_list("id", flat=True))

                    if profile_mode == "new":
                        conta_id = int(profile_conta_id) if profile_conta_id else None
                        if not conta_id:
                            raise ValueError("Para criar perfil novo, informe profile_conta_id.")
                        if user_conta_ids and conta_id not in user_conta_ids and not request.user.is_superuser:
                            raise ValueError("Conta inválida para o usuário logado.")
                        categoria = (
                            CategoriaContato.objects.filter(nome__iexact="MUNÍCIPE").first()
                            or CategoriaContato.objects.filter(ativa=True).order_by("id").first()
                        )
                        if not categoria:
                            raise ValueError("Nenhuma categoria de contato ativa para criar o perfil.")
                        target_profile = PerfilMunicipe.objects.create(
                            municipe=mun,
                            conta_id=conta_id,
                            categoria=categoria,
                            cargo=(merged_payload["cargo"] or "").strip(),
                            instituicao=(merged_payload["orgao"] or "").strip(),
                            ativo=True,
                        )
                        profile_result = {"updated": False, "created": True, "profile_id": target_profile.id}
                    else:
                        if profile_id:
                            target_profile = (
                                mun.perfis.select_related("conta")
                                .filter(id=profile_id, ativo=True)
                                .first()
                            )
                        if not target_profile:
                            # fallback: primeiro perfil da(s) conta(s) do usuário
                            q = mun.perfis.select_related("conta").filter(ativo=True)
                            if user_conta_ids and not request.user.is_superuser:
                                q = q.filter(conta_id__in=user_conta_ids)
                            target_profile = q.order_by("-id").first()
                        if target_profile:
                            if fields["cargo"]:
                                target_profile.cargo = (merged_payload["cargo"] or "").strip()
                            if fields["orgao"]:
                                target_profile.instituicao = (merged_payload["orgao"] or "").strip()
                            target_profile.save(update_fields=["cargo", "instituicao"])
                            profile_result = {"updated": True, "created": False, "profile_id": target_profile.id}
                        else:
                            profile_result = {
                                "updated": False,
                                "created": False,
                                "warning": "Nenhum perfil elegível para edição. Use profile_mode='new'.",
                            }

                # Auditoria de origem da sugestão aplicada (Human-in-the-Loop).
                audit_base = mun.auditoria_ia if isinstance(mun.auditoria_ia, dict) else {}
                events = audit_base.get("enrichment_events")
                if not isinstance(events, list):
                    events = []
                events.append(
                    {
                        "ts": timezone.now().isoformat(),
                        "source": suggestion_source,
                        "user_id": request.user.id,
                        "applied_fields": fields,
                        "profile_mode": profile_mode,
                        "profile_result": profile_result,
                        "decision_aprovado": bool(decision_data.get("aprovado_para_salvar", False)) if decision_data else None,
                    }
                )
                # Limite simples para não crescer indefinidamente.
                audit_base["enrichment_events"] = events[-30:]
                audit_base["last_enrichment_source"] = suggestion_source
                audit_base["last_enrichment_at"] = timezone.now().isoformat()
                mun.auditoria_ia = audit_base
                mun.auditoria_ia_data = timezone.now()
                mun.save(update_fields=["auditoria_ia", "auditoria_ia_data", "data_atualizacao"])
        except Exception as exc:
            _record_metric("apply_error_total")
            _maybe_alert_error_spike()
            _log_event(
                "enrich_apply_error",
                user_id=getattr(request.user, "id", None),
                contact_id=pk,
                error=str(exc),
                duration_ms=int((time.monotonic() - started_at) * 1000),
            )
            return Response(
                {"detail": f"Falha ao aplicar enriquecimento: {exc}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        response_data = {
            "ok": True,
            "contact_id": pk,
            "applied_fields": fields,
            "suggestion_source": suggestion_source,
            "profile_result": profile_result,
            "available_profiles": current_profiles,
            "save_result": save_result,
        }
        if idem_key:
            cache.set(f"enrichment:apply:idem:{request.user.id}:{pk}:{idem_key}", response_data, timeout=6 * 3600)

        _record_metric("apply_success_total")
        _log_event(
            "enrich_apply_success",
            user_id=getattr(request.user, "id", None),
            contact_id=pk,
            source=suggestion_source,
            duration_ms=int((time.monotonic() - started_at) * 1000),
        )

        return Response(response_data, status=status.HTTP_200_OK)
