"""Adapters exposing build orders and purchase orders through one interface."""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.db import models

logger = logging.getLogger("inventree")


def _fmt_qty(value) -> str:
    try:
        value = float(value or 0)
    except (TypeError, ValueError):
        return str(value)
    return str(int(value)) if value.is_integer() else f"{value:g}"


def _iso(value) -> str | None:
    if not value:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def parameter_value(obj, name: str) -> str:
    """Value of the parameter ``name`` (case insensitive) on an InvenTree object."""
    params = getattr(obj, "parameters", None) if obj is not None and name else None
    if not callable(params):
        return ""
    for param in params():
        if param.template.name.strip().lower() == name.strip().lower() and param.data:
            return str(param.data).strip()
    return ""


def cost_hours(quantity, units: str) -> float | None:
    """Convert a manufacturing cost quantity in ``units`` to hours (None if not a time)."""
    if not units:
        return None
    try:
        from InvenTree.conversion import get_unit_registry

        ureg = get_unit_registry()
        return float((float(quantity) * ureg.Quantity(1, units)).to("hour").magnitude)
    except Exception:  # noqa: BLE001 - pint raises many unrelated error types
        return None


def name_list(text: str | None) -> set[str]:
    """'A; B, C' -> {'a', 'b', 'c'}."""
    return {
        n.strip().lower()
        for n in (text or "").replace(";", ",").split(",")
        if n.strip()
    }


def manufacturing_costs(part) -> list:
    """Active entries of the 'manufacturing costs' plugin for a part (if installed)."""
    costs = getattr(part, "manufacturing_costs", None) if part is not None else None
    if costs is None:
        return []
    return list(costs.filter(active=True).select_related("rate"))


class OrderAdapter:
    """Common interface for the order types that get synced."""

    model_type: str  # InvenTree model type, as used by the UI ('build', ...)
    label: str
    type_setting: str
    sync_setting: str

    def __init__(self, instance: models.Model):
        self.instance = instance

    # Plugin setting lookup, set by Syncer.bind()
    settings_lookup = None

    def setting(self, key: str):
        """Plugin setting value (None while not bound to a syncer)."""
        return self.settings_lookup(key) if self.settings_lookup else None

    def type_setting_key(self) -> str:
        """Setting holding the work package type name for this order."""
        return self.type_setting

    def work_hours(self) -> float | None:
        """Planned work in hours."""
        return None

    def set_machine(self, value: str) -> str | None:
        """Store a machine chosen in OpenProject. Returns a warning on failure."""
        return "Maschine kann für diesen Auftragstyp nicht gespeichert werden."

    @property
    def reference(self) -> str:
        """Order reference, e.g. 'BO-0012'."""
        return self.instance.reference

    def web_url(self) -> str:
        """Absolute URL of the order in the InvenTree web UI."""
        from InvenTree.helpers_model import construct_absolute_url

        return construct_absolute_url(self.instance.get_absolute_url())

    def status_name(self) -> str:
        """Logical status name, e.g. 'PRODUCTION'."""
        try:
            return self.status_class()(self.instance.status).name
        except ValueError:
            return str(self.instance.status)

    def status_text(self) -> str:
        """Human readable status (custom states included)."""
        try:
            return str(self.instance.get_status_display())
        except (AttributeError, ValueError):
            return self.status_name()

    def is_active(self) -> bool:
        """True if the order is still open."""
        return self.instance.status in self.active_codes()

    def dates(self) -> dict[str, str | None]:
        """Dates as OpenProject field names."""
        return {
            "startDate": _iso(getattr(self.instance, "start_date", None)),
            "dueDate": _iso(self.instance.target_date),
        }

    def set_dates(self, values: dict[str, str | None]) -> list[str]:
        """Apply OpenProject dates, returns the changed model fields."""
        mapping = {"startDate": "start_date", "dueDate": "target_date"}
        changed = []
        for op_field, value in values.items():
            field = mapping[op_field]
            if hasattr(self.instance, field):
                setattr(
                    self.instance, field, date.fromisoformat(value) if value else None
                )
                changed.append(field)
        return changed

    def texts(self) -> list[str]:
        """Free text fields that are scanned for work package references."""
        inst = self.instance
        return [
            str(getattr(inst, f, "") or "")
            for f in ("title", "description", "notes", "link")
        ]

    def subject(self) -> str:
        """Work package subject."""
        raise NotImplementedError

    def info_rows(self) -> list[tuple[str, str]]:
        """Rows of the InvenTree table in the work package description."""
        raise NotImplementedError

    def progress(self) -> int | None:
        """Progress in percent."""
        raise NotImplementedError

    def parent_order(self) -> models.Model | None:
        """Order whose work package is the parent of this order's work package."""
        return None

    def child_orders(self) -> list[models.Model]:
        """Orders whose work package is a child of this order's work package."""
        return []

    def preceding_orders(self) -> list[models.Model]:
        """Orders whose work package 'precedes' this order's work package."""
        return []

    def following_orders(self) -> list[models.Model]:
        """Orders whose work package this order's work package 'precedes'."""
        return []

    def details_markdown(self) -> str:
        """Extra markdown below the info table."""
        return ""

    def field_values(self) -> dict[str, object]:
        """Values available for the custom field mapping."""
        return {
            "reference": self.reference,
            "url": self.web_url(),
            "status": self.status_text(),
            "progress": self.progress(),
            "order_type": self.label,
        }

    @staticmethod
    def status_class():
        """InvenTree status code class of this order type."""
        raise NotImplementedError

    @staticmethod
    def active_codes() -> list[int]:
        """Status codes of open orders."""
        raise NotImplementedError

    @staticmethod
    def model():
        """Django model class."""
        raise NotImplementedError


