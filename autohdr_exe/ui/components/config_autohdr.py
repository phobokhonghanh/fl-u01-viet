"""
AutoHDR Configuration Component.
Đóng gói Category selector và Model ComboBox của AutoHDR.
"""
import customtkinter as ctk
from typing import Dict, Any
from core.cache import cache
from models.available_models import (
    CATEGORIES,
    CATEGORY_INDOOR,
    get_model_names_by_category,
    get_model_by_name_and_category,
    get_payload_model_ids,
)


class ConfigAutoHDRFrame(ctk.CTkFrame):
    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._build_ui()

    def _build_ui(self):
        ctk.CTkLabel(self, text="Cấu hình:", font=("Arial", 13, "bold")).pack(side="left", padx=(2, 4))
        ctk.CTkLabel(self, text="Style:", font=("Arial", 12)).pack(side="left", padx=(2, 4))

        cached_category = cache.get("model_category", CATEGORY_INDOOR)
        if cached_category not in CATEGORIES:
            cached_category = CATEGORY_INDOOR
        self.category_var = ctk.StringVar(value=cached_category)

        self.category_selector = ctk.CTkSegmentedButton(
            self,
            values=CATEGORIES,
            variable=self.category_var,
            command=self._on_category_changed,
            height=28,
            font=("Arial", 11, "bold"),
        )
        self.category_selector.pack(side="left", padx=(0, 8))

        ctk.CTkLabel(self, text="Model:", font=("Arial", 12)).pack(side="left", padx=(2, 4))

        initial_names = get_model_names_by_category(cached_category)
        cached_model = cache.get("model_name", "Lisa" if cached_category == CATEGORY_INDOOR else (initial_names[0] if initial_names else ""))
        if cached_model not in initial_names and initial_names:
            cached_model = initial_names[0]

        self.model_var = ctk.StringVar(value=cached_model)
        self.model_selector = ctk.CTkComboBox(
            self,
            values=initial_names,
            variable=self.model_var,
            command=self._on_model_changed,
            state="readonly",
            height=28,
            font=("Arial", 12),
        )
        self.model_selector.pack(side="left", fill="x", expand=True, padx=2)

    def _on_category_changed(self, new_category: str):
        cache.set("model_category", new_category)
        names = get_model_names_by_category(new_category)
        self.model_selector.configure(values=names)

        cached_for_cat = cache.get(f"model_name_{new_category}")
        if cached_for_cat and cached_for_cat in names:
            selected = cached_for_cat
        else:
            selected = "Lisa" if new_category == CATEGORY_INDOOR and "Lisa" in names else (names[0] if names else "")

        self.model_var.set(selected)
        cache.set("model_name", selected)

    def _on_model_changed(self, new_model_name: str):
        cache.set("model_name", new_model_name)
        cat = self.category_var.get()
        cache.set(f"model_name_{cat}", new_model_name)

    def get_preferences(self) -> Dict[str, Any]:
        cat = self.category_var.get()
        model_name = self.model_var.get()
        model = get_model_by_name_and_category(model_name, cat)
        model_id = model["id"] if model else 1
        indoor_id, outdoor_id = get_payload_model_ids(cat, model_id)

        cache.set("model_category", cat)
        cache.set("model_name", model_name)
        cache.set(f"model_name_{cat}", model_name)

        return {
            "category": cat,
            "model_name": model_name,
            "indoor_model_id": indoor_id,
            "outdoor_model_id": outdoor_id,
        }

