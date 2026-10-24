"""Temporary DB for browser acceptance; synthetic by default, explicit --live for providers."""
import os
from pathlib import Path
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
os.environ['TOURISM_LLM_API_KEY']=''
os.environ['TOURISM_FRONTEND_DIST']=str(ROOT/'frontend/dist')
with tempfile.TemporaryDirectory(prefix='travel-services-') as folder:
    os.environ['DATABASE_PATH']=str(Path(folder)/'tourism.sqlite3')
    os.environ['M7_DELETION_LEDGER_PATH']=str(Path(folder)/'deletions.sqlite3')
    from app.main import app
    if '--live' not in sys.argv:
        from app import language
        from app.city.agent import provider
        from app.city import hotels
        # Synthetic semantic adapter: exercise the model entry and routing in
        # browser tests, without credentials, live requests or the offline path.
        language.available=lambda:True
        def semantic(system, payload, **kwargs):
            text=payload['text']
            if text=='你能查阅相关酒店资料吗':
                return {'conversation':{'reply':'可以查询酒店名称、地址和展示起价，房型和评价需要另行核实。'}},{'source':'mock_model'}
            if text=='我要标间，离清明上河园近的，住两个晚上，有没有早餐都行，住两个晚上，预算两天500':
                fields=[{'field':'room_type','value':'标间','quote':'我要标间'},
                        {'field':'location_text','value':'清明上河园','quote':'离清明上河园近的'},
                        {'field':'nights','value':2,'quote':'住两个晚上'},
                        {'field':'breakfast','value':True,'strength':'optional','quote':'有没有早餐都行'},
                        {'field':'stay_budget_fen','value':50000,'quote':'预算两天500'}]
                return {'service_query':{'quote':text,'fields':fields}},{'source':'mock_model'}
            if text=='明天入住':
                return {'service_query':{'quote':text,'fields':[{'field':'check_in','value':'明天','quote':text}]}},{'source':'mock_model'}
            return {'actions':[{'kind':'search_services','quote':text}]},{'source':'mock_model'}
        provider.generate=semantic
        def search(q):
            return hotels.normalize({'status':0,'data':{'itemList':[
                {'shId':'fixture-a','name':'[合成测试]候选甲','price':'¥188','address':'合成地址甲'},
                {'shId':'fixture-b','name':'[合成测试]候选乙','price':'¥148','address':'合成地址乙'}]}})
        hotels.search=search
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=int(os.getenv('TRAVEL_TEST_PORT','8027')),log_level='warning')
