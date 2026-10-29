/** Parse decimal yuan without binary floating point rounding or empty-as-zero. */
export function yuanToFen(value:string):number|null {
 const text=value.trim();if(!text)return null
 if(!/^\d+(\.\d{1,2})?$/.test(text))throw new Error('金额请填写非负数字，最多两位小数')
 const [whole,fraction='']=text.split('.');const fen=Number(whole)*100+Number(fraction.padEnd(2,'0'))
 if(!Number.isSafeInteger(fen)||fen>100000000)throw new Error('金额超出可填写范围')
 return fen
}
export const yuan=(fen:number)=>'¥'+(fen/100).toFixed(2)
