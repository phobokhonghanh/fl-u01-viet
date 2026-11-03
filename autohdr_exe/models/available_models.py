"""
Static AutoHDR models configuration.
Predefined list of available models filtered by variant (indoor, outdoor)
and family (twilight).
"""

from typing import Dict, List, Optional, Tuple, Any

CATEGORY_INDOOR = "Indoor"
CATEGORY_OUTDOOR = "Outdoor"
CATEGORY_BOTH = "Both"

CATEGORIES = [CATEGORY_INDOOR, CATEGORY_OUTDOOR, CATEGORY_BOTH]

# Filtered list of models from AutoHDR API
# Keeps variant in ('indoor', 'outdoor') and family == 'twilight' (when variant is null)
AVAILABLE_MODELS: List[Dict[str, Any]] = [
    # Indoor models (variant == 'indoor')
    {"id": 1, "name": "Classic", "type": "predefined", "family": None, "variant": "indoor"},
    {"id": 3, "name": "Lisa", "type": "predefined", "family": None, "variant": "indoor"},
    {"id": 16, "name": "Classic V4 [New]", "type": "predefined", "family": None, "variant": "indoor"},
    {"id": 17, "name": "Classic V4 | With Clouds [New]", "type": "predefined", "family": None, "variant": "indoor"},
    {"id": 18, "name": "Classic V4 | No Sky Replacement [New]", "type": "predefined", "family": None, "variant": "indoor"},
    {"id": 24, "name": "Lisa V4 [New]", "type": "predefined", "family": None, "variant": "indoor"},
    {"id": 25, "name": "Classic V4 With Clouds", "type": "predefined", "family": None, "variant": "indoor"},
    {"id": 26, "name": "Classic V4 Blue Sky", "type": "predefined", "family": None, "variant": "indoor"},
    {"id": 27, "name": "Classic V4 Original Sky", "type": "predefined", "family": None, "variant": "indoor"},

    # Outdoor models (variant == 'outdoor')
    {"id": 2, "name": "No Clouds", "type": "predefined", "family": None, "variant": "outdoor"},
    {"id": 9, "name": "Fluffy", "type": "predefined", "family": None, "variant": "outdoor"},
    {"id": 10, "name": "Fluffy Wispy", "type": "predefined", "family": None, "variant": "outdoor"},
    {"id": 11, "name": "Wispy", "type": "predefined", "family": None, "variant": "outdoor"},
    {"id": 19, "name": "V4 Exterior", "type": "predefined", "family": None, "variant": "outdoor"},
    {"id": 20, "name": "V4 Fluffy", "type": "predefined", "family": None, "variant": "outdoor"},
    {"id": 21, "name": "V4 Wispy", "type": "predefined", "family": None, "variant": "outdoor"},
    {"id": 22, "name": "V4 Fluffy Wispy", "type": "predefined", "family": None, "variant": "outdoor"},
    {"id": 23, "name": "V4 No Sky Replacement", "type": "predefined", "family": None, "variant": "outdoor"},
    {"id": 28, "name": "Fluffy V4", "type": "predefined", "family": None, "variant": "outdoor"},
    {"id": 29, "name": "Wispy V4", "type": "predefined", "family": None, "variant": "outdoor"},
    {"id": 30, "name": "Fluffy Wispy V4", "type": "predefined", "family": None, "variant": "outdoor"},
    {"id": 31, "name": "Original Sky V4", "type": "predefined", "family": None, "variant": "outdoor"},
    {"id": 32, "name": "Blue Sky V4", "type": "predefined", "family": None, "variant": "outdoor"},

    # Both (Twilight family models)
    {"id": 6, "name": "Twilight Golden", "type": "predefined", "family": "twilight", "variant": None},
    {"id": 7, "name": "Twilight Pink", "type": "predefined", "family": "twilight", "variant": None},
    {"id": 8, "name": "Twilight Midnight", "type": "predefined", "family": "twilight", "variant": None},
    {"id": 33, "name": "Twilight Pink V4", "type": "predefined", "family": "twilight", "variant": None},
    {"id": 34, "name": "Twilight Golden V4", "type": "predefined", "family": "twilight", "variant": None},
    {"id": 35, "name": "Twilight Natural V4", "type": "predefined", "family": "twilight", "variant": None},
]


def get_model_category(model: Dict[str, Any]) -> str:
    """Determine the selection category for a model."""
    if model.get("family") == "twilight":
        return CATEGORY_BOTH
    variant = model.get("variant")
    if variant == "indoor":
        return CATEGORY_INDOOR
    if variant == "outdoor":
        return CATEGORY_OUTDOOR
    return CATEGORY_INDOOR


def get_models_by_category(category: str) -> List[Dict[str, Any]]:
    """Return all models belonging to a category (Indoor, Outdoor, Both)."""
    return [m for m in AVAILABLE_MODELS if get_model_category(m) == category]


def get_model_names_by_category(category: str) -> List[str]:
    """Return model display names for a category."""
    return [m["name"] for m in get_models_by_category(category)]


def get_model_by_id(model_id: int) -> Optional[Dict[str, Any]]:
    """Find model by its ID."""
    for m in AVAILABLE_MODELS:
        if m["id"] == model_id:
            return m
    return None


def get_model_by_name_and_category(name: str, category: str) -> Optional[Dict[str, Any]]:
    """Find model by its name within a category."""
    for m in get_models_by_category(category):
        if m["name"] == name:
            return m
    return None


def get_payload_model_ids(category: str, model_id: int) -> Tuple[Optional[int], Optional[int]]:
    """
    Return (indoor_model_id, outdoor_model_id) for associate-and-run payload:
    - Both (Twilight): (model_id, model_id)
    - Indoor: (model_id, None)
    - Outdoor: (None, model_id)
    """
    if category == CATEGORY_BOTH:
        return model_id, model_id
    if category == CATEGORY_OUTDOOR:
        return None, model_id
    return model_id, None

