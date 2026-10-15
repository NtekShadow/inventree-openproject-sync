"""Sync InvenTree build and purchase orders with OpenProject work packages."""

from __future__ import annotations

import functools
import logging

from django.apps import apps
from django.db import transaction

from plugin import InvenTreePlugin
from plugin.mixins import (
    EventMixin,
    ScheduleMixin,
    SettingsMixin,
    UrlsMixin,
    UserInterfaceMixin,
)

from . import PLUGIN_VERSION, refs
from .client import OpenProjectError
from .orders import ADAPTERS
from .sync import Syncer, SyncError

logger = logging.getLogger("inventree")

# Models whose notes are not linkified: saved too often to justify a task per save
LINKIFY_EXCLUDED_TABLES = {"stock_stockitem"}


class OpenProjectSync(
    EventMixin,
    ScheduleMixin,
    SettingsMixin,
    UrlsMixin,
    UserInterfaceMixin,
    InvenTreePlugin,
):
    """Sync build and purchase orders with OpenProject, link OP# references."""

    TITLE = "OpenProject Sync"
    NAME = "OpenProjectSync"
    SLUG = "openproject-sync"
    DESCRIPTION = (
        "Sync InvenTree build and purchase orders with OpenProject work packages"
    )
    VERSION = PLUGIN_VERSION

    AUTHOR = "Jan Malte Sörensen"
    LICENSE = "MIT"

    MIN_VERSION = "1.0.0"

    SCHEDULED_TASKS = {
        "poll_openproject": {
            "func": "poll_openproject",
            "schedule": "I",
            "minutes": 5,
        }
    }

    SETTINGS = {
        "OPENPROJECT_URL": {
            "name": "OpenProject URL",
            "description": "Basis-URL der OpenProject-Instanz, z. B. https://openproject.example.com",
            "default": "",
        },
        "OPENPROJECT_API_KEY": {
            "name": "OpenProject API-Schlüssel",
            "description": "API-Token eines Benutzers mit Schreibrechten in den betroffenen Projekten (Mein Konto > Zugangstokens)",
            "protected": True,
            "default": "",
        },
        "SYNC_BUILD_ORDERS": {
            "name": "Fertigungsaufträge synchronisieren",
            "validator": bool,
            "default": True,
        },
        "SYNC_PURCHASE_ORDERS": {
            "name": "Bestellungen synchronisieren",
            "validator": bool,
            "default": True,
        },
        "PROJECT": {
            "name": "Projekt",
            "description": "Projekt für neue Arbeitspakete: Kennung (z. B. FER_27) oder Link zum Projekt",
            "default": "",
        },
        "AUTO_CREATE": {
            "name": "Arbeitspakete automatisch anlegen",
            "description": "Für jeden offenen Auftrag ein Arbeitspaket anlegen. Aus: nur über das Panel.",
            "validator": bool,
            "default": True,
        },
        "BUILD_TYPE": {
            "name": "Typ für Fertigungsaufträge",
            "description": "Name des Arbeitspaket-Typs (fehlt er im Projekt, wird der erste Typ genommen)",
            "default": "Aufgabe",
        },
        "PURCHASE_TYPE": {
            "name": "Typ für Bestellungen",
            "description": "Name des Arbeitspaket-Typs (fehlt er im Projekt, wird der erste Typ genommen)",
            "default": "Bestellung",
        },
        "EXTERNAL_BUILD_TYPE": {
            "name": "Typ für externe Fertigungsaufträge",
            "description": "Eigener Typ für Fremdfertigung (leer = wie Fertigungsaufträge)",
            "default": "",
        },
        "PROCESS_PARAMETER": {
            "name": "Parameter Fertigungsverfahren",
            "description": "Name des Teile-Parameters mit dem Fertigungsverfahren (am Fertigungsauftrag oder Teil)",
            "default": "Fertigungsverfahren",
        },
        "MACHINE_PARAMETER": {
            "name": "Parameter Maschine",
            "description": "Parameter am Fertigungsauftrag mit der Maschine. Wird in OpenProject geändert, schreibt das Plugin ihn zurück (Parametervorlage muss existieren).",
            "default": "Maschine",
        },
        "MACHINE_RATES": {
            "name": "Maschinen-Sätze",
            "description": "Manufacturing-Costs-Sätze, die eine Maschine sind (getrennt mit ';'). Ohne Parameter am Auftrag wird der Satz mit dem größten Zeitanteil vorgeschlagen.",
            "default": "",
        },
        "NO_WORK_RATES": {
            "name": "Sätze ohne Personalaufwand",
            "description": "Sätze, die nicht in 'Arbeit' zählen, z. B. Standzeit (Maschine läuft allein); getrennt mit ';'",
            "default": "",
        },
        "PUSH_WORK": {
            "name": "Aufwand übertragen",
            "description": "Zeitbasierte Einträge aus Manufacturing Costs × Menge als 'Arbeit' in OpenProject",
            "validator": bool,
            "default": True,
        },
        "STATUS_MAP": {
            "name": "Status-Zuordnung",
            "description": 'InvenTree-Status=OpenProject-Statusname, getrennt mit ";". Nicht aufgeführte Status werden nicht übertragen.',
            "default": "PENDING=Neu; PRODUCTION=In Bearbeitung; PLACED=In Bearbeitung; COMPLETE=Abgeschlossen; CANCELLED=Abgeschlossen",
        },
        "PUSH_PROGRESS": {
            "name": "Fortschritt übertragen",
            "description": "Fertig- bzw. Liefermenge als % erledigt (nur bei arbeitsbasierter Fortschrittsberechnung in OpenProject)",
            "validator": bool,
            "default": True,
        },
        "PULL_DATES": {
            "name": "Termine aus OpenProject übernehmen",
            "description": "Start- und Endtermin werden in beide Richtungen abgeglichen",
            "validator": bool,
            "default": True,
        },
        "CUSTOM_FIELDS": {
            "name": "Benutzerdefinierte Felder (optional)",
            "description": 'Feldname in OpenProject=Wert, getrennt mit ";". Werte: reference, url, status, progress, order_type, part, ipn, quantity, completed, process, machine, manufacturing, work, supplier, supplier_reference',
            "default": "",
        },
        "REFERENCE_PREFIX": {
            "name": "Präfix für Verweise",
            "description": "Text vor der Arbeitspaket-ID, z. B. OP# für OP#EL-5",
            "default": refs.DEFAULT_PREFIX,
        },
        "LINKIFY_NOTES": {
            "name": "Verweise in Notizen verlinken",
            "description": "Ersetzt beim Speichern OP#EL-5 in Notizen durch einen Markdown-Link",
            "validator": bool,
            "default": True,
        },
        "LINKIFY_UI": {
            "name": "Verweise in der Oberfläche verlinken",
            "description": "Macht OP#-Verweise in allen Texten klickbar (aktiv, sobald das Panel oder Dashboard-Widget geladen wurde)",
            "validator": bool,
            "default": True,
        },
    }

    # Helpers

    def syncer(self) -> Syncer:
        """New sync engine for the current settings."""
        return Syncer(self)

    def frontend_config(self) -> dict:
        """Settings the frontend needs."""
        return {
            "op_url": (self.get_setting("OPENPROJECT_URL") or "").rstrip("/"),
            "prefix": self.get_setting("REFERENCE_PREFIX") or refs.DEFAULT_PREFIX,
            "link_template": refs.DEFAULT_LINK_TEMPLATE,
            "linkify": bool(self.get_setting("LINKIFY_UI")),
        }

    def run_action(
        self, model_type: str, pk: int, action: str = "sync", **kwargs
    ) -> tuple[bool, str]:
        """Run a sync action for one order, serialised per order across workers."""
        adapter_cls = ADAPTERS[model_type]
        with transaction.atomic():
            instance = (
                adapter_cls.model().objects.select_for_update().filter(pk=pk).first()
            )
            if instance is None:
                return False, "Auftrag nicht gefunden"
            adapter = adapter_cls(instance)
            try:
                result = getattr(self.syncer(), action)(adapter, **kwargs)
            except (SyncError, OpenProjectError) as exc:
                return False, str(exc)
        return True, result or "ok"

    def has_refs(self, model_type: str, pk) -> bool:
        """True if an instance has OP# references in any text field."""
        from .views import instance_texts, load_instance

        instance = load_instance(model_type, pk)
        if instance is None:
            return False
        prefix = self.get_setting("REFERENCE_PREFIX") or refs.DEFAULT_PREFIX
        return any(refs.find_refs(text, prefix) for text in instance_texts(instance))

    @staticmethod
    @functools.cache
    def notes_tables() -> dict[str, type]:
        """Tables of models with a markdown notes field."""
        from InvenTree.models import InvenTreeNotesMixin

        return {
            m._meta.db_table: m
            for m in apps.get_models()
            if issubclass(m, InvenTreeNotesMixin)
            and m._meta.db_table not in LINKIFY_EXCLUDED_TABLES
        }

    # Scheduled task

    def poll_openproject(self):
        """Pull changes from OpenProject for all linked open orders."""
        stats = self.syncer().poll()
        if stats["changed"] or stats["errors"]:
            logger.info("OpenProject poll: %s", stats)

    # Events

    def wants_process_event(self, event: str) -> bool:
        """Order saves trigger a sync, notes saves the linkification."""
        table, _, action = event.rpartition(".")
        if action not in ("created", "saved"):
            return False
        if table == "build_build" and self.get_setting("SYNC_BUILD_ORDERS"):
            return True
        if table in (
            "order_purchaseorder",
            "order_purchaseorderlineitem",
        ) and self.get_setting("SYNC_PURCHASE_ORDERS"):
            return True
        return bool(self.get_setting("LINKIFY_NOTES")) and table in self.notes_tables()

    def process_event(self, event: str, *args, **kwargs) -> None:
        """Handle a model save."""
        table = event.rpartition(".")[0]
        pk = kwargs.get("id")
        if not pk:
            return

        if self.get_setting("LINKIFY_NOTES"):
            model = self.notes_tables().get(table)
            if model:
                self.linkify_notes(model, pk)

        if table == "build_build" and self.get_setting("SYNC_BUILD_ORDERS"):
            ok, msg = self.run_action("build", pk)
        elif table == "order_purchaseorder" and self.get_setting(
            "SYNC_PURCHASE_ORDERS"
        ):
            ok, msg = self.run_action("purchaseorder", pk)
        elif table == "order_purchaseorderlineitem" and self.get_setting(
            "SYNC_PURCHASE_ORDERS"
        ):
            from order.models import PurchaseOrderLineItem

            line = PurchaseOrderLineItem.objects.filter(pk=pk).first()
            if not line:
                return
            ok, msg = self.run_action(
                "purchaseorder", line.order_id, allow_create=False
            )
        else:
            return

        if not ok:
            logger.warning("OpenProject sync after %s #%s failed: %s", event, pk, msg)

    def linkify_notes(self, model, pk: int) -> None:
        """Replace bare OP# references in a notes field with markdown links."""
        base_url = (self.get_setting("OPENPROJECT_URL") or "").rstrip("/")
        if not base_url:
            return
        notes = model.objects.filter(pk=pk).values_list("notes", flat=True).first()
        if not notes:
            return
        linked = refs.linkify_markdown(
            notes, base_url, self.get_setting("REFERENCE_PREFIX") or refs.DEFAULT_PREFIX
        )
        if linked != notes:
            # Queryset update: no save signal, no new event
            model.objects.filter(pk=pk).update(notes=linked)

    # API endpoints

    def setup_urls(self):
        """API used by the frontend panel and dashboard item."""
        from django.urls import path

        from . import views

        return [
            path(
                "order/<str:model_type>/<int:pk>/",
                views.OrderView.as_view(plugin=self),
                name="order",
            ),
            path(
                "order/<str:model_type>/<int:pk>/<str:action>/",
                views.OrderActionView.as_view(plugin=self),
                name="order-action",
            ),
            path(
                "refs/<str:model_type>/<int:pk>/",
                views.RefsView.as_view(plugin=self),
                name="refs",
            ),
            path("projects/", views.ProjectsView.as_view(plugin=self), name="projects"),
            path("overview/", views.OverviewView.as_view(plugin=self), name="overview"),
        ]

    # User interface

    def get_ui_panels(self, request, context: dict, **kwargs):
        """OpenProject panel on order pages, and on any page that contains OP# references."""
        model_type = context.get("target_model")
        pk = context.get("target_id")
        if not model_type or not pk:
            return []

        adapter_cls = ADAPTERS.get(model_type)
        if adapter_cls:
            if not self.get_setting(adapter_cls.sync_setting):
                return []
        elif not self.has_refs(model_type, pk):
            return []

        return [
            {
                "key": "openproject-sync",
                "title": "OpenProject",
                "description": "Verknüpfte Arbeitspakete",
                "icon": "ti:link:outline",
                "source": self.plugin_static_file("Panel.js:renderPanel"),
                "context": {**self.frontend_config(), "is_order": bool(adapter_cls)},
            }
        ]

    def get_ui_dashboard_items(self, request, context: dict, **kwargs):
        """Sync overview widget."""
        return [
            {
                "key": "openproject-sync-overview",
                "title": "OpenProject Sync",
                "description": "Status der Synchronisation mit OpenProject",
                "icon": "ti:refresh:outline",
                "source": self.plugin_static_file("Dashboard.js:renderDashboardItem"),
                "context": self.frontend_config(),
            }
        ]
