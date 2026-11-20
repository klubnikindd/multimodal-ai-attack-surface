"""Лицензионный гейт весов моделей (D11, SEC-08, §12.2–12.3)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .constants import DENIED_WEIGHT_LICENSES, UNKNOWN_LICENSE_VALUES
from .errors import LicenseError

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

_SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")
_REVISION = re.compile(r"^[a-fA-F0-9]{7,64}$")
_DENIED_RE = re.compile(
    r"^(?:" + "|".join(re.escape(item) + r"(?:$|[-.])" for item in DENIED_WEIGHT_LICENSES) + r")"
)


def default_manifest_path() -> Path:
    return Path(__file__).resolve().parents[1] / "models" / "manifest.yaml"


@dataclass(slots=True)
class ManifestReport:
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    models: list[dict[str, Any]] = field(default_factory=list)
    enabled_count: int = 0

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "issues": list(self.issues),
            "warnings": list(self.warnings),
            "enabled_count": self.enabled_count,
            "models": list(self.models),
        }


def load_manifest(path: str | Path | None = None) -> dict[str, Any]:
    manifest_path = Path(path) if path else default_manifest_path()
    if not manifest_path.is_file():
        raise LicenseError(f"Манифест моделей не найден: {manifest_path}")
    try:
        if yaml is None:
            raise LicenseError("PyYAML не установлен")
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise LicenseError(f"Не удалось прочитать манифест: {exc}") from exc
    if not isinstance(data, dict):
        raise LicenseError("Манифест должен быть объектом")
    return data


def _normalize_license(value: Any) -> str:
    return str(value or "").strip().lower()


def _is_denied(license_name: str) -> bool:
    return bool(_DENIED_RE.match(license_name))


def _positive_int(value: Any) -> bool:
    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return False


def validate_manifest(data: dict[str, Any], *, target_profile: str | None = None) -> ManifestReport:
    report = ManifestReport()
    models = data.get("models")
    if not isinstance(models, list) or not models:
        report.issues.append("manifest.models должен быть непустым списком")
        return report
    seen: set[str] = set()
    for index, model in enumerate(models):
        if not isinstance(model, dict):
            report.issues.append(f"models[{index}] должен быть объектом")
            continue
        model_id = str(model.get("id") or "").strip()
        if not model_id:
            report.issues.append(f"models[{index}].id обязателен")
            continue
        if model_id in seen:
            report.issues.append(f"дублирующийся id модели: {model_id}")
        seen.add(model_id)
        enabled = bool(model.get("enabled", True))
        role = str(model.get("role") or "")
        variant = str(model.get("variant") or "")
        license_name = _normalize_license(model.get("license"))
        summary = {
            "id": model_id,
            "role": role,
            "enabled": enabled,
            "license": license_name,
            "commercial_use": model.get("commercial_use"),
            "license_verified_at": model.get("license_verified_at"),
            "verification_status": model.get("verification_status", "pending"),
            "allowed_profiles": model.get("allowed_profiles", []),
        }
        report.models.append(summary)
        if enabled:
            report.enabled_count += 1
        if _is_denied(license_name):
            report.issues.append(f"{model_id}: запрещённая лицензия весов {license_name!r}")
        elif license_name in UNKNOWN_LICENSE_VALUES or not license_name:
            if enabled:
                report.issues.append(f"{model_id}: неизвестная лицензия — fail-closed")
            else:
                report.warnings.append(f"{model_id}: лицензия не подтверждена (модель отключена)")
        if enabled:
            if not model.get("license_source_url"):
                report.issues.append(f"{model_id}: отсутствует license_source_url")
            if not model.get("license_verified_at"):
                report.issues.append(f"{model_id}: отсутствует license_verified_at")
            else:
                try:
                    verified = date.fromisoformat(str(model["license_verified_at"])[:10])
                    if verified > date.today():
                        report.issues.append(f"{model_id}: license_verified_at в будущем")
                except ValueError:
                    report.issues.append(f"{model_id}: некорректный license_verified_at")
            if model.get("commercial_use") is not True:
                report.issues.append(f"{model_id}: commercial_use должен быть true для пользовательской сборки")
            revision = str(model.get("revision") or "")
            if not _REVISION.match(revision):
                report.issues.append(f"{model_id}: revision должен быть пиннингом commit sha, а не {revision!r}")
            files = model.get("files")
            if not isinstance(files, list) or not files:
                report.issues.append(f"{model_id}: files должен быть непустым списком")
            else:
                for file in files:
                    if not isinstance(file, dict) or not _SHA256.match(str(file.get("sha256") or "")):
                        report.issues.append(f"{model_id}: у файла модели отсутствует корректный sha256")
                        break
        if role == "vlm":
            if variant.lower() != "instruct":
                report.issues.append(f"{model_id}: VLM должен быть *-Instruct")
            if model.get("thinking") is not False:
                report.issues.append(f"{model_id}: thinking должен быть false")
            if "lite" in {str(p).lower() for p in model.get("allowed_profiles", [])}:
                report.issues.append(f"{model_id}: VLM не может быть в Lite-профиле")
            if enabled and not (
                _positive_int(model.get("max_output_tokens")) and _positive_int(model.get("image_max_side"))
            ):
                report.issues.append(f"{model_id}: нужны числовые max_output_tokens и image_max_side")
        if target_profile and enabled:
            allowed = {str(p).lower() for p in model.get("allowed_profiles", [])}
            if allowed and target_profile.lower() not in allowed:
                report.warnings.append(f"{model_id}: не разрешена для профиля {target_profile}")
    if report.enabled_count == 0:
        report.warnings.append(
            "Ни одна модель не включена: M0-веса не подтверждены, используется concept-hash fallback"
        )
    return report


def validate_manifest_file(path: str | Path | None = None, *, target_profile: str | None = None) -> ManifestReport:
    return validate_manifest(load_manifest(path), target_profile=target_profile)


__all__ = [
    "ManifestReport",
    "default_manifest_path",
    "load_manifest",
    "validate_manifest",
    "validate_manifest_file",
]
