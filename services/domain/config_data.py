from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


@dataclass(slots=True)
class SeedInfo:
    seed_id: int
    name: str
    required_level: int
    price: int
    image: str


class GameConfigData:
    def __init__(self, plugin_root: Path) -> None:
        self.plugin_root = Path(plugin_root)
        self.docs_root = self._resolve_docs_root(self.plugin_root)
        self.config_dir = self.docs_root / "gameConfig"
        self.seed_image_dir = self.config_dir / "seed_images_named"

        self.role_level: list[dict[str, Any]] = []
        self.level_exp_table: dict[int, int] = {}

        self.plants: list[dict[str, Any]] = []
        self.plant_by_id: dict[int, dict[str, Any]] = {}
        self.plant_by_seed: dict[int, dict[str, Any]] = {}
        self.plant_by_fruit: dict[int, dict[str, Any]] = {}

        self.item_info: list[dict[str, Any]] = []
        self.item_by_id: dict[int, dict[str, Any]] = {}
        self.seed_item_by_id: dict[int, dict[str, Any]] = {}

        self.seed_image_by_id: dict[int, str] = {}
        self.seed_image_by_asset: dict[str, str] = {}

        self.reload()

    @staticmethod
    def _resolve_docs_root(plugin_root: Path) -> Path:
        preferred = plugin_root / "qqfarm文档"
        if preferred.exists():
            return preferred

        # 兼容历史乱码目录名或平台解压差异，只要目录下含 gameConfig 即可。
        try:
            children = list(plugin_root.iterdir())
        except Exception:
            children = []

        for child in children:
            if not child.is_dir():
                continue
            name = child.name.lower()
            if not name.startswith("qqfarm"):
                continue
            if (child / "gameConfig").exists():
                return child

        # 回退到标准目录，后续读取 JSON 时会自动降级为空配置。
        return preferred

    def reload(self) -> None:
        self._load_role_level()
        self._load_plants()
        self._load_items()
        self._load_seed_images()

    def get_level_exp_progress(self, level: int, total_exp: int) -> dict[str, int]:
        current_start = self.level_exp_table.get(level, 0)
        next_start = self.level_exp_table.get(level + 1, current_start + 100000)
        current = max(0, int(total_exp) - int(current_start))
        needed = max(1, int(next_start) - int(current_start))
        return {"current": current, "needed": needed, "level": int(level)}

    def get_seed_unlock_level(self, seed_id: int) -> int:
        item = self.seed_item_by_id.get(int(seed_id))
        return _to_int(item.get("level"), 1) if item else 1

    def get_seed_price(self, seed_id: int) -> int:
        item = self.seed_item_by_id.get(int(seed_id))
        return _to_int(item.get("price"), 0) if item else 0

    def get_fruit_price(self, fruit_id: int) -> int:
        item = self.item_by_id.get(int(fruit_id))
        return _to_int(item.get("price"), 0) if item else 0

    def get_item_by_id(self, item_id: int) -> dict[str, Any] | None:
        return self.item_by_id.get(int(item_id))

    def get_fruit_name(self, fruit_id: int) -> str:
        plant = self.plant_by_fruit.get(int(fruit_id))
        if plant:
            return str(plant.get("name") or f"果实{fruit_id}")
        item = self.item_by_id.get(int(fruit_id))
        if item:
            return str(item.get("name") or f"果实{fruit_id}")
        return f"果实{fruit_id}"

    def get_plant_by_fruit(self, fruit_id: int) -> dict[str, Any] | None:
        return self.plant_by_fruit.get(int(fruit_id))

    def get_plant_exp(self, plant_id: int) -> int:
        plant = self.plant_by_id.get(int(plant_id))
        return _to_int(plant.get("exp"), 0) if plant else 0

    def get_plant_grow_time_sec(self, plant_id: int) -> int:
        plant = self.plant_by_id.get(int(plant_id))
        if not plant:
            return 0
        raw = str(plant.get("grow_phases") or "")
        total = 0
        for seg in raw.split(";"):
            part = seg.strip()
            if not part or ":" not in part:
                continue
            total += _to_int(part.rsplit(":", 1)[1], 0)
        return max(0, total)

    @staticmethod
    def format_grow_time(seconds: int) -> str:
        sec = max(0, int(seconds))
        if sec < 60:
            return f"{sec}秒"
        if sec < 3600:
            return f"{sec // 60}分钟"
        hours = sec // 3600
        mins = (sec % 3600) // 60
        if mins > 0:
            return f"{hours}小时{mins}分钟"
        return f"{hours}小时"

    def get_seed_image(self, seed_id: int) -> str:
        return self.seed_image_by_id.get(int(seed_id), "")

    def get_plant_by_seed(self, seed_id: int) -> dict[str, Any] | None:
        return self.plant_by_seed.get(int(seed_id))

    def get_plant_name_by_seed(self, seed_id: int) -> str:
        plant = self.get_plant_by_seed(seed_id)
        return str(plant.get("name")) if plant else f"种子{seed_id}"

    def get_plant_name(self, plant_id: int) -> str:
        plant = self.plant_by_id.get(int(plant_id))
        return str(plant.get("name")) if plant else f"植物{plant_id}"

    def get_all_seeds(self, current_level: int) -> list[SeedInfo]:
        _ = current_level
        rows: list[SeedInfo] = []
        for plant in self.plants:
            seed_id = _to_int(plant.get("seed_id"), 0)
            if seed_id <= 0:
                continue
            required_level = _to_int(plant.get("land_level_need"), 0)
            rows.append(
                SeedInfo(
                    seed_id=seed_id,
                    name=str(plant.get("name") or f"种子{seed_id}"),
                    required_level=required_level,
                    price=self.get_seed_price(seed_id),
                    image=self.get_seed_image(seed_id),
                )
            )
        rows.sort(key=lambda x: (x.required_level, x.seed_id))
        return rows

    def _load_role_level(self) -> None:
        path = self.config_dir / "RoleLevel.json"
        self.role_level = self._read_json(path, [])
        self.level_exp_table = {}
        for row in self.role_level:
            level = _to_int(row.get("level"), 0)
            exp = _to_int(row.get("exp"), 0)
            if level > 0:
                self.level_exp_table[level] = exp

    def _load_plants(self) -> None:
        path = self.config_dir / "Plant.json"
        self.plants = self._read_json(path, [])
        self.plant_by_id = {}
        self.plant_by_seed = {}
        self.plant_by_fruit = {}
        for plant in self.plants:
            plant_id = _to_int(plant.get("id"), 0)
            if plant_id > 0:
                self.plant_by_id[plant_id] = plant
            seed_id = _to_int(plant.get("seed_id"), 0)
            if seed_id > 0:
                self.plant_by_seed[seed_id] = plant
            fruit = plant.get("fruit") if isinstance(plant.get("fruit"), dict) else {}
            fruit_id = _to_int(fruit.get("id"), 0)
            if fruit_id > 0:
                self.plant_by_fruit[fruit_id] = plant

    def _load_items(self) -> None:
        path = self.config_dir / "ItemInfo.json"
        self.item_info = self._read_json(path, [])
        self.item_by_id = {}
        self.seed_item_by_id = {}
        for row in self.item_info:
            item_id = _to_int(row.get("id"), 0)
            if item_id <= 0:
                continue
            self.item_by_id[item_id] = row
            if _to_int(row.get("type"), 0) == 5:
                self.seed_item_by_id[item_id] = row

    def _load_seed_images(self) -> None:
        self.seed_image_by_id = {}
        self.seed_image_by_asset = {}
        if not self.seed_image_dir.exists():
            return
        for path in self.seed_image_dir.iterdir():
            if not path.is_file():
                continue
            name = path.name
            url = f"/game-config/seed_images_named/{name}"
            parts = name.split("_", 1)
            if parts and parts[0].isdigit():
                seed_id = int(parts[0])
                self.seed_image_by_id.setdefault(seed_id, url)
            if "Crop_" in name and "_Seed" in name:
                start = name.find("Crop_")
                end = name.find("_Seed", start)
                if start >= 0 and end > start:
                    asset = name[start:end]
                    self.seed_image_by_asset.setdefault(asset, url)

    @staticmethod
    def _read_json(path: Path, default: Any) -> Any:
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
