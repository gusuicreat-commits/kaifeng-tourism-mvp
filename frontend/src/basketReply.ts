const accept=new Set('好|好的|好啊|好呀|行|可以|嗯|嗯嗯|对|是|是的|确认|没问题|就这样|就这么办|加|加吧|加上|加上吧|加进去|加进去吧|都加|都加上|都加上吧|全加|全部加上|加入|加入吧|要|要的'.split('|'))
const decline=new Set('不用|不用了|不要|不要了|算了|先不加|不加|不加了|别加|取消'.split('|'))

export function basketReply(input:string):'accept'|'decline'|null{
 const parts=input.trim().replace(/[。！!～~]+$/u,'').split(/[，,、 ]+/u)
 if(parts.every(part=>accept.has(part)))return 'accept'
 if(parts.every(part=>decline.has(part)))return 'decline'
 return null
}
