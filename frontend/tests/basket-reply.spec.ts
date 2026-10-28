import {test,expect} from '@playwright/test'
import {basketReply} from '../src/basketReply'

test('accepts only complete affirmative replies',()=>{
 for(const input of ['好的，加进去','行，都加上吧','嗯','确认。','加吧！'])expect(basketReply(input),input).toBe('accept')
})

test('declines only complete negative replies',()=>{
 for(const input of ['不用了','算了，先不加'])expect(basketReply(input),input).toBe('decline')
})

test('normalizes only surrounding whitespace, terminal punctuation and supported separators',()=>{
 for(const separator of ['，',',','、',' ','， 、 ']){
  expect(basketReply(' \t好的'+separator+'加进去。！!～~\n')).toBe('accept')
  expect(basketReply(' 算了'+separator+'先不加！ ')).toBe('decline')
 }
})

test('covers every closed reply token with suffixes and same-class combinations',()=>{
 const groups={accept:'好|好的|好啊|好呀|行|可以|嗯|嗯嗯|对|是|是的|确认|没问题|就这样|就这么办|加|加吧|加上|加上吧|加进去|加进去吧|都加|都加上|都加上吧|全加|全部加上|加入|加入吧|要|要的',decline:'不用|不用了|不要|不要了|算了|先不加|不加|不加了|别加|取消'} as const
 for(const [reply,words] of Object.entries(groups))for(const word of words.split('|')){
  expect(basketReply(word),word).toBe(reply)
  expect(basketReply(word+'，'+word+'！'),word).toBe(reply)
 }
})

test('rejects mixed replies, extra instructions, names and empty input',()=>{
 for(const input of ['好的，再加个包公祠','加进去，明天下午去','不用了，换成铁塔','好的不用了','怎么走','','   ','。！','好，不用了','算了，加吧','好。加吧','好的？','好的\n加进去','好的，','，好的'])expect(basketReply(input),input).toBeNull()
 for(const name of ['包公祠','清明上河园','万岁山武侠城','铁塔','[合成测试]museum','任意新地名'])expect(basketReply('好'+name),name).toBeNull()
})
