"""Explicit synthetic LLM responses for isolated browser servers; never imported by the application."""
import contextvars


def install():
    from app import language
    from app.city.agent import provider, understand
    active=contextvars.ContextVar('planning_model_fixture',default=None)
    original_available=language.available
    original_generate=provider.generate
    original_parse=understand.parse
    def field(name,value,quote):return {'field':name,'value':value,'quote':quote}
    day=field('date','2030-05-01','2030-05-01')
    morning=field('start_time','09:00','上午9点')
    evening=field('end_time','18:00','下午6点')
    cases={
        '2030-05-01，上午9点到下午6点':{'fields':[day,morning,evening]},
        '2030-05-01，上午9点到下午6点，轻松一点':{'fields':[day,morning,evening,field('pace','relaxed','轻松一点')]},
        '2030-05-01，下午6点到上午9点':{'fields':[day,field('start_time','18:00','下午6点'),field('end_time','09:00','上午9点')]},
        '改成2030-05-02':{'fields':[field('date','2030-05-02','2030-05-02')]},
        '[合成测试]museum改为想去':{'choices':[{'name':'[合成测试]museum','priority':'want','quote':'[合成测试]museum改为想去'}]},
    }
    for text in ('我想明天去开封玩','我明天想去开封玩','明天我想去开封玩','我们打算明天去开封旅游'):
        cases[text]={'fields':[field('date','明天','明天')]}
    dialogue={
        '你能干嘛':'可以帮你整理想去的地方和想吃的东西，再一起安排行程。',
        '什么叫补齐候选要求？':'就是先补上游玩时间，再试排一份尚未保存的行程草案。',
        '你刚才说的草案是什么意思？':'草案是先给你看的安排，确认后才会保存。',
    }
    def available():return active.get() is not None or original_available()
    def generate(system,payload,**kwargs):
        if active.get() is not None:return active.get(),{'source':'mock_model','http_attempts':0}
        return original_generate(system,payload,**kwargs)
    def parse(text,snapshot,**kwargs):
        if text not in cases and text not in dialogue:return original_parse(text,snapshot,**kwargs)
        response=({'conversation':{'reply':dialogue[text]}} if text in dialogue else
                  {'actions':[{'kind':'modify','quote':text}], 'planning':{'quote':text,**cases[text]}})
        token=active.set(response)
        try:return original_parse(text,snapshot,**kwargs)
        finally:active.reset(token)
    language.available=available
    provider.generate=generate
    understand.parse=parse