class BuildAdapter(OrderAdapter):
    """Build order."""

    model_type = "build"
    label = "Fertigungsauftrag"
    type_setting = "BUILD_TYPE"
    sync_setting = "SYNC_BUILD_ORDERS"

    @staticmethod
    def model():
        """Django model class."""
        from build.models import Build

        return Build

    @staticmethod
    def status_class():
        """InvenTree status code class of this order type."""
        from build.status_codes import BuildStatus

        return BuildStatus

    @staticmethod
    def active_codes():
        """Status codes of open orders."""
        from build.status_codes import BuildStatusGroups

        return BuildStatusGroups.ACTIVE_CODES

    def _part(self) -> str:
        part = self.instance.part
        return part.full_name if part else ""

    @property
    def is_external(self) -> bool:
        """Build is done by a supplier (InvenTree >= 1.0 'external' flag)."""
        return bool(getattr(self.instance, "external", False))

    def type_setting_key(self):
        """External builds can use their own work package type."""
        if self.is_external and self.setting("EXTERNAL_BUILD_TYPE"):
            return "EXTERNAL_BUILD_TYPE"
        return self.type_setting

    def _param(self, setting_key: str) -> str:
        """Parameter value, from the build order first, then from the part."""
        name = self.setting(setting_key) or ""
        return parameter_value(self.instance, name) or parameter_value(
            self.instance.part, name
        )

    def process(self) -> str:
        """Manufacturing process, e.g. 'Drehen'."""
        return self._param("PROCESS_PARAMETER")

    def steps(self) -> list[dict]:
        """Manufacturing cost entries of the part as work steps.

        Hours per part = quantity (in the rate's time unit) / amortization, so a
        set-up time entered once and amortized over the batch size is spread out.
        """
        machine_rates = name_list(self.setting("MACHINE_RATES"))
        no_work_rates = name_list(self.setting("NO_WORK_RATES"))
        qty = float(self.instance.quantity or 0)
        steps = []
        for cost in manufacturing_costs(self.instance.part):
            rate = cost.rate.name if cost.rate else ""
            hours = cost_hours(cost.quantity, cost.rate.units if cost.rate else "")
            per_part = hours / float(cost.amortization or 1) if hours else None
            steps.append(
                {
                    "name": cost.description or rate or "-",
                    "rate": rate,
                    "per_part": per_part,
                    "total": per_part * qty if per_part else None,
                    "is_machine": rate.lower() in machine_rates,
                    "is_work": bool(per_part) and rate.lower() not in no_work_rates,
                }
            )
        return steps

    def machine(self) -> str:
        """Machine of this build order (one value).

        The 'Maschine' parameter of the build order; without it the suggestion
        from the routing: the machine rate with the most time in the manufacturing costs.
        """
        machine = parameter_value(
            self.instance, self.setting("MACHINE_PARAMETER") or ""
        )
        if machine:
            return machine
        totals: dict[str, float] = {}
        for step in self.steps():
            if step["is_machine"]:
                totals[step["rate"]] = totals.get(step["rate"], 0) + (
                    step["per_part"] or 0
                )
        return max(totals, key=totals.get) if totals else ""

    def set_machine(self, value: str) -> str | None:
        """Write the machine into the build order parameter."""
        from common.models import ParameterTemplate

        name = self.setting("MACHINE_PARAMETER") or ""
        template = (
            ParameterTemplate.objects.filter(name__iexact=name).first()
            if name
            else None
        )
        if template is None:
            return f'Parametervorlage "{name}" fehlt in InvenTree, Maschine aus OpenProject nicht übernommen.'
        params = self.instance.parameters_list
        if value:
            params.update_or_create(template=template, defaults={"data": value})
        else:
            params.filter(template=template).delete()
        return None

    def work_hours(self):
        """Planned personnel hours: time based steps except 'no work' rates."""
        total = sum(s["total"] for s in self.steps() if s["is_work"])
        return round(total, 2) if total > 0 else None

    def details_markdown(self):
        """Table of the work steps."""
        steps = self.steps()
        if not steps:
            return ""

        def fmt(hours):
            return f"{hours:.2f} h".replace(".", ",") if hours else "-"

        lines = ["| Schritt | Satz | pro Stück | gesamt | |", "|---|---|---|---|---|"]
        for s in steps:
            note = "ohne Personal" if s["per_part"] and not s["is_work"] else ""
            name = s["name"].replace("|", "/")
            lines.append(
                f"| {name} | {s['rate']} | {fmt(s['per_part'])} | {fmt(s['total'])} | {note} |"
            )
        return "\n".join(lines)

    def subject(self):
        """Work package subject."""
        title = self.instance.title or self._part()
        return f"{self.reference}: {title} ({_fmt_qty(self.instance.quantity)} Stk)"

    def progress(self):
        """Completed outputs in percent."""
        qty = float(self.instance.quantity or 0)
        if qty <= 0:
            return None
        return min(100, round(float(self.instance.completed or 0) / qty * 100))

    def info_rows(self):
        """Rows of the InvenTree table in the work package description."""
        b = self.instance
        rows = [
            ("Fertigungsauftrag", f"[{b.reference}]({self.web_url()})"),
            ("Teil", self._part() or "-"),
            ("Menge", f"{_fmt_qty(b.completed)} / {_fmt_qty(b.quantity)} fertig"),
            ("Status", self.status_text()),
        ]
        rows.append(("Fertigung", self.manufacturing()))
        for label, value in (
            ("Verfahren", self.process()),
            ("Maschine", self.machine()),
        ):
            if value:
                rows.append((label, value))
        hours = self.work_hours()
        if hours:
            rows.append(("Geplanter Aufwand", f"{hours:g} h"))
        if b.parent:
            rows.append(("Nächster Schritt", b.parent.reference))
        return rows

    # Structure: a build of a part *with* a manufacturing process is a process
    # stage (turned, milled, anodized); a build of a part without one is an
    # assembly (Akku). Sub-builds of a stage are its previous stages
    # ('precedes'); sub-builds of an assembly are its children. All stages of a
    # part thereby end up as siblings below the nearest assembly.

    def _adapter(self, build) -> BuildAdapter:
        adapter = BuildAdapter(build)
        adapter.settings_lookup = self.settings_lookup
        return adapter

    def is_stage(self) -> bool:
        """The build makes a process stage of a part (its part has a process)."""
        return bool(self.process())

    def parent_order(self):
        """Nearest assembly build above this build (skipping process stages)."""
        parent = self.instance.parent
        while parent is not None and self._adapter(parent).is_stage():
            parent = parent.parent
        return parent

    def child_orders(self):
        """Builds below this assembly, including all stages of its direct parts."""
        if self.is_stage():
            return []
        result = []
        pending = list(self.instance.children.all())
        while pending:
            child = pending.pop(0)
            result.append(child)
            if self._adapter(child).is_stage():
                pending.extend(child.children.all())
        return result

    def preceding_orders(self):
        """Previous stages (if this is a stage) and purchase orders for this build."""
        from order.models import PurchaseOrder

        stages = list(self.instance.children.all()) if self.is_stage() else []
        pos = PurchaseOrder.objects.filter(lines__build_order=self.instance).distinct()
        return [*stages, *pos]

    def following_orders(self):
        """The next stage, if the parent build is a stage."""
        parent = self.instance.parent
        return (
            [parent] if parent is not None and self._adapter(parent).is_stage() else []
        )

    def field_values(self):
        """Values available for the custom field mapping."""
        part = self.instance.part
        return {
            **super().field_values(),
            "part": self._part(),
            "ipn": (part.IPN if part else "") or "",
            "quantity": float(self.instance.quantity or 0),
            "completed": float(self.instance.completed or 0),
            "process": self.process(),
            "machine": self.machine(),
            "manufacturing": self.manufacturing(),
            "work": self.work_hours(),
        }

    def manufacturing(self) -> str:
        """'Eigenfertigung' or 'Fremdfertigung'."""
        return "Fremdfertigung" if self.is_external else "Eigenfertigung"


