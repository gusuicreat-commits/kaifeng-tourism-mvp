import {ref} from 'vue'
import {clearPendingMutations} from './api'
// Temporary reviewed transcript only; never audio, GPS, localStorage or an automatic command.
export const pendingVoiceText=ref('')
export const memoryEpoch=ref(0)
const channel=typeof BroadcastChannel!=='undefined'?new BroadcastChannel('bianyou-memory-deletion'):undefined
function reset(){pendingVoiceText.value='';clearPendingMutations();memoryEpoch.value++}
if(channel)channel.onmessage=e=>{if(e.data==='clear')reset()}
export function clearMemoryCaches(){reset();channel?.postMessage('clear')}
