"""Conservative request-evidence checks; substrings alone never authorize a mutation.

This is a bounded safety gate, not a general Chinese semantic parser. Ambiguous
negation/quotation is clarified; positive corrections remain independently usable.
"""
import re

_CLAUSE = re.compile(r'[^，,。；;！!？?\n]+')
_DATA = re.compile(r'网页原文|商家原文|日志原文|以下是.{0,10}(?:原文|内容|引用)|只是(?:引用|举例|示例)|不是我的要求|仅供参考|例如|举个例子')
_QUESTION = re.compile(r'如果|假如|假设|请解释|什么意思|怎么样|怎么(?:修改|改|恢复|调整|设置|取消)|如何(?:修改|改|恢复|调整|设置|取消)|是否(?:要|可以|能)|能不能|是几号|是什么(?:日期|时候|时间)|星期几|吗$')
_DENY = re.compile(r'(?:不要|不用|不必|别|禁止|不想|不是要|并非要).{0,12}(?:修改|改成|改为|改动|调整|更新|保存|确认|安排|规划|取消|恢复|设置|设为)')
_QUOTED = re.compile(r'“[^”]*”|「[^」]*」|『[^』]*』|【[^】]*】|"[^"\n]*"|```[\s\S]*?```')


def request_evidence(text, quote):
    if not quote or quote not in text:raise ValueError('current request evidence missing')
    # Explicitly labelled external/example text does not become a command. A new
    # affirmative request after such data needs its own unquoted evidence.
    ranges=[m.span() for m in _QUOTED.finditer(text)]
    occurrences=[m.span() for m in re.finditer(re.escape(quote),text)]
    labelled=bool(_DATA.search(text))
    for begin,end in occurrences:
        clause=next((m.group() for m in _CLAUSE.finditer(text) if m.start()<=begin and end<=m.end()),quote)
        if labelled and (any(a<=begin and end<=b for a,b in ranges) or _DATA.search(clause)):
            continue
        if _QUESTION.search(clause) or _DENY.search(clause):continue
        # "do not modify" governs the whole utterance unless there is a later
        # explicit, positive correction rather than merely an evidence substring.
        denials=list(_DENY.finditer(text))
        if denials:
            later=text[denials[-1].end():]
            positive=re.search(r'(?:但|不过|现在|还是|请|改成|改为).{0,12}(?:改成|改为|调整|设置|设为|安排|恢复)',later)
            if not positive:continue
        if labelled and not re.search(r'(?:现在|请|帮我|改成|改为|设为|出发|返回)',clause):continue
        return
    raise ValueError('negated, hypothetical or quoted request evidence')


# Read requests do not need mutation wording. Their evidence must still be a
# current, unquoted request rather than a denial, example or hypothetical.
_READ_DENY = re.compile(r'(?:不要|不用|不必|别|禁止|不想|不是要|并非要|暂不|无需)[^，,。；;！？!?\n]{0,10}(?:查|搜|找|推荐|检索|调用)')
_READ_META = re.compile(r'如果|假如|假设|请解释|什么意思|怎么(?:查询|搜索|检索|调用)|如何(?:查询|搜索|检索|调用)')
_READ_REQUEST = re.compile(r'(?:请|帮我|现在|实际)[^，,。；;！？!?\n]{0,12}(?:查|搜|找|推荐|检索)')
_STAY_DENY = re.compile(r'(?:不要|不用|不必|别|不想|不是要|并非要)[^，,。；;！？!?\n]{0,12}(?:入住|住|退房)|不(?:入住|住|退房)')


def query_evidence(text, quote):
    if not quote or quote not in text:raise ValueError('current query evidence missing')
    ranges=[m.span() for m in _QUOTED.finditer(text)]
    accepted=[]
    for match in re.finditer(re.escape(quote),text):
        begin,end=match.span()
        clause=next((m.group() for m in _CLAUSE.finditer(text) if m.start()<=begin and end<=m.end()),quote)
        sentence=next((m.group() for m in re.finditer(r'[^。；;！!？?\n]+',text) if m.start()<=begin and end<=m.end()),quote)
        if any(a<=begin and end<=b for a,b in ranges):continue
        if _DATA.search(clause) or _READ_META.search(clause) or _READ_DENY.search(clause):continue
        if _READ_META.search(sentence):continue
        earlier=[m for m in _READ_DENY.finditer(text) if m.end()<=begin]
        if earlier and not _READ_REQUEST.search(text[earlier[-1].end():end]):continue
        if re.search(r'(?:算了|取消查询)[^。；;！？!?\n]*'+_READ_DENY.pattern,text[end:]):continue
        if _DATA.search(text) and not _READ_REQUEST.search(clause):continue
        accepted.append((begin,end))
    if not accepted:raise ValueError('negated, hypothetical or quoted query evidence')
    return accepted


