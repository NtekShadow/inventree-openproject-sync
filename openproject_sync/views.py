"""API views used by the OpenProject panel and dashboard item."""

from __future__ import annotations

from django.apps import apps
from django.core.cache import cache
from django.db import models
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from . import refs
from .client import OpenProjectError
from .orders import ADAPTERS, get_adapter
from .sync import META_KEY, SyncError

ACTIONS = {"sync", "link", "create", "unlink", "move"}
MAX_RESOLVED_REFS = 20


def all_models() -> list[type[models.Model]]:
    """All registered models.

    Unlike ``apps.get_models()`` this also works while InvenTree reloads its
    plugin registry, when Django's app registry is temporarily not ready.
    """
    return [
        m
        for app_models in list(apps.all_models.values())
        for m in list(app_models.values())
    ]


def load_instance(model_type: str, pk) -> models.Model | None:
    """Load an instance by InvenTree model type ('part', 'build', ...)."""
    for model in all_models():
        if model.__name__.lower() == model_type and hasattr(model, "get_absolute_url"):
            return model.objects.filter(pk=pk).first()
    return None


def instance_texts(instance: models.Model) -> list[str]:
    """Values of all text fields of an instance."""
    return [
        str(getattr(instance, f.attname) or "")
        for f in instance._meta.concrete_fields
        if isinstance(f, (models.CharField, models.TextField))
    ]


def wp_summary(syncer, wp: dict) -> dict:
    """Fields of a work package the frontend displays."""
    links = wp.get("_links", {})
    display = wp.get("displayId") or str(wp["id"])
    return {
        "id": wp["id"],
        "display": display,
        "url": syncer.wp_url(display),
        "subject": wp.get("subject"),
        "status": links.get("status", {}).get("title"),
        "project": links.get("project", {}).get("title"),
        "type": links.get("type", {}).get("title"),
        "startDate": wp.get("startDate"),
        "dueDate": wp.get("dueDate"),
        "percentageDone": wp.get("percentageDone"),
    }


def resolve_refs(syncer, found: list[refs.WorkPackageRef]) -> list[dict]:
    """Look up subject and status of referenced work packages (cached 5 min)."""
    result = []
    for i, ref in enumerate(found):
        entry = {"key": ref.key, "text": ref.text, "url": syncer.wp_url(ref.key)}
        if i < MAX_RESOLVED_REFS and syncer.is_configured():
            cache_key = f"openproject-sync:wp:{syncer.base_url}:{ref.key}"
            summary = cache.get(cache_key)
            if summary is None:
                try:
                    wp = syncer.client.get_work_package(ref.key)
                    summary = wp_summary(syncer, wp) if wp else {}
                except OpenProjectError:
                    summary = {}
                cache.set(cache_key, summary, 300)
            entry["wp"] = summary or None
        result.append(entry)
    return result


class PluginView(APIView):
    """Base view with access to the plugin instance."""

    plugin = None
    permission_classes = [permissions.IsAuthenticated]

    @staticmethod
    def can_change(request, model_type: str) -> bool:
        """User may change orders of this type."""
        model = ADAPTERS[model_type].model()
        return request.user.has_perm(
            f"{model._meta.app_label}.change_{model._meta.model_name}"
        )


class OrderView(PluginView):
    """Sync state of one order."""

    def get(self, request, model_type: str, pk: int):
        """Linked work package, references and suggestions."""
        adapter = get_adapter(model_type, pk)
        if adapter is None:
            return Response({"detail": "Auftrag nicht gefunden"}, status=404)

        syncer = self.plugin.syncer()
        md = syncer.meta(adapter)
        data = {
            "configured": syncer.is_configured(),
            "can_edit": self.can_change(request, model_type),
            "reference": adapter.reference,
            "project": syncer.project_setting,
            "linked": bool(md.get("wp")),
            "wp": None,
            "error": md.get("error"),
            "warnings": md.get("warnings") or [],
            "synced": md.get("synced"),
            "refs": [],
        }
        if not data["configured"]:
            return Response(data)

        try:
            data["refs"] = resolve_refs(syncer, syncer.text_refs(adapter))
            if md.get("wp"):
                wp = syncer.client.get_work_package(md["wp"])
                data["wp"] = wp_summary(syncer, wp) if wp else None
        except (OpenProjectError, SyncError) as exc:
            data["error"] = str(exc)
        return Response(data)


