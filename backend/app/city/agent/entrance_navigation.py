"""Catalog entrances with temporary AMap proxy positions, for reference routes only."""
import math
import re
import time

from ..facts import Entrance, Place


EXCLUDED = ('停车','酒店','宾馆','餐','店','厕','公交','站')
# AMap appends the business status to a POI name; a closed ticket office is no entrance proxy.
CLOSED = ('暂停营业','停业','已关闭','关闭')
CACHE = {}
TTL = 6*60*60


def scenic_for(name, facts):
    for place in facts.values():
        if not isinstance(place,Place) or place.place_type!='scenic' or name not in (place.name,*place.aliases):continue
        seen=set()
        while place and place.superseded_by and place.fact_id not in seen:
            seen.add(place.fact_id);place=facts.get(('place',place.superseded_by))
        if isinstance(place,Place) and place.place_type=='scenic' and not place.superseded_by:return place
    return None


def entrances(scenic, facts):
    result=[];seen=set()
    for relation in facts.values():
        if not isinstance(relation,Entrance) or relation.place_id!=scenic.fact_id or relation.direction not in ('entry','both'):continue
        place=facts.get(('place',relation.entrance_place_id))
        if not place or place.name in seen:continue
        seen.add(place.name)
        located=place.coordinate_system=='GCJ02' and place.longitude is not None and place.latitude is not None
        result.append({'entrance_id':relation.fact_id,'name':place.name,
                       'longitude':place.longitude if located else None,'latitude':place.latitude if located else None,
                       'basis':'catalog' if located else None,'poi_name':None})
    return result


def distance(a, b):
    lon1,lat1,lon2,lat2=map(math.radians,(*a,*b))
    h=math.sin((lat2-lat1)/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371000*2*math.asin(math.sqrt(min(1,max(0,h))))


def matches(scenic, entrance_name, candidate_name):
    if not isinstance(candidate_name,str) or any(word in candidate_name for word in (*EXCLUDED,*CLOSED)):return False
    names=(scenic.name,*(a for a in scenic.aliases if len(a)>=2))
    core=re.sub(r'（[^）]*）|\([^)]*\)','',entrance_name).strip()
    return bool(core) and any(n in candidate_name for n in names) and (core in candidate_name or core.replace('大门','门') in candidate_name)


def locate_entrances(scenic, items, provider, around):
    # Scope the cache to catalog content and the selected scenic POI, never a visitor's origin.
    key=(scenic.fact_id,scenic.name,scenic.aliases,tuple(tuple(i.items()) for i in items),around.longitude,around.latitude)
    now=time.monotonic()
    for expired in [k for k,(at,_) in CACHE.items() if now-at>=TTL]:CACHE.pop(expired,None)
    if key in CACHE:return [dict(i) for i in CACHE[key][1]]
    located=[dict(i) for i in items];pool=[]
    for query in (scenic.name,scenic.name+'门'):
        if all(i['longitude'] is not None for i in located):break
        try:
            result=provider.search_pois(query,version='v5')
            for poi in result.pois:
                if poi.coordinate_system!='GCJ02':continue
                pool.append((poi.name,poi.longitude,poi.latitude))
            for poi in result.pois:
                if scenic.name not in poi.name:continue
                for child in poi.children:
                    try:
                        lon,lat=map(float,child['location'].split(','))
                        pool.append((child['name'],lon,lat))
                    except (KeyError,ValueError,TypeError,AttributeError):continue
        except Exception:
            # Entrance lookup must never prevent routing to the selected scenic POI.
            pass
        finally:
            # Leave room for the next lookup and the route request under the provider QPS gate.
            getattr(provider,'sleeper',time.sleep)(getattr(provider,'interval',0))
        for item in located:
            if item['longitude'] is not None:continue
            for name,lon,lat in pool:
                if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in (lon,lat)) or not (-180<=lon<=180 and -90<=lat<=90):continue
                if matches(scenic,item['name'],name) and distance((lon,lat),(around.longitude,around.latitude))<=2000:
                    item.update(longitude=lon,latitude=lat,basis='amap_poi_proxy',poi_name=name);break
    CACHE[key]=(now,located)
    return [dict(i) for i in located]


def nearest(origin, located):
    choices=[(item,distance((origin.longitude,origin.latitude),(item['longitude'],item['latitude'])))
             for item in located if item['longitude'] is not None and item['latitude'] is not None]
    return min(choices,key=lambda value:value[1]) if choices else (None,None)
