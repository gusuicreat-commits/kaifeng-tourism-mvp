// Execute the real component setup offline; only its storage and HTTP boundaries are replaced.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm')
const {createRequire}=require('node:module')
const frontend=path.resolve(__dirname,'../../frontend'),frontendRequire=createRequire(path.join(frontend,'package.json'))
const ts=frontendRequire('typescript'),vue=frontendRequire('vue'),{parse,compileScript}=frontendRequire('@vue/compiler-sfc')
const caseName=process.argv[2],chosen=caseName.startsWith('quick')?'quick':caseName.startsWith('deep')?'deep':null
const props=vue.reactive({tripId:'trip',autoPlan:caseName!=='manual'})
const modes=vue.ref({'other-trip':'deep'}),calls=[],mounted=[],cleanups=[]
if(chosen&&!caseName.endsWith('stored'))modes.value.trip=chosen
const constraints={business_date:'2030-05-01',starts_at:'2030-05-01T09:00:00+08:00',ends_at:'2030-05-01T20:00:00+08:00',origin_place_id:'origin',destination_place_id:'hotel',modes:['walking'],pace:'standard',objective:'balanced'}
if(caseName==='no-mode-party')constraints.party_size=2
if(caseName==='quick-missing')constraints.destination_place_id=null
const oldResult={status:'unknown',reply:'确认素材篮变更后接着安排',issues:[{code:'BASKET_CONFIRMATION_REQUIRED'}],options:[],rounds:0,planning:null}
const journey=vue.reactive({trip:{id:'trip',version:1,status:'DRAFT',draft:constraints},basketVersion:2,basketError:'',choices:[{kind:'place',id:'museum',status:'pending'}],catalog:[]})
const api={constraints:async()=>({version:3,rounds:0,constraints,result:oldResult}),preview:async(...args)=>{calls.push(args);return {...oldResult,reply:'new preview',issues:[]}}}
const mocks={
 vue:{...vue,onMounted:f=>mounted.push(f),onBeforeUnmount:f=>cleanups.push(f)},
 '../journey':{journey,clock:s=>s.slice(11,16),nameOf:id=>id,loadJourney:async()=>{},loadBasket:async()=>{},localDate:()=>constraints.business_date},
 '../explorationApi':{explorationApi:api},'../api':{ApiError:class extends Error{}},'../cityApi':{isMultiDayDraft:()=>false},'../voiceDraft':{memoryEpoch:vue.ref(0)},
 '../interactionMode':{interactionModes:modes,readMode:id=>modes.value[id]=caseName.endsWith('stored')?chosen:null,writeMode:(id,value)=>modes.value[id]=value},
}
const filename=path.join(frontend,'src/components/BasketPlanning.vue')
const {descriptor}=parse(fs.readFileSync(filename,'utf8'),{filename})
const compiled=compileScript(descriptor,{id:'offline-basket'})
const code=ts.transpileModule(compiled.content,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText
const moduleValue={exports:{}}
vm.runInNewContext('(function(require,module,exports){'+code+'\n})',{setTimeout,clearTimeout})(name=>{
 if(name in mocks)return mocks[name]
 if(name.endsWith('.vue')||name==='lucide-vue-next')return {}
 throw new Error('Unexpected dependency: '+name)
},moduleValue,moduleValue.exports)
const scope=vue.effectScope()
const setup=scope.run(()=>moduleValue.exports.default.setup(props,{expose(){},emit(){}}))
async function test(){
 await mounted[0]()
 assert.equal(setup.error.value,'')
 if(chosen&&caseName!=='quick-missing'){
  assert.equal(calls.length,1);assert.equal(setup.result.value.reply,'new preview')
  assert.equal(calls[0][3].party_size,undefined);assert.equal(calls[0][4],3)
 }else{
  assert.equal(calls.length,0)
  assert.equal(setup.activeStep.value,caseName==='no-mode-party'?'mode':caseName==='quick-missing'?'destination':'party')
  if(props.autoPlan)assert.equal(setup.result.value,null)
 }
 if(caseName==='no-mode-party'){
  setup.selectMode('deep');await vue.nextTick()
  assert.equal(setup.step.value,'preferences');assert.equal(calls.length,0)
 }
 if(caseName==='no-mode'){
  constraints.party_size=2
  await setup.resume()
  assert.equal(setup.activeStep.value,'mode');assert.equal(setup.result.value,null);assert.equal(calls.length,0)
 }
 if(caseName==='manual')assert.equal(setup.result.value.reply,oldResult.reply)
}
test().then(()=>console.log(caseName+' passed')).catch(error=>{console.error(error);process.exitCode=1}).finally(()=>{cleanups.forEach(f=>f());scope.stop()})