class OrderActionView(PluginView):
    """Run sync / link / create / unlink for one order."""

    def post(self, request, model_type: str, pk: int, action: str):
        """Run the action, returns {ok, message}."""
        if model_type not in ADAPTERS or action not in ACTIONS:
            return Response({"detail": "Unbekannte Aktion"}, status=404)
        if not self.can_change(request, model_type):
            return Response({"detail": "Keine Berechtigung"}, status=403)

        kwargs = {}
        if action == "link":
            key = str(request.data.get("ref") or "").strip()
            prefix = self.plugin.get_setting("REFERENCE_PREFIX") or refs.DEFAULT_PREFIX
            if key.upper().startswith(prefix.upper()):
                key = key[len(prefix) :]
            url_ref = refs.find_url_ref(key, self.plugin.syncer().base_url)
            kwargs["key"] = url_ref.key if url_ref else key
            if not kwargs["key"]:
                return Response(
                    {"ok": False, "message": "Arbeitspaket angeben"}, status=400
                )
        elif action in ("create", "move"):
            kwargs["project"] = (request.data.get("project") or "").strip() or None
            if action == "move" and not kwargs["project"]:
                return Response({"ok": False, "message": "Projekt angeben"}, status=400)

        ok, message = self.plugin.run_action(model_type, pk, action, **kwargs)
        return Response({"ok": ok, "message": message}, status=200 if ok else 400)


class ProjectsView(PluginView):
    """Projects for the project selection in the panel (cached 5 min)."""

    def get(self, request):
        """Active projects as {value: identifier, label: 'Parent / Name'}."""
        syncer = self.plugin.syncer()
        cache_key = f"openproject-sync:projects:{syncer.base_url}"
        projects = cache.get(cache_key)
        if projects is None:
            try:
                elements = syncer.client.projects()
            except (OpenProjectError, SyncError) as exc:
                return Response({"detail": str(exc)}, status=502)
            projects = []
            for p in elements:
                parent = (p.get("_links", {}).get("parent") or {}).get("title")
                label = f"{parent} / {p['name']}" if parent else p["name"]
                projects.append(
                    {"value": p["identifier"], "label": f"{label} ({p['identifier']})"}
                )
            projects.sort(key=lambda x: x["label"].lower())
            cache.set(cache_key, projects, 300)
        return Response(projects)


class RefsView(PluginView):
    """OP# references found in any model instance."""

    def get(self, request, model_type: str, pk: int):
        """References with subject and status."""
        instance = load_instance(model_type, pk)
        if instance is None:
            return Response({"detail": "Nicht gefunden"}, status=404)
        syncer = self.plugin.syncer()
        found: dict[str, refs.WorkPackageRef] = {}
        for text in instance_texts(instance):
            for ref in refs.find_refs(text, syncer.prefix):
                found.setdefault(ref.key, ref)
        return Response(
            {
                "configured": syncer.is_configured(),
                "refs": resolve_refs(syncer, list(found.values())),
            }
        )


class OverviewView(PluginView):
    """Counts and errors for the dashboard item; POST syncs all open orders."""

    def get(self, request):
        """Linked/total open orders per type and orders with errors."""
        syncer = self.plugin.syncer()
        data = {
            "configured": syncer.is_configured(),
            "connection": None,
            "types": [],
            "errors": [],
        }
        if data["configured"]:
            try:
                data["connection"] = syncer.client.whoami().get("name")
            except OpenProjectError as exc:
                data["errors"].append({"reference": "Verbindung", "error": str(exc)})

        for adapter_cls in ADAPTERS.values():
            if not self.plugin.get_setting(adapter_cls.sync_setting):
                continue
            open_orders = adapter_cls.model().objects.filter(
                status__in=adapter_cls.active_codes()
            )
            linked = open_orders.filter(metadata__openproject__wp__isnull=False)
            data["types"].append(
                {
                    "model_type": adapter_cls.model_type,
                    "label": adapter_cls.label,
                    "open": open_orders.count(),
                    "linked": linked.count(),
                }
            )
            for order in linked.filter(metadata__openproject__error__isnull=False)[:20]:
                md = order.get_metadata(META_KEY) or {}
                if md.get("error"):
                    data["errors"].append(
                        {
                            "reference": order.reference,
                            "model_type": adapter_cls.model_type,
                            "id": order.pk,
                            "error": md["error"],
                        }
                    )
        return Response(data)

    def post(self, request):
        """Sync all open orders (also creates missing work packages per AUTO_CREATE)."""
        if not request.user.is_staff:
            return Response({"detail": "Nur für Administratoren"}, status=403)
        done = failed = 0
        for adapter_cls in ADAPTERS.values():
            if not self.plugin.get_setting(adapter_cls.sync_setting):
                continue
            pks = (
                adapter_cls.model()
                .objects.filter(status__in=adapter_cls.active_codes())
                .values_list("pk", flat=True)
            )
            for pk in pks:
                ok, _ = self.plugin.run_action(adapter_cls.model_type, pk)
                done += ok
                failed += not ok
        return Response(
            {"ok": True, "message": f"{done} synchronisiert, {failed} Fehler"}
        )
