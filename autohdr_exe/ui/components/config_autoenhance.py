"""
Autoenhance Configuration Component.
Đóng gói 10 Dropdowns và 2 Radio Buttons của Autoenhance.
"""
import customtkinter as ctk
from typing import Dict, Any, Optional
from core.cache import cache

PRESET_MAP = {
    "none": None,
    "vivid": "d8a5ecac-202e-4d1f-8ed4-39669d4f81cb",
    "warm": "c1824178-a113-43f3-a9b5-15caa91d93df",
    "natural": "b02886eb-a8e8-4ffc-bcd4-e9c901dddb52",
}

CLOUD_MAP = {
    "off": None,
    "clear": "CLEAR",
    "low cloud": "LOW_CLOUD",
    "high cloud": "HIGH_CLOUD",
    "dusk cloud": "DUSK_CLOUD",
    "whispy cloud": "WHISPY_CLOUD",
}

PERSP_MAP = {
    "off": None,
    "preserve content": "PRESERVE_CONTENT",
    "prioritize perspective": "PRIORITIZE_PERSPECTIVE",
}

WIN_MAP = {
    "off": "NONE",
    "only windows": "ONLY_WINDOWS",
    "windows_with_skies": "WINDOWS_WITH_SKIES",
}


class ConfigAutoenhanceFrame(ctk.CTkFrame):
    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self._build_ui()

    def _create_dropdown_cell(self, row, col, label_text, cache_key, default_val, options):
        cell = ctk.CTkFrame(self, fg_color="transparent")
        cell.grid(row=row, column=col, sticky="ew", padx=3, pady=2)
        lbl = ctk.CTkLabel(cell, text=label_text, font=("Arial", 11, "bold"), width=85, anchor="w")
        lbl.pack(side="left", padx=(2, 4))

        cached_val = cache.get(cache_key, default_val)
        if isinstance(cached_val, str):
            cached_val = cached_val.lower()
        if cached_val not in options:
            cached_val = default_val
        var = ctk.StringVar(value=cached_val)

        combo = ctk.CTkComboBox(
            cell,
            values=options,
            variable=var,
            command=lambda val, k=cache_key: cache.set(k, val),
            state="readonly",
            height=26,
            font=("Arial", 11),
            dropdown_font=("Arial", 11),
        )
        combo.pack(side="right", fill="x", expand=True)
        return var, combo

    def _build_ui(self):
        # Hàng 0: Preset (Trái) & Enhancement (Phải)
        self.preset_var, self.preset_selector = self._create_dropdown_cell(
            0, 0, "Preset:", "ae_preset", "none", ["none", "vivid", "warm", "natural"]
        )
        self.enhancement_var, self.enhancement_selector = self._create_dropdown_cell(
            0, 1, "Enhance:", "ae_enhancement_style", "natural", ["natural", "dynamic"]
        )

        # Hàng 1: Cloud (Trái) & Bracket (Phải)
        self.cloud_var, self.cloud_selector = self._create_dropdown_cell(
            1, 0, "Cloud:", "ae_cloud_type", "off", ["off", "clear", "low cloud", "high cloud", "dusk cloud", "whispy cloud"]
        )
        self.brackets_var, self.brackets_selector = self._create_dropdown_cell(
            1, 1, "Bracket:", "ae_brackets", "1", [str(i) for i in range(1, 11)]
        )

        # Hàng 2: Perspective (Trái) & Window Pull (Phải)
        self.persp_var, self.persp_selector = self._create_dropdown_cell(
            2, 0, "Perspective:", "ae_perspective_mode", "off", ["off", "preserve content", "prioritize perspective"]
        )
        self.win_pull_var, self.win_pull_selector = self._create_dropdown_cell(
            2, 1, "Window Pull:", "ae_window_pull", "off", ["off", "only windows", "windows_with_skies"]
        )

        # Hàng 3: TV Blackout (Trái) & Fireplace (Phải)
        self.tv_var, self.tv_selector = self._create_dropdown_cell(
            3, 0, "TV Blackout:", "ae_tv_blackout", "off", ["off", "black out"]
        )
        self.fire_var, self.fire_selector = self._create_dropdown_cell(
            3, 1, "Fireplace:", "ae_fireplace", "off", ["off", "alight"]
        )

        # Hàng 4: Photographer (Trái) & Grass (Phải)
        self.photo_var, self.photo_selector = self._create_dropdown_cell(
            4, 0, "Photographer:", "ae_photographer", "off", ["off", "remove"]
        )
        self.grass_var, self.grass_selector = self._create_dropdown_cell(
            4, 1, "Grass:", "ae_grass", "off", ["off", "green"]
        )

        # Hàng 5: 2 Radio Buttons (Click toggle)
        radio_row = ctk.CTkFrame(self, fg_color="transparent")
        radio_row.grid(row=5, column=0, columnspan=2, sticky="ew", padx=3, pady=(4, 2))
        radio_row.grid_columnconfigure(0, weight=1)
        radio_row.grid_columnconfigure(1, weight=1)

        def _setup_toggle_radio(text, cache_key, default_state):
            cached_val = bool(cache.get(cache_key, default_state))
            var = ctk.StringVar(value="on" if cached_val else "")
            rb = ctk.CTkRadioButton(
                radio_row, text=text, variable=var, value="on",
                font=("Arial", 11), radiobutton_width=18, radiobutton_height=18
            )
            if not cached_val:
                rb.deselect()
            else:
                rb.select()

            def toggle(event=None):
                new_state = not (var.get() == "on")
                if new_state:
                    var.set("on")
                    rb.select()
                else:
                    var.set("")
                    rb.deselect()
                cache.set(cache_key, new_state)
                return "break"

            rb._canvas.bind("<Button-1>", toggle)
            rb._text_label.bind("<Button-1>", toggle)
            return rb, var, lambda: var.get() == "on"

        self.lens_rb, self.lens_var, self.lens_val = _setup_toggle_radio(
            "Sửa méo ống kính (Lens Correction)", "ae_lens_correction", True
        )
        self.lens_rb.grid(row=0, column=0, sticky="w", padx=(2, 6))

        self.privacy_rb, self.privacy_var, self.privacy_val = _setup_toggle_radio(
            "Che riêng tư (Auto Privacy)", "ae_privacy", False
        )
        self.privacy_rb.grid(row=0, column=1, sticky="w", padx=2)

    def get_preferences(self) -> Dict[str, Any]:
        """Tập hợp và ánh xạ toàn bộ 12 tùy chọn sang payload chuẩn API."""
        preset_choice = self.preset_var.get()
        cloud_choice = self.cloud_var.get()
        persp_choice = self.persp_var.get()
        win_choice = self.win_pull_var.get()
        tv_choice = self.tv_var.get()
        fire_choice = self.fire_var.get()
        photo_choice = self.photo_var.get()
        grass_choice = self.grass_var.get()

        preset_key = preset_choice.lower() if isinstance(preset_choice, str) else preset_choice
        cloud_key = cloud_choice.lower() if isinstance(cloud_choice, str) else cloud_choice
        persp_key = persp_choice.lower() if isinstance(persp_choice, str) else persp_choice
        win_key = win_choice.lower() if isinstance(win_choice, str) else win_choice

        # Đồng bộ lưu lại cache
        cache.set("ae_preset", preset_choice)
        cache.set("ae_enhancement_style", self.enhancement_var.get())
        cache.set("ae_cloud_type", cloud_choice)
        cache.set("ae_brackets", self.brackets_var.get())
        cache.set("ae_perspective_mode", persp_choice)
        cache.set("ae_window_pull", win_choice)
        cache.set("ae_tv_blackout", tv_choice)
        cache.set("ae_fireplace", fire_choice)
        cache.set("ae_photographer", photo_choice)
        cache.set("ae_grass", grass_choice)
        cache.set("ae_lens_correction", self.lens_val())
        cache.set("ae_privacy", self.privacy_val())

        return {
            "ai_version": "latest",
            "enhancement_style": self.enhancement_var.get(),
            "cloud_type": CLOUD_MAP.get(cloud_key),
            "number_of_brackets_per_image": self.brackets_var.get(),
            "perspective_correction_mode": PERSP_MAP.get(persp_key),
            "privacy": self.privacy_val(),
            "lens_correction": self.lens_val(),
            "window_pull_type": WIN_MAP.get(win_key, "WINDOWS_WITH_SKIES"),
            "restage.tvs": "BLACK_OUT" if tv_choice == "black out" else "AS_SHOT",
            "restage.fire_in_fireplaces": "ALIGHT" if fire_choice == "alight" else "AS_SHOT",
            "restage.photographer": "REMOVE" if photo_choice == "remove" else "AS_SHOT",
            "restage.grass": "GREEN" if grass_choice == "green" else "AS_SHOT",
            "preset_id": PRESET_MAP.get(preset_key),
        }