def service_condition_evidence(text, quote, request_quote, *, literal_name=False):
    parents=query_evidence(text,request_quote)
    if not quote or quote not in request_quote:raise ValueError('service field outside current query')
    ranges=[m.span() for m in _QUOTED.finditer(text)]
    for match in re.finditer(re.escape(quote),text):
        begin,end=match.span()
        if not any(a<=begin and end<=b for a,b in parents):continue
        clause=next((m.group() for m in _CLAUSE.finditer(text) if m.start()<=begin and end<=m.end()),quote)
        if _DENY.search(clause) or _STAY_DENY.search(clause):continue
        # A quoted hotel/room name may be a literal search parameter. A quoted
        # instruction or a date copied from external text is not a new condition.
        if any(a<=begin and end<=b for a,b in ranges) and not literal_name:continue
        return
    raise ValueError('quoted or unrelated service field evidence')


def effective_clock(text, quote, clock):
    """Only inherit a day period immediately before this quote in its own clause."""
    if re.search(r'早上|上午|下午|晚上|中午|凌晨',quote):return clock(quote)
    start=text.find(quote)
    prefix=re.split(r'[，,。；;！!？?\n]',text[:start])[-1]
    periods=re.findall(r'早上|上午|下午|晚上|中午|凌晨',prefix)
    return clock(quote,afternoon=bool(periods and periods[-1] in ('下午','晚上')))


def checked_reply(reply):
    """A read-only conversation turn has no receipt for a current-turn mutation.

Common completion claims are gated; this is not a proof of arbitrary free-text
truth, and tool-backed factual questions must still use deterministic queries.
"""
    clauses=re.split(r'[。！？!?；;\n]',reply)
    for clause in clauses:
        if re.search(r'(?:我(?:正在|正|会|能)|系统(?:正在|会)|已开启).{0,8}(?:持续|实时|自动)(?:跟踪|定位)',clause):
            return '我不会持续定位你；只能使用你主动提供或确认的位置，不能将聊天当作实时监测。'
        if re.search(r'(?:当前|现在|此时).{0,12}(?:不拥挤|很空|畅通|无需排队|不用排队)|(?<!不能)(?<!无法)(?<!不)保证.{0,15}(?:赶上|到达)',clause):
            return '没有当前客流或可靠到场依据，不能判断畅通、无需排队或保证赶上演出；需要核对实际资料。'
        # '已保存的行程可以查看' describes a feature, not a current-turn success.
        claim=re.sub(r'已(?:经)?(?:保存|确认)(?:的|过的)(?:行程|安排|方案|路线|记录)','',clause)
        if not re.search(r'已经|已(?:为|帮|经|替|保存|确认|取消|更新|修改|调整|完成|生成|安排|规划)|保存(?:好|成功|了)|修改成功',claim):continue
        if re.search(r'保存|确认|取消|更新|修改|调整|改成|改为|生成|安排|规划',claim):
            return '本轮没有执行保存、取消或确认操作。需要修改时，我会先展示可核对的草案，再由你确认。'
    return reply


PREFERENCE_CUES={
    'pace':{'relaxed':r'轻松|宽松|慢点|慢一点|慢一些|放慢|悠闲|不赶|别太累','compact':r'紧凑|多安排|多逛|充实|快一点|加快','standard':r'标准|正常|普通节奏'},
    'objective':{'less_walking':r'少走|走路少|步行少|近点|近一点|别走太远|不想走太多',
        'less_waiting':r'少等|少排队|减少等待|不想等','lower_cost':r'省钱|便宜|少花|低费用|预算更低','balanced':r'均衡|平衡|综合'},
    'mode':{'walking':r'步行|走路','driving':r'开车|驾车|自驾','transit':r'公交|公共交通|巴士|地铁'},
}


def preference_evidence(field,value,quote):
    pattern=PREFERENCE_CUES[field].get(value,r'(?!)')
    if not re.search(pattern,quote):raise ValueError('invented preference or mode')
    if re.search(r'(?:不要|不想|不是要|并非要|不用|不坐|不乘).{0,3}(?:'+pattern+r')',quote):
        raise ValueError('negated preference or mode')
