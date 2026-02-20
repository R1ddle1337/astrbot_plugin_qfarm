from __future__ import annotations

from typing import Any

from .config_data import GameConfigData


def _parse_grow_time(grow_phases: str | None) -> int:
    if not grow_phases:
        return 0
    total = 0
    for seg in str(grow_phases).split(";"):
        seg = seg.strip()
        if not seg or ":" not in seg:
            continue
        try:
            total += int(seg.rsplit(":", 1)[1])
        except Exception:
            continue
    return total


def _parse_normal_fertilizer_reduce_sec(grow_phases: str | None) -> int:
    if not grow_phases:
        return 0
    first = str(grow_phases).split(";", 1)[0]
    if ":" not in first:
        return 0
    try:
        return int(first.rsplit(":", 1)[1])
    except Exception:
        return 0


def _format_seconds(seconds: int) -> str:
    sec = max(0, int(seconds))
    if sec < 60:
        return f"{sec}秒"
    if sec < 3600:
        return f"{sec // 60}分{sec % 60}秒"
    h = sec // 3600
    m = (sec % 3600) // 60
    return f"{h}时{m}分" if m else f"{h}时"


class AnalyticsService:
    def __init__(self, config: GameConfigData) -> None:
        self.config = config

    def get_plant_rankings(self, sort_by: str = "exp") -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for plant in self.config.plants:
            plant_id = int(plant.get("id") or 0)
            seed_id = int(plant.get("seed_id") or 0)
            if plant_id <= 0 or seed_id <= 0:
                continue
            if not str(plant_id).startswith("102"):
                continue
            if not (20000 <= seed_id < 30000):
                continue

            base_grow = _parse_grow_time(plant.get("grow_phases"))
            if base_grow <= 0:
                continue
            seasons = int(plant.get("seasons") or 1)
            is_two = seasons == 2
            grow_time = int(base_grow * 1.5) if is_two else base_grow

            base_exp = int(plant.get("exp") or 0)
            harvest_exp = base_exp * 2 if is_two else base_exp
            exp_per_hour = (harvest_exp / max(1, grow_time)) * 3600

            reduce_base = _parse_normal_fertilizer_reduce_sec(plant.get("grow_phases"))
            reduce_applied = reduce_base * 2 if is_two else reduce_base
            fert_time = max(1, grow_time - reduce_applied)
            fert_exp_per_hour = (harvest_exp / fert_time) * 3600

            fruit = plant.get("fruit") if isinstance(plant.get("fruit"), dict) else {}
            fruit_id = int(fruit.get("id") or 0)
            fruit_count = int(fruit.get("count") or 0)
            fruit_price = self.config.get_fruit_price(fruit_id)
            seed_price = self.config.get_seed_price(seed_id)
            income = fruit_count * fruit_price * (2 if is_two else 1)
            net_profit = income - seed_price
            gold_per_hour = (income / max(1, grow_time)) * 3600
            profit_per_hour = (net_profit / max(1, grow_time)) * 3600
            fert_profit_per_hour = (net_profit / fert_time) * 3600

            rows.append(
                {
                    "id": plant_id,
                    "seedId": seed_id,
                    "name": str(plant.get("name") or f"作物{seed_id}"),
                    "seasons": seasons,
                    "level": int(plant.get("land_level_need") or 0),
                    "growTime": grow_time,
                    "growTimeStr": _format_seconds(grow_time),
                    "reduceSec": reduce_base,
                    "reduceSecApplied": reduce_applied,
                    "expPerHour": round(exp_per_hour, 2),
                    "normalFertilizerExpPerHour": round(fert_exp_per_hour, 2),
                    "goldPerHour": round(gold_per_hour, 2),
                    "profitPerHour": round(profit_per_hour, 2),
                    "normalFertilizerProfitPerHour": round(fert_profit_per_hour, 2),
                    "income": income,
                    "netProfit": net_profit,
                    "fruitId": fruit_id,
                    "fruitCount": fruit_count,
                    "fruitPrice": fruit_price,
                    "seedPrice": seed_price,
                }
            )

        sort_key = {
            "exp": "expPerHour",
            "fert": "normalFertilizerExpPerHour",
            "gold": "goldPerHour",
            "profit": "profitPerHour",
            "fert_profit": "normalFertilizerProfitPerHour",
            "level": "level",
        }.get(sort_by, "expPerHour")
        rows.sort(key=lambda x: float(x.get(sort_key, 0)), reverse=True)
        return rows
