"""Idempotent seed: never overwrite imported observations or existing scenic records."""
import json
from datetime import datetime, timezone
from .db import connect, init_db

WSS = 'https://www.dasongwuxiacheng.com/website/pc/index.html'
QMS = 'https://www.qmsyun.com/Mobile_page/o_program.php'
GOV = 'https://www.kaifeng.gov.cn/kfsrmzfwz/zwyw/pc/content/content_1906869173468610560.html'
FOOD = 'https://www.kaifeng.gov.cn/kfsrmzfwz/tzgg/1768115858696634368/EWVE1D0L.pdf'
CHECKED = '2026-10-19'


def make_network(sid, names):
    specs = [
        ('gate', '示意入口', 80, 420, [], 0),
        ('plaza', names[0], 250, 320, ['culture', 'photo'], 15),
        ('theatre', names[1], 420, 170, ['show'], 35),
        ('photo', names[2], 620, 130, ['photo'], 20),
        ('culture', names[3], 610, 320, ['culture'], 30),
        ('food', '演示·小吃休憩点', 420, 460, ['food'], 25),
        ('garden', '演示·林荫休憩点', 230, 130, ['photo', 'culture'], 15),
        ('exit', '示意出口', 750, 430, [], 0),
    ]
    features = []
    for id_, name, x, y, interests, stay in specs:
        features.append({'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [x, y]}, 'properties': {
            'id': id_, 'name': name, 'interests': interests, 'stay_minutes': stay,
            'kind': 'entrance' if id_ in ('gate', 'exit') else 'poi',
            'is_simulated': True, 'position_status': 'schematic',
            'description': '用于路径算法验证的示意位置；坐标、停留时间均为演示参数。',
            'name_source_url': QMS if sid == 'qingming' and id_ in ('plaza','theatre','photo','culture') else None,
        }})
    edges = [('gate','plaza',200),('gate','garden',330),('plaza','theatre',230),('plaza','culture',370),
             ('plaza','food',240),('garden','theatre',210),('theatre','photo',220),('photo','culture',210),
             ('culture','exit',200),('food','exit',360),('food','culture',250),('garden','photo',450)]
    positions = {s[0]: [s[2], s[3]] for s in specs}
    for a,b,m in edges:
        features.append({'type': 'Feature', 'geometry': {'type': 'LineString', 'coordinates': [positions[a],positions[b]]},
                         'properties': {'id': f'{a}-{b}', 'from': a, 'to': b, 'distance_m': m, 'is_simulated': True}})
    return {'type': 'FeatureCollection', 'scenic_id': sid, 'is_simulated': True,
            'coordinate_space': 'local_schematic_units_not_geographic',
            'notice': '演示示意路网 · 非地理坐标，非真实道路，不可用于现场导航；距离和耗时均为演示估算。', 'features': features}


