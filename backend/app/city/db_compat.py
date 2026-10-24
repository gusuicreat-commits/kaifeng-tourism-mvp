"""Read-only checks from the scenic adapter into the existing city fact catalog."""
from fastapi import HTTPException

from . import facts
from ..db import connect


def verified_poi(poi_id: str, park_id: str, require_routing: bool):
    with connect() as db:
        records = facts.records(db)
        poi = records.get(("place", poi_id))
        if not poi or poi.place_type != "stage" or poi.validation_status != "verified" or poi.is_demo:
            raise HTTPException(422, detail="场地必须是既有城市事实中的已核验舞台，不能用文字或坐标代建。")
        parent = poi.parent_place_id
        if park_id != parent or not parent or ("place", parent) not in records:
            raise HTTPException(422, detail="场地不属于目标景区，拒绝映射。")
        if require_routing:
            usable = False
            from .engine.storage import load_operations
            for operation in load_operations(db).journeys:
                if (operation.travel.mode == "walking" and operation.travel.path_status == "verified" and
                    operation.travel.evidence.usable_at(__import__('datetime').datetime.now(__import__('zoneinfo').ZoneInfo("Asia/Shanghai"))) and
                    poi_id in (operation.travel.origin_place_id, operation.travel.destination_place_id)):
                    usable = True
                    break
            if not usable:
                raise HTTPException(422, detail="场地尚未连接既有有效、已核验的有向步行运营数据。")
    return poi