class PurchaseOrderAdapter(OrderAdapter):
    """Purchase order."""

    model_type = "purchaseorder"
    label = "Bestellung"
    type_setting = "PURCHASE_TYPE"
    sync_setting = "SYNC_PURCHASE_ORDERS"

    @staticmethod
    def model():
        """Django model class."""
        from order.models import PurchaseOrder

        return PurchaseOrder

    @staticmethod
    def status_class():
        """InvenTree status code class of this order type."""
        from order.status_codes import PurchaseOrderStatus

        return PurchaseOrderStatus

    @staticmethod
    def active_codes():
        """Status codes of open orders."""
        from order.status_codes import PurchaseOrderStatusGroups

        return PurchaseOrderStatusGroups.OPEN

    def _supplier(self) -> str:
        supplier = self.instance.supplier
        return supplier.name if supplier else ""

    def _quantities(self) -> tuple[float, float]:
        ordered = received = 0.0
        for line in self.instance.lines.all():
            ordered += float(line.quantity or 0)
            received += float(line.received or 0)
        return ordered, received

    def subject(self):
        """Work package subject."""
        po = self.instance
        text = po.description or self._supplier() or "Bestellung"
        return f"{self.reference}: {text}"

    def progress(self):
        """Received quantity in percent."""
        ordered, received = self._quantities()
        if ordered <= 0:
            return None
        return min(100, round(received / ordered * 100))

    def info_rows(self):
        """Rows of the InvenTree table in the work package description."""
        po = self.instance
        ordered, received = self._quantities()
        rows = [
            ("Bestellung", f"[{po.reference}]({self.web_url()})"),
            ("Lieferant", self._supplier() or "-"),
        ]
        if po.supplier_reference:
            rows.append(("Lieferanten-Referenz", po.supplier_reference))
        rows += [
            ("Positionen", str(po.lines.count())),
            ("Geliefert", f"{_fmt_qty(received)} / {_fmt_qty(ordered)}"),
            ("Status", self.status_text()),
        ]
        builds = self._builds()
        if builds:
            rows.append(("Für Fertigung", ", ".join(b.reference for b in builds)))
        return rows

    def _builds(self) -> list[models.Model]:
        """Build orders served by this order (external manufacturing)."""
        builds = {}
        for line in self.instance.lines.all():
            build = getattr(line, "build_order", None)
            if build:
                builds[build.pk] = build
        return list(builds.values())

    def following_orders(self):
        """Build orders the purchased items are for."""
        return self._builds()

    def parent_order(self):
        """The assembly of the (first) build order this order is for."""
        for build in self._builds():
            adapter = BuildAdapter(build)
            adapter.settings_lookup = self.settings_lookup
            parent = adapter.parent_order()
            if parent is not None:
                return parent
        return None

    def texts(self):
        """Free text fields, including line item notes."""
        texts = super().texts()
        texts += [line.notes or "" for line in self.instance.lines.all()]
        return texts

    def field_values(self):
        """Values available for the custom field mapping."""
        return {
            **super().field_values(),
            "supplier": self._supplier(),
            "supplier_reference": self.instance.supplier_reference or "",
        }


ADAPTERS: dict[str, type[OrderAdapter]] = {
    BuildAdapter.model_type: BuildAdapter,
    PurchaseOrderAdapter.model_type: PurchaseOrderAdapter,
}


def adapter_for(instance: models.Model) -> OrderAdapter | None:
    """Return the adapter for a model instance, or None if it is not synced."""
    for adapter in ADAPTERS.values():
        if isinstance(instance, adapter.model()):
            return adapter(instance)
    return None


def get_adapter(model_type: str, pk: int) -> OrderAdapter | None:
    """Load an order by model type and primary key."""
    adapter = ADAPTERS.get(model_type)
    if not adapter:
        return None
    instance = adapter.model().objects.filter(pk=pk).first()
    return adapter(instance) if instance else None
