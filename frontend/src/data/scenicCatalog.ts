import type {Scenic} from '../types'

// Basic display metadata from backend/app/seed.py. Live crowds and schedules are
// deliberately excluded: an unavailable API must never become invented live data.
export type ScenicSummary=Pick<Scenic,'id'|'name'|'subtitle'|'category'|'theme'|'internal_map'|'highlights'>
export const scenicCatalog:ScenicSummary[]=[
 {id:'wansui',name:'万岁山武侠城',subtitle:'入一座城，赴一场江湖',category:'武侠演艺',theme:'wuxia',internal_map:true,highlights:['武侠主题','互动游园']},
 {id:'qingming',name:'清明上河园',subtitle:'走进画卷，遇见大宋',category:'宋韵文化',theme:'song',internal_map:true,highlights:['宋韵游园','实景演艺','虹桥']},
 {id:'longting',name:'龙亭景区',subtitle:'漫步古都，寻一段文脉',category:'历史人文',theme:'palace',internal_map:false,highlights:['文化游园']},
 {id:'tieta',name:'铁塔景区',subtitle:'古塔与花木，慢游好时光',category:'古迹园林',theme:'tower',internal_map:false,highlights:['古迹','园林']},
 {id:'kaifengfu',name:'开封府',subtitle:'寻访汴梁，感受府衙文化',category:'历史人文',theme:'court',internal_map:false,highlights:['人文']},
]
