"""Mapeo de etiquetas de modelos al vocabulario canonico de percepcion."""

from __future__ import annotations

# Vocabulario canonico v2 consumido por CR-01 / CR-02 (sin bare_head en salida).
CANONICAL_LABELS = frozenset({"person", "helmet", "vest"})
CONTROL_LABELS = CANONICAL_LABELS

# Etiquetas YOLO / construction-PPE → vocabulario canonico.
YOLO_LABEL_MAP: dict[str, str] = {
    "person": "person",
    "worker": "person",
    "human": "person",
    "people": "person",
    "hardhat": "helmet",
    "hard hat": "helmet",
    "helmet": "helmet",
    "safety helmet": "helmet",
    "protective helmet": "helmet",
    "safety vest": "vest",
    "reflective vest": "vest",
    "high visibility vest": "vest",
    "high-visibility vest": "vest",
    "hi-vis vest": "vest",
    "hi vis vest": "vest",
    "vest": "vest",
    # Clases de ausencia / ruido: no emitir.
    "no-hardhat": "",
    "no-hard hat": "",
    "no_hardhat": "",
    "no_helmet": "",
    "no-safety vest": "",
    "no_safety vest": "",
    "no-safety boot": "",
    "no_safety boot": "",
    "no-vest": "",
    "no_vest": "",
    "bare head": "",
    "bare_head": "",
    "mask": "",
    "glove": "",
    "gloves": "",
    "goggles": "",
    "shoes": "",
    "safety boot": "",
    "safety boots": "",
    "machinery": "",
    "vehicle": "",
    "safety cone": "",
}

# Prompts GDINO alineados al evaluador espacial del plano de control.
GDINO_PROMPTS = ["person", "helmet", "safety vest"]
GDINO_PROMPT_TO_CONTROL: dict[str, str] = {
    "person": "person",
    "helmet": "helmet",
    "safety vest": "vest",
}

# Prompts YOLOE activos (sin bare_head).
YOLOE_PROMPTS = ["person", "helmet", "vest"]
YOLOE_PROMPT_TO_CONTROL: dict[str, str] = {
    "person": "person",
    "helmet": "helmet",
    "vest": "vest",
}


def _normalize_key(raw: str) -> str:
    return raw.strip().lower().replace("_", " ")


def to_canonical_label(raw: str, *, backend: str = "yolo") -> str | None:
    """Mapea una etiqueta cruda del modelo a person/helmet/vest o None si se descarta."""
    normalized_backend = backend.strip().lower().replace("_", "-")
    key = _normalize_key(raw)

    if normalized_backend in {"yolo", "yolo-ppe", "construction-ppe"}:
        return normalize_yolo_label(raw)

    if normalized_backend in {"yoloe", "yoloe-26s"}:
        mapped = YOLOE_PROMPT_TO_CONTROL.get(key)
        if mapped is not None:
            return mapped
        for prompt, canonical in YOLOE_PROMPT_TO_CONTROL.items():
            if key == prompt or key in prompt or prompt in key:
                return canonical
        return None

    if normalized_backend in {"gdino", "grounding-dino"}:
        return normalize_gdino_label(raw, GDINO_PROMPTS)

    mapped = YOLO_LABEL_MAP.get(key)
    if mapped is None:
        mapped = YOLO_LABEL_MAP.get(raw.strip().lower())
    if mapped is None or mapped == "":
        return None
    return mapped


def normalize_yolo_label(raw: str) -> str | None:
    """Normaliza una etiqueta YOLO al vocabulario canonico o None si se descarta."""
    key = _normalize_key(raw)
    mapped = YOLO_LABEL_MAP.get(key)
    if mapped is None:
        mapped = YOLO_LABEL_MAP.get(raw.strip().lower())
    if mapped is None or mapped == "":
        return None
    return mapped


def normalize_gdino_label(raw: str, prompts: list[str]) -> str | None:
    """Mapea un span GDINO al prompt canonico mas cercano."""
    detected_lower = raw.strip().lower()
    for prompt in prompts:
        if prompt.lower() == detected_lower:
            return GDINO_PROMPT_TO_CONTROL.get(prompt, prompt)
    for prompt in prompts:
        if detected_lower in prompt.lower() or prompt.lower() in detected_lower:
            return GDINO_PROMPT_TO_CONTROL.get(prompt, prompt)
    detected_words = set(detected_lower.split())
    if not detected_words:
        return None
    best = max(prompts, key=lambda p: len(detected_words & set(p.lower().split())))
    return GDINO_PROMPT_TO_CONTROL.get(best, best)