def seed():
    init_db()
    items = [
        dict(id='wansui',name='万岁山武侠城',subtitle='入一座城，赴一场江湖',category='武侠演艺',theme='wuxia',
             intro='开封的武侠主题景区。本原型先验证游览偏好、示意路线和客流数据接入，现场项目与开放信息请查看景区官方公告。',
             address='河南省开封市（请在高德地图核对入口）', source_url=WSS, internal_map=True,
             highlights=['武侠主题','互动游园','演示路网'],
             shows=[{'name':'武侠演艺项目','venue':'请查景区官方公告','schedule':'未接入当日节目单','source_url':WSS}]),
        dict(id='qingming',name='清明上河园',subtitle='走进画卷，遇见大宋',category='宋韵文化',theme='song',
             intro='以宋文化体验为特色的主题景区。官方节目页列有虹桥、上善门与景龙湖等区域；本原型仅引用名称，位置与道路仍为演示。',
             address='河南省开封市（请在高德地图核对入口）',source_url=QMS,internal_map=True,
             highlights=['宋韵游园','实景演艺','虹桥'],
             shows=[{'name':'大宋·东京梦华','venue':'景龙湖水面','schedule':'以景区当天公示为准；本系统不安排演出场次','source_url':QMS},
                    {'name':'杨志卖刀','venue':'虹桥广场','schedule':'以景区当天公示为准','source_url':QMS}]),
        dict(id='longting',name='龙亭景区',subtitle='漫步古都，寻一段文脉',category='历史人文',theme='palace',
             intro='开封市政府旅游报道中介绍的游园景区。本阶段提供基础信息与外部地图入口，内部点位和道路待核实。',
             address='河南省开封市',source_url=GOV,internal_map=False,highlights=['文化游园','基础信息'],shows=[]),
        dict(id='tieta',name='铁塔景区',subtitle='古塔与花木，慢游好时光',category='古迹园林',theme='tower',
             intro='开封市政府旅游报道中介绍的景区，报道提及铁塔公园春日赏花。本阶段暂未接入内部路网与演出数据。',
             address='河南省开封市',source_url=GOV,internal_map=False,highlights=['古迹','园林'],shows=[]),
        dict(id='kaifengfu',name='开封府',subtitle='寻访汴梁，感受府衙文化',category='历史人文',theme='court',
             intro='开封市政府旅游报道中介绍的景区。本阶段提供基础资料，开放时间、票价与当日活动请以景区公告为准。',
             address='河南省开封市',source_url=GOV,internal_map=False,highlights=['人文','基础信息'],shows=[]),
    ]
    for s in items:
        s.update(info_status='public_source',checked_at=CHECKED,location=None,location_status='awaiting_geocoding',
                 foods=[{'name':'开封灌汤包','description':'开封地方特色；未核实景区内商户、价格和售卖位置。','location':'景区外 · 开封市区（具体商户待核实）','source_url':FOOD,'is_simulated':False},
                        {'name':'桶子鸡','description':'地方传统美食；不代表景区内有售。','location':'景区外 · 开封市区（具体商户待核实）','source_url':FOOD,'is_simulated':False}],
                 guides=[{'title':'先看公告，再安排游览','body':'出发前核对景区开放与演出公告，优先确定必去点，给休息和临时调整留出时间。','kind':'editorial','url':s['source_url']},
                         {'title':'查找视频攻略','body':'外部搜索结果由平台提供，非官方攻略背书。','kind':'video_search','url':'https://search.bilibili.com/all?keyword='+s['name']}])
    with connect() as db:
        for s in items:
            db.execute('INSERT OR IGNORE INTO scenic VALUES (?,?)', (s['id'],json.dumps(s,ensure_ascii=False)))
        for sid,names in [('wansui',['演示·江湖广场','演示·武侠剧场','演示·古风照相点','演示·民俗体验区']),
                          ('qingming',['虹桥（示意位置）','景龙湖演出区（示意位置）','上善门（示意位置）','勾栏瓦肆（示意位置）'])]:
            net=make_network(sid,names)
            db.execute('INSERT OR IGNORE INTO networks VALUES (?,?)',(sid,json.dumps(net,ensure_ascii=False)))
            for day in range(1,8):
                for hour in [9,11,13,15,17,19]:
                    for i,node in enumerate(['plaza','theatre','photo','culture','food','garden']):
                        level=round(min(.95,max(.12, [.62,.8,.4,.35,.55,.2][i]+(.08 if hour in (13,15) else -.08)+(day%3-1)*.03)),2)
                        db.execute('''INSERT OR IGNORE INTO observations
                        (scenic_id,node_id,observed_at,crowd_level,source_type,source_name,measurement_kind,updated_at,confidence,is_simulated)
                        VALUES (?,?,?,?,?,?,?,?,?,1)''',
                        (sid,node,f'2026-10-{day:02d}T{hour:02d}:00:00+08:00',level,'field','MVP固定模拟样本','observation','2026-10-08T00:00:00+08:00',.65))


if __name__=='__main__':
    seed()
    print('Database initialized (idempotent). All seeded crowd records are simulated.')
