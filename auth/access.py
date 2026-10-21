"""Quién puede entrar y qué puede hacer cada uno.

Entrar: los admins (ADMIN_EMAILS o rol admin), los mails de ALLOWED_EMAILS, las
cuentas de Google Workspace de ALLOWED_EMAIL_DOMAINS (se exige el claim `hd`) y
quien fue invitado. Una cuenta deshabilitada no entra aunque cumpla lo demás.

Proyectos: owner (administra miembros), editor (edita lo del proyecto) y viewer
(lo ve). Un dashboard sin proyecto es personal: solo lo ve y edita su creador.
"""

from __future__ import annotations

from typing import Any

from .. import config
from .store import ROLES, AppStore, clean_email

# Rol sobre un dashboard según el rol en su proyecto.
_ARTIFACT_ROLE = {"owner": "editor", "editor": "editor", "viewer": "viewer"}
_RANK = {"viewer": 0, "editor": 1, "owner": 2}


class AccessError(PermissionError):
    def __init__(self, message: str, code: str = "forbidden"):
        super().__init__(message)
        self.code = code


class Access:
    def __init__(self, store: AppStore):
        self.store = store

    # --- cuentas ---------------------------------------------------------------------

    def is_admin(self, user: str) -> bool:
        if clean_email(user) in config.ADMIN_EMAILS:
            return True
        row = self.store.user(user)
        return bool(row) and row["role"] == "admin" and row["status"] == "active"

    def domain_allowed(self, email: str, hosted_domain: str | None) -> bool:
        domain = clean_email(email).rpartition("@")[2]
        return bool(domain) and domain in config.ALLOWED_EMAIL_DOMAINS and (hosted_domain or "").lower() == domain

    def may_login(self, email: str, hosted_domain: str | None = None) -> bool:
        email = clean_email(email)
        row = self.store.user(email)
        if row and row["status"] != "active":
            return False
        return bool(
            email in config.ADMIN_EMAILS
            or email in config.ALLOWED_EMAILS
            or self.domain_allowed(email, hosted_domain)
            or row  # invitado (o ya entró antes)
        )

    def invitable(self, email: str) -> bool:
        """Lo que un owner de proyecto puede sumar sin un admin: cuentas ya admitidas por config."""
        email = clean_email(email)
        domain = email.rpartition("@")[2]
        return bool(
            self.store.user(email)
            or email in config.ALLOWED_EMAILS
            or email in config.ADMIN_EMAILS
            or domain in config.ALLOWED_EMAIL_DOMAINS
        )

    # --- proyectos ---------------------------------------------------------------------

    def project_role(self, pid: str | None, user: str) -> str | None:
        return self.store.role(pid, user) if pid else None

    def require_project(self, pid: str, user: str, at_least: str = "viewer") -> dict[str, Any]:
        project = self.store.project(pid)
        role = self.project_role(pid, user)
        if not project or project["archived"] or role is None:
            raise AccessError("No existe el proyecto o no sos miembro.", "missing")
        if _RANK[role] < _RANK[at_least]:
            raise AccessError(f"Hace falta ser {at_least} del proyecto.")
        return {**project, "role": role}

    def add_member(self, pid: str, actor: str, email: str, role: str) -> None:
        if role not in ROLES:
            raise AccessError(f"Rol inválido. Válidos: {', '.join(ROLES)}.", "invalid")
        self.require_project(pid, actor, "owner")
        email = clean_email(email)
        if "@" not in email:
            raise AccessError("Poné un mail.", "invalid")
        if not self.invitable(email):
            if not self.is_admin(actor):
                raise AccessError("Ese mail no está habilitado para entrar: pedile a un admin que lo invite.")
            self.store.add_user(email, invited_by=clean_email(actor))
        elif not self.store.user(email):
            self.store.add_user(email, invited_by=clean_email(actor))
        self.store.set_member(pid, email, role, actor)

    def change_member(self, pid: str, actor: str, email: str, role: str | None) -> None:
        """Cambia el rol o, con role=None, saca al miembro. Nunca deja un proyecto sin owner."""
        email = clean_email(email)
        leaving = email == clean_email(actor) and role is None
        if not leaving:
            self.require_project(pid, actor, "owner")
        elif self.project_role(pid, actor) is None:
            raise AccessError("No sos miembro de este proyecto.", "missing")
        if role is not None and role not in ROLES:
            raise AccessError(f"Rol inválido. Válidos: {', '.join(ROLES)}.", "invalid")
        members = self.store.members(pid)
        owners = [m["user_id"] for m in members if m["role"] == "owner"]
        if email in owners and owners == [email] and role != "owner":
            raise AccessError("El proyecto se quedaría sin owner: nombrá a otro antes.", "invalid")
        if role is None:
            self.store.remove_member(pid, email)
        else:
            if email not in {m["user_id"] for m in members}:
                raise AccessError("No es miembro del proyecto.", "missing")
            self.store.set_member(pid, email, role, actor)

    # --- dashboards ------------------------------------------------------------------------

    def artifact_role(self, platform: dict[str, Any], user: str) -> str | None:
        """'owner' (lo creó), 'editor', 'viewer' o None."""
        user = clean_email(user)
        if clean_email(platform.get("owner", "")) == user:
            return "owner"
        role = self.project_role(platform.get("project_id"), user)
        return _ARTIFACT_ROLE.get(role) if role else None

    def can_view(self, platform: dict[str, Any], user: str) -> bool:
        return self.artifact_role(platform, user) is not None

    def can_edit(self, platform: dict[str, Any], user: str) -> bool:
        return self.artifact_role(platform, user) in ("owner", "editor")

    def can_move(self, platform: dict[str, Any], user: str, target_pid: str | None) -> bool:
        """Mover a un proyecto (o volver a personal): quien lo creó o un owner del proyecto actual,
        y en el proyecto de destino hay que ser editor o más."""
        current = platform.get("project_id")
        allowed_here = self.artifact_role(platform, user) == "owner" or self.project_role(current, user) == "owner"
        if not allowed_here:
            return False
        if target_pid is None:
            return clean_email(platform.get("owner", "")) == clean_email(user)
        role = self.project_role(target_pid, user)
        return role is not None and _RANK[role] >= _RANK["editor"]
