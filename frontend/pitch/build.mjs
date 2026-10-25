import {existsSync} from 'node:fs'
import {fileURLToPath} from 'node:url'
import path from 'node:path'
import {spawnSync} from 'node:child_process'

const root=fileURLToPath(new URL('../../',import.meta.url))
const local=path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python')
const candidates=process.env.PYTHON_BIN?[[process.env.PYTHON_BIN]]:
 existsSync(local)?[[local]]:process.platform==='win32'?[['py','-3'],['python']]:[['python3']]
for(const [command,...args] of candidates){
 const result=spawnSync(command,[...args,'scripts/build_pitch.py'],{cwd:root,stdio:'inherit'})
 if(result.error?.code==='ENOENT')continue
 if(result.error)throw result.error
 process.exit(result.status??1)
}
throw new Error('Python 3.11+ is required to build the pitch; run scripts/setup.py first.')
