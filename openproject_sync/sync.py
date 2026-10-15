"""Synchronisation of orders with OpenProject work packages.

Per order the plugin stores (in ``metadata['openproject']``) the linked work
package and a *base* snapshot: the values both sides agreed on after the last
sync. Comparing both sides against that snapshot tells which side changed, so
no loop prevention flags are needed:

- Fields owned by InvenTree (subject, description block, status, progress,
  custom fields) are pushed whenever the InvenTree value differs from the base.
  Manual edits in OpenProject survive until the InvenTree value changes again.
- Dates are synced both ways. On conflicting edits OpenProject wins (planning
  happens there).
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from . import refs
from .client import OpenProjectClient, OpenProjectError, href_id
from .orders import ADAPTERS, OrderAdapter, adapter_for

if TYPE_CHECKING:
    from .core import OpenProjectSync

logger = logging.getLogger("inventree")

META_KEY = "openproject"
MARKER = "###### InvenTree"
# Start of the description written by the previous single file plugin (replaced on first sync)
LEGACY_MARKERS = ("### 🏭 InvenTree Fertigungsauftrag", "### 📦 InvenTree Bestellung")
DATE_FIELDS = ("startDate", "dueDate")


class SyncError(Exception):
    """A sync failed for a reason the user can fix."""


# Pure helpers (unit tested)


def plan_two_way(
    inv: str | None, op: str | None, base: str | None, pull: bool, has_base: bool
):
    """Decide what to do with a field synced both ways (dates, machine).

    Returns ('push', value), ('pull', value) or (None, agreed value).
    """
    if inv == op:
        return None, inv
    if not pull:
        return "push", inv
    if not has_base:
        # First sync: InvenTree value wins if it has one
        return ("push", inv) if inv else ("pull", op)
    if op == base:
        return "push", inv
    return "pull", op


def split_description(raw: str) -> str:
    """Return the user written part of a work package description."""
    raw = raw or ""
    found = [i for i in (raw.find(m) for m in (MARKER, *LEGACY_MARKERS)) if i >= 0]
    return raw[: min(found)].rstrip() if found else raw.rstrip()


def compose_description(user_part: str, block: str) -> str:
    """Join the user written part and the InvenTree block."""
    return f"{user_part}\n\n{block}" if user_part else block


def render_block(rows: list[tuple[str, str]], details: str = "") -> str:
    """Markdown block with the InvenTree details."""
    lines = [MARKER, "", "| | |", "|---|---|"]
    lines += [f"| **{k}** | {str(v).replace('|', '/')} |" for k, v in rows]
    if details:
        lines += ["", details]
    lines += ["", "_Dieser Abschnitt wird von InvenTree gepflegt._"]
    return "\n".join(lines)


def parse_mapping(text: str) -> dict[str, str]:
    """Parse 'A=b; C=d' (or one pair per line) into a dict."""
    result = {}
    for part in (text or "").replace("\n", ";").split(";"):
        if "=" in part:
            key, _, value = part.partition("=")
            if key.strip():
                result[key.strip()] = value.strip()
    return result


def read_meta(instance) -> dict:
    """Sync metadata of an order (also understands the previous plugin's format)."""
    md = dict(instance.get_metadata(META_KEY) or {})
    if "wp" not in md and md.get("work_package_id"):
        return {"wp": int(md["work_package_id"])}
    return md


def iso_duration(hours: float) -> str:
    """Hours as ISO 8601 duration for OpenProject ('PT2H30M')."""
    minutes = round(hours * 60)
    h, m = divmod(minutes, 60)
    return f"PT{h}H{m}M" if m else f"PT{h}H"


def option_links(spec: dict, value, multi: bool) -> tuple[object, list[str]]:
    """Links to the list options matching ``value`` ('A, B' for several).

    Returns (link or list of links, values without a matching option).
    """
    options = {}
    for opt in spec.get("_links", {}).get("allowedValues") or []:
        if opt.get("title") and opt.get("href"):
            options[opt["title"].strip().lower()] = opt["href"]
    for opt in spec.get("_embedded", {}).get("allowedValues") or []:
        href = opt.get("_links", {}).get("self", {}).get("href")
        if opt.get("value") and href:
            options[str(opt["value"]).strip().lower()] = href

    wanted = [v.strip() for v in str(value or "").split(",") if v.strip()]
    if not multi:
        wanted = wanted[:1]
    hrefs = [options.get(v.lower()) for v in wanted]
    missing = [v for v, h in zip(wanted, hrefs) if not h]
    hrefs = [h for h in hrefs if h]
    if multi:
        return [{"href": h} for h in hrefs], missing
    return {"href": hrefs[0] if hrefs else None}, missing


def match_principal(principals: list[dict], who: dict) -> dict | None:
    """OpenProject user or group for an InvenTree owner.

    Users are matched by email, then by login (email or username), then by
    name; groups by name. Emails are only visible to OpenProject admins (or
    when users do not hide them), hence the fallbacks.
    """

    def norm(value) -> str:
        # OpenProject names can contain double spaces ("Lasse  Willmann")
        return " ".join(str(value or "").split()).lower()

    if who.get("type") == "group":
        candidates = [("name", who.get("name"))]
        kind = "Group"
    else:
        candidates = [
            ("email", who.get("email")),
            ("login", who.get("email")),
            ("login", who.get("login")),
            ("name", who.get("name")),
        ]
        kind = "User"
    for attr, value in candidates:
        if not norm(value):
            continue
        for principal in principals:
            if principal.get("_type") == kind and norm(principal.get(attr)) == norm(
                value
            ):
                return principal
    return None


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Sync engine


class Syncer:
    """Runs the sync for one plugin configuration."""

    def __init__(self, plugin: OpenProjectSync):
        self.plugin = plugin
        self._client: OpenProjectClient | None = None

    # Configuration

    def setting(self, key: str):
        """Plugin setting value."""
        return self.plugin.get_setting(key)

    @property
    def base_url(self) -> str:
        """OpenProject base URL."""
        return (self.setting("OPENPROJECT_URL") or "").strip().rstrip("/")

    @property
    def prefix(self) -> str:
        """Prefix of work package references in text."""
        return self.setting("REFERENCE_PREFIX") or refs.DEFAULT_PREFIX

    def is_configured(self) -> bool:
        """URL and API key are set."""
        return bool(self.base_url and self.setting("OPENPROJECT_API_KEY"))

    @property
    def client(self) -> OpenProjectClient:
        """API client, raises SyncError if not configured."""
        if not self.is_configured():
            raise SyncError(
                "OpenProject URL und API-Schlüssel in den Plugin-Einstellungen setzen."
            )
        if self._client is None:
            self._client = OpenProjectClient(
                self.base_url, self.setting("OPENPROJECT_API_KEY")
            )
        return self._client

    def bind(self, adapter: OrderAdapter) -> OrderAdapter:
        """Give an adapter access to the plugin settings."""
        adapter.settings_lookup = self.setting
        return adapter

    def wp_url(self, key) -> str:
        """Browser URL of a work package."""
        return refs.work_package_url(self.base_url, str(key))

    def enabled(self, adapter: OrderAdapter) -> bool:
        """Sync is enabled for this order type."""
        return bool(self.setting(adapter.sync_setting))

    # Metadata

    @staticmethod
    def meta(adapter: OrderAdapter) -> dict:
        """Sync metadata of an order."""
        return read_meta(adapter.instance)

    @staticmethod
    def save_meta(adapter: OrderAdapter, md: dict | None, **fields) -> None:
        """Store metadata (and other fields) without triggering save events."""
        inst = adapter.instance
        metadata = dict(inst.metadata or {})
        if md is None:
            metadata.pop(META_KEY, None)
        else:
            metadata[META_KEY] = md
        inst.metadata = metadata
        for key, value in fields.items():
            setattr(inst, key, value)
        type(inst).objects.filter(pk=inst.pk).update(metadata=metadata, **fields)

    # References

    def text_refs(self, adapter: OrderAdapter) -> list[refs.WorkPackageRef]:
        """All work package references in the order's text fields."""
        found: dict[str, refs.WorkPackageRef] = {}
        for text in adapter.texts():
            for ref in refs.find_refs(text, self.prefix):
                found.setdefault(ref.key, ref)
        return list(found.values())

    def link_field_ref(self, adapter: OrderAdapter) -> refs.WorkPackageRef | None:
        """A work package URL pasted into the order's link field."""
        return refs.find_url_ref(
            getattr(adapter.instance, "link", "") or "", self.base_url
        )

    @property
    def project_setting(self) -> str:
        """Configured target project identifier (also accepts a project URL)."""
        return refs.project_key(self.setting("PROJECT") or "")

    def target_project_id(self, override: str | None = None) -> str:
        """Numeric id of the project new work packages are created in."""
        identifier = refs.project_key(override or "") or self.project_setting
        if not identifier:
            raise SyncError(
                "Kein Projekt eingestellt (Plugin-Einstellung Projekt, z. B. FER_27)."
            )
        project = self.client.get_project(identifier)
        if project is None:
            raise SyncError(
                f"Projekt {identifier} nicht gefunden oder keine Berechtigung."
            )
        return str(project["id"])

    # Entry points

    def sync(
        self, adapter: OrderAdapter, wp: dict | None = None, allow_create: bool = True
    ) -> str:
        """Sync one order. Returns a short description of what happened."""
        self.bind(adapter)
        md = self.meta(adapter)
        try:
            if not md.get("wp"):
                ref = self.link_field_ref(adapter)
                if ref:
                    return self.link(adapter, ref.key)
                if allow_create and self._should_auto_create(adapter):
                    return self.create(adapter)
                return "nicht verknüpft"

            if wp is None:
                wp = self.client.get_work_package(md["wp"])
            if wp is None:
                # Deleted in OpenProject (e.g. to change its type): drop the link
                # and start over with a new work package
                gone = md.get("display") or md["wp"]
                logger.info(
                    "OpenProject: %s for %s is gone, unlinking", gone, adapter.reference
                )
                self.unlink(adapter)
                md = {}
                if allow_create and self._should_auto_create(adapter):
                    return f"{gone} war gelöscht, {self.create(adapter)}"
                return f"{gone} war gelöscht, Verknüpfung entfernt"
            return self._reconcile(adapter, wp, md)
        except (SyncError, OpenProjectError) as exc:
            logger.warning("OpenProject sync %s: %s", adapter.reference, exc)
            if md.get("wp"):
                md["error"] = str(exc)
                self.save_meta(adapter, md)
            raise

    def _should_auto_create(self, adapter: OrderAdapter) -> bool:
        return (
            bool(self.setting("AUTO_CREATE"))
            and bool(self.project_setting)
            and adapter.is_active()
        )

    def link(self, adapter: OrderAdapter, key: str) -> str:
        """Link an order to an existing work package (keeps its subject)."""
        self.bind(adapter)
        wp = self.client.get_work_package(refs.normalize_key(str(key)))
        if wp is None:
            raise SyncError(f"Arbeitspaket {key} nicht gefunden.")
        other = self._linked_order(adapter, wp["id"])
        if other:
            raise SyncError(f"Arbeitspaket ist bereits mit {other} verknüpft.")
        md = {
            "wp": wp["id"],
            "display": wp.get("displayId") or str(wp["id"]),
            "base": {"subject": adapter.subject()},
        }
        self.save_meta(adapter, md)
        self._reconcile(adapter, wp, md)
        return f"verknüpft mit {md['display']}"

    def move(self, adapter: OrderAdapter, project: str) -> str:
        """Move the linked work package to another project."""
        self.bind(adapter)
        md = self.meta(adapter)
        wp = self.client.get_work_package(md["wp"]) if md.get("wp") else None
        if wp is None:
            raise SyncError("Kein verknüpftes Arbeitspaket.")
        project_id = self.target_project_id(project)
        if href_id(wp["_links"]["project"]["href"]) == project_id:
            return "bereits in diesem Projekt"
        payload = {
            "lockVersion": wp["lockVersion"],
            "_links": {
                "project": {"href": f"/api/v3/projects/{project_id}"},
                "type": {"href": f"/api/v3/types/{self._type_id(project_id, adapter)}"},
            },
        }
        wp = self.client.update_work_package(wp["id"], payload)
        md.update(
            {
                "lock": wp.get("lockVersion"),
                "display": wp.get("displayId") or str(wp["id"]),
            }
        )
        self.save_meta(adapter, md, **self._link_field(adapter, md))
        return f"verschoben, jetzt {md['display']}"

    def _link_field(self, adapter: OrderAdapter, md: dict) -> dict:
        """Point the order's link field at the work package (if empty or ours)."""
        if not hasattr(adapter.instance, "link"):
            return {}
        current = adapter.instance.link or ""
        if current and not refs.find_url_ref(current, self.base_url):
            return {}
        return {"link": self.wp_url(md["display"])}

    def unlink(self, adapter: OrderAdapter) -> None:
        """Remove the link (the work package itself stays)."""
        fields = {}
        link = getattr(adapter.instance, "link", None)
        if link and refs.find_url_ref(link, self.base_url):
            fields["link"] = ""
        self.save_meta(adapter, None, **fields)

    def create(self, adapter: OrderAdapter, project: str | None = None) -> str:
        """Create a work package for an order in the target project."""
        self.bind(adapter)
        client = self.client
        project_id = self.target_project_id(project)

        type_id = self._type_id(project_id, adapter)
        values = self._owned_values(adapter)
        dates = adapter.dates()
        payload = {
            "subject": values["subject"],
            "description": {
                "format": "markdown",
                "raw": self._block(adapter),
            },
            "_links": {
                "project": {"href": f"/api/v3/projects/{project_id}"},
                "type": {"href": f"/api/v3/types/{type_id}"},
            },
            **{k: v for k, v in dates.items() if v},
        }
        extra = [
            f
            for f in values
            if f not in ("subject", "block")
            and not (f in ("parent", "responsible") and not values[f])
        ]
        warnings = self._apply_owned(payload, values, extra, project_id, type_id)

        wp = self._write(client.create_work_package, payload, warnings)

        md = {
            "wp": wp["id"],
            "display": wp.get("displayId") or str(wp["id"]),
            "lock": wp.get("lockVersion"),
            "base": {**values, **{f: wp.get(f) for f in DATE_FIELDS}},
            "synced": _now(),
            "warnings": warnings,
        }
        self._ensure_relations(adapter, md)
        self.save_meta(adapter, md, **self._link_field(adapter, md))
        logger.info("OpenProject: created %s for %s", md["display"], adapter.reference)
        return f"{md['display']} angelegt"

    def poll(self) -> dict[str, int]:
        """Pull changes made in OpenProject for all linked, open orders."""
        from django.db.models import Q

        stats = {"checked": 0, "changed": 0, "errors": 0}
        if not self.is_configured():
            return stats
        for adapter_cls in ADAPTERS.values():
            if not self.setting(adapter_cls.sync_setting):
                continue
            orders = adapter_cls.model().objects.filter(
                Q(metadata__openproject__wp__isnull=False)
                | Q(metadata__openproject__work_package_id__isnull=False),
                status__in=adapter_cls.active_codes(),
            )
            by_wp = {}
            for order in orders:
                wp_id = read_meta(order).get("wp")
                if wp_id:
                    by_wp[wp_id] = adapter_cls(order)
            if not by_wp:
                continue
            wps = self.client.get_work_packages(list(by_wp))
            for wp_id, adapter in by_wp.items():
                stats["checked"] += 1
                wp = wps.get(wp_id)
                if wp and wp.get("lockVersion") == self.meta(adapter).get("lock"):
                    continue
                try:
                    self.sync(adapter, wp=wp)
                    stats["changed"] += 1
                except (SyncError, OpenProjectError):
                    stats["errors"] += 1
        return stats

    # Internals

    def _adopt(self, child: OrderAdapter, parent_wp: int) -> None:
        """Make ``parent_wp`` the parent of a child order's work package."""
        child_md = self.meta(child)
        wp = self.client.get_work_package(child_md["wp"])
        if wp is None or href_id(wp["_links"].get("parent", {}).get("href")) == str(
            parent_wp
        ):
            return
        try:
            wp = self.client.update_work_package(
                wp["id"],
                {
                    "lockVersion": wp["lockVersion"],
                    "_links": {
                        "parent": {"href": f"/api/v3/work_packages/{parent_wp}"}
                    },
                },
            )
        except OpenProjectError as exc:
            if exc.status not in (409, 422):
                raise
            logger.info("OpenProject: parent for %s not set: %s", child.reference, exc)
            return
        child_md.setdefault("base", {})["parent"] = parent_wp
        child_md["lock"] = wp.get("lockVersion")
        self.save_meta(child, child_md)

    def _ensure_relations(self, adapter: OrderAdapter, md: dict) -> None:
        """Create missing relations; done ones are remembered in md['relations'].

        - ``OP#`` references in the order: 'relates'
        - Purchase order for an external build: purchase order 'precedes' build
        """
        own = md["wp"]
        done = set(md.get("relations") or [])
        wanted = []  # (key, from_id, to_id, type) - to_id may be a ref key to resolve
        for ref in self.text_refs(adapter):
            wanted.append((f"relates:{ref.key}", own, ref.key, "relates"))
        for order in adapter.preceding_orders():
            wp_id = read_meta(order).get("wp")
            if wp_id:
                wanted.append((f"after:{wp_id}", wp_id, own, "precedes"))
        for order in adapter.following_orders():
            wp_id = read_meta(order).get("wp")
            if wp_id:
                wanted.append((f"before:{wp_id}", own, wp_id, "precedes"))

        for key, from_id, to_id, rel_type in wanted:
            if key in done:
                continue
            if rel_type == "relates":
                target = self.client.get_work_package(to_id)
                if target is None or target["id"] == own:
                    continue
                to_id = target["id"]
            try:
                self.client.create_relation(from_id, to_id, rel_type)
            except OpenProjectError as exc:
                # 422: relation exists already (e.g. created from the other order) or not allowed
                if exc.status != 422:
                    raise
                logger.info(
                    "OpenProject relation %s for %s: %s", key, adapter.reference, exc
                )
            done.add(key)
        # Children whose work package was created before this one: set their parent now
        for order in adapter.child_orders():
            key = f"child:{read_meta(order).get('wp')}"
            if not read_meta(order).get("wp") or key in done:
                continue
            self._adopt(adapter_for(order), own)
            done.add(key)

        md["relations"] = sorted(done)

    def _linked_order(self, adapter: OrderAdapter, wp_id: int) -> str | None:
        for adapter_cls in ADAPTERS.values():
            qs = adapter_cls.model().objects.filter(metadata__openproject__wp=wp_id)
            if adapter_cls is type(adapter):
                qs = qs.exclude(pk=adapter.instance.pk)
            other = qs.first()
            if other:
                return other.reference
        return None

    def _type_id(self, project_id: str, adapter: OrderAdapter) -> str:
        types = self.client.project_types(project_id)
        if not types:
            raise SyncError(
                f"Im Projekt {project_id} sind keine Arbeitspaket-Typen aktiviert."
            )
        wanted = (self.setting(adapter.type_setting_key()) or "").strip().lower()
        match = next((t for t in types if t["name"].lower() == wanted), None)
        if match is None:
            match = next((t for t in types if not t.get("isMilestone")), types[0])
            if wanted:
                logger.info(
                    "OpenProject: type %r not in project %s, using %r",
                    wanted,
                    project_id,
                    match["name"],
                )
        return str(match["id"])

    def _machine_field(self) -> str | None:
        """Sync field of the custom field mapped to 'machine', if any."""
        for cf_name, source in parse_mapping(self.setting("CUSTOM_FIELDS")).items():
            if source == "machine":
                return f"cf:{cf_name}"
        return None

    def _cf_value(self, wp: dict, name: str) -> str | None:
        """Current value of a custom field (by name) in a work package."""
        project_id = href_id(wp["_links"]["project"]["href"])
        type_id = href_id(wp["_links"]["type"]["href"])
        schema = self.client.schema(project_id, type_id)
        key = next(
            (
                k
                for k, s in schema.items()
                if k.startswith("customField")
                and isinstance(s, dict)
                and s.get("name") == name
            ),
            None,
        )
        if key is None:
            return None
        link = wp.get("_links", {}).get(key)
        if isinstance(link, dict):
            return link.get("title") or ""
        if isinstance(link, list):
            return ", ".join(x.get("title", "") for x in link)
        value = wp.get(key)
        return value.get("raw", "") if isinstance(value, dict) else (value or "")

    def _plan_machine(
        self, adapter, wp, values, base, push, new_base, warnings
    ) -> str | None:
        """Two way sync of the machine field. Returns a value to pull, or None."""
        field = self._machine_field()
        if not field or field not in values:
            return None
        op_value = self._cf_value(wp, field[3:])
        if op_value is None:
            return None
        action, value = plan_two_way(
            values[field], op_value, base.get(field), True, field in base
        )
        if field in push and action != "push":
            push.remove(field)
        new_base[field] = value
        return value if action == "pull" else None

    @staticmethod
    def _block(adapter: OrderAdapter) -> str:
        return render_block(adapter.info_rows(), adapter.details_markdown())

    def _owned_values(self, adapter: OrderAdapter) -> dict:
        """Values InvenTree owns, keyed by sync field name."""
        values = {
            "subject": adapter.subject(),
            "block": _digest(self._block(adapter)),
        }
        parent = adapter.parent_order()
        values["parent"] = read_meta(parent).get("wp") if parent is not None else None
        status = parse_mapping(self.setting("STATUS_MAP")).get(adapter.status_name())
        if status:
            values["status"] = status
        if self.setting("PUSH_PROGRESS"):
            progress = adapter.progress()
            if progress is not None:
                values["percentageDone"] = progress
        if self.setting("PUSH_RESPONSIBLE"):
            values["responsible"] = adapter.responsible()
        if self.setting("PUSH_WORK"):
            hours = adapter.work_hours()
            if hours:
                values["estimatedTime"] = iso_duration(hours)
        field_values = adapter.field_values()
        for cf_name, source in parse_mapping(self.setting("CUSTOM_FIELDS")).items():
            if source in field_values and field_values[source] is not None:
                values[f"cf:{cf_name}"] = field_values[source]
        return values

    def _apply_owned(
        self,
        payload: dict,
        values: dict,
        fields: list[str],
        project_id: str,
        type_id: str,
    ) -> list[str]:
        """Write owned values into an API payload. Returns warnings."""
        warnings = []
        schema = None
        for field in fields:
            value = values.get(field)
            if field == "subject":
                payload["subject"] = value
            elif field == "status":
                status = self.client.status_by_name(value)
                if status:
                    payload["_links"]["status"] = {
                        "href": status["_links"]["self"]["href"]
                    }
                else:
                    warnings.append(f'Status "{value}" gibt es in OpenProject nicht.')
            elif field == "parent":
                href = f"/api/v3/work_packages/{value}" if value else None
                payload["_links"]["parent"] = {"href": href}
            elif field == "responsible":
                href = None
                if value:
                    principal = match_principal(
                        self.client.principals(project_id), value
                    )
                    if principal is None:
                        who = value.get("email") or value.get("name")
                        warnings.append(
                            f'Verantwortlich: "{who}" ist in OpenProject nicht Mitglied des Projekts.'
                        )
                        continue
                    href = principal["_links"]["self"]["href"]
                payload["_links"]["responsible"] = {"href": href}
            elif field in ("percentageDone", "estimatedTime"):
                payload[field] = value
            elif field.startswith("cf:"):
                if schema is None:
                    schema = self.client.schema(project_id, type_id)
                name = field[3:]
                key, spec = next(
                    (
                        (k, s)
                        for k, s in schema.items()
                        if k.startswith("customField")
                        and isinstance(s, dict)
                        and s.get("name") == name
                    ),
                    (None, None),
                )
                if key is None:
                    warnings.append(
                        f'Feld "{name}" ist für diesen Typ nicht aktiviert.'
                    )
                    continue
                kind = spec.get("type")
                if kind == "Formattable":
                    payload[key] = {"format": "markdown", "raw": str(value)}
                elif kind == "Integer":
                    payload[key] = round(float(value))
                elif kind == "Float":
                    payload[key] = float(value)
                elif kind == "Boolean":
                    payload[key] = bool(value)
                elif kind in ("String", "Date"):
                    payload[key] = str(value)
                elif kind in ("CustomOption", "[]CustomOption"):
                    payload.setdefault("_links", {})[key], missing = option_links(
                        spec, value, multi=kind.startswith("[]")
                    )
                    if missing:
                        warnings.append(
                            f'"{", ".join(missing)}" fehlt in der Liste "{name}" in OpenProject.'
                        )
                else:
                    warnings.append(
                        f'Feld "{name}" hat einen nicht unterstützten Typ ({kind}).'
                    )
        return warnings

    def _write(self, func, payload: dict, warnings: list[str]) -> dict:
        """Send a payload, retrying once without fields OpenProject rejects (422)."""
        try:
            return func(payload)
        except OpenProjectError as exc:
            if exc.status != 422:
                raise
            rejected = exc.invalid_attributes()
            dropped = [
                a for a in rejected if a in payload or a in payload.get("_links", {})
            ]
            if not dropped or set(dropped) & {
                "subject",
                "project",
                "type",
                "lockVersion",
            }:
                raise
            for attr in dropped:
                payload.pop(attr, None)
                payload.get("_links", {}).pop(attr, None)
            warnings.append(
                f"OpenProject hat {', '.join(sorted(dropped))} abgelehnt: {exc}"
            )
            return func(payload)

    def _reconcile(self, adapter: OrderAdapter, wp: dict, md: dict) -> str:
        base = md.get("base", {})
        new_base = dict(base)
        values = self._owned_values(adapter)

        push = [f for f, v in values.items() if base.get(f) != v]
        description = (wp.get("description") or {}).get("raw") or ""
        if MARKER not in description and "block" not in push:
            push.append("block")
        new_base.update(values)

        pulls = {}
        pull_enabled = bool(self.setting("PULL_DATES"))
        inv_dates = adapter.dates()
        for field in DATE_FIELDS:
            if field == "startDate" and not hasattr(adapter.instance, "start_date"):
                continue
            action, value = plan_two_way(
                inv_dates[field],
                wp.get(field),
                base.get(field),
                pull_enabled,
                field in base,
            )
            if action == "push":
                push.append(field)
            elif action == "pull":
                pulls[field] = value
            new_base[field] = value

        warnings = []
        machine_pull = self._plan_machine(
            adapter, wp, values, base, push, new_base, warnings
        )

        lock = wp.get("lockVersion")
        if push:
            project_id = href_id(wp["_links"]["project"]["href"])
            type_id = href_id(wp["_links"]["type"]["href"])
            payload = {"lockVersion": lock, "_links": {}}
            if "block" in push:
                block = self._block(adapter)
                payload["description"] = {
                    "format": "markdown",
                    "raw": compose_description(split_description(description), block),
                }
            for field in DATE_FIELDS:
                if field in push:
                    payload[field] = inv_dates[field]
            warnings = self._apply_owned(
                payload,
                values,
                [f for f in push if f in values and f != "block"],
                project_id,
                type_id,
            )
            if not payload["_links"]:
                del payload["_links"]
            wp = self._write(
                lambda p: self.client.update_work_package(wp["id"], p),
                payload,
                warnings,
            )
            lock = wp.get("lockVersion")

        md.update(
            {
                "display": wp.get("displayId") or str(wp["id"]),
                "lock": lock,
                "base": new_base,
                "synced": _now(),
                "error": None,
                "warnings": warnings,
            }
        )
        self._ensure_relations(adapter, md)

        machine_pulled = False
        if machine_pull is not None:
            warning = adapter.set_machine(machine_pull)
            if warning:
                warnings.append(warning)
                # Keep the old base, so the next sync tries again instead of pushing back
                md["base"][self._machine_field()] = base.get(self._machine_field())
            machine_pulled = not warning

        if pulls:
            adapter.set_dates(pulls)
            adapter.instance.set_metadata(META_KEY, md, commit=False)
            # Regular save: InvenTree validation runs, the resulting event finds nothing to push
            try:
                adapter.instance.save()
            except Exception as exc:
                raise SyncError(
                    f"Termine aus OpenProject nicht übernehmbar: {exc}"
                ) from exc
            logger.info(
                "OpenProject: %s dates from %s: %s",
                adapter.reference,
                md["display"],
                pulls,
            )
        else:
            self.save_meta(adapter, md)

        parts = []
        if push:
            parts.append("gesendet: " + ", ".join(sorted(push)))
        pulled = sorted(pulls) + (["Maschine"] if machine_pulled else [])
        if pulled:
            parts.append("übernommen: " + ", ".join(pulled))
        return "; ".join(parts) or "keine Änderungen"
