/** Independent API2 package. Copies only reviewed code and compiled UI, never local state. */
import { createHash, randomUUID } from 'node:crypto';
import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
const integration = dirname(fileURLToPath(import.meta.url));
const options = {};
for (let i=2;i<process.argv.length;i+=2) {
  const key=process.argv[i], value=process.argv[i+1];
  if (!['--opendesk','--project','--python','--output','--work'].includes(key) || !value || Object.hasOwn(options,key)) throw new Error('Invalid AI build arguments.');
  options[key]=resolve(value);
}
const host=options['--opendesk'], project=options['--project'];
if (!host || !project || !options['--python'] || !options['--output'] || !options['--work'] || process.platform!=='win32') throw new Error('Explicit Windows build roots required.');
const manifest=JSON.parse(readFileSync(join(integration,'manifest.json'),'utf8'));
const output=options['--output'];
if (existsSync(join(output,`${manifest.id}-${manifest.version}.ndpkg`))) throw new Error('Preserve the existing version; build a new version or a fresh reviewed staging directory.');
const work=join(options['--work'],randomUUID().slice(0,8));
const packageRoot=join(work,'package');
mkdirSync(packageRoot,{recursive:true});
const run=(command,args,extra={})=>{
  const result=spawnSync(command,args,{cwd:host,windowsHide:true,stdio:'inherit',env:{...process.env,...extra}});
  if(result.error || result.status!==0)throw result.error ?? new Error('AI build command failed: '+command);
};
function copyTree(source,target){
  mkdirSync(target,{recursive:true});
  for(const entry of readdirSync(source,{withFileTypes:true})){
    if(entry.name==='__pycache__')continue;
    if(entry.isSymbolicLink())throw new Error('Links cannot enter packages.');
    if(entry.isDirectory())copyTree(join(source,entry.name),join(target,entry.name));
    else if(entry.isFile())copyFileSync(join(source,entry.name),join(target,entry.name));
    else throw new Error('Unexpected package input.');
  }
}
const requireHost=createRequire(join(host,'package.json'));
const {build}=await import(pathToFileURL(requireHost.resolve('vite')).href);
const vue=(await import(pathToFileURL(requireHost.resolve('@vitejs/plugin-vue')).href)).default;
run(process.execPath,[join(host,'node_modules/vue-tsc/bin/vue-tsc.js'),'--noEmit','-p',join(project,'tsconfig.json')]);
const web=join(work,'web');
await build({configFile:false,root:join(project,'frontend'),base:'./',envDir:false,plugins:[vue()],
  build:{outDir:web,emptyOutDir:true,target:'es2022',sourcemap:false}});
await build({configFile:false,root:join(integration,'frontend'),base:'./',envDir:false,
  resolve:{alias:{'@opendesk/module-sdk':join(host,'apps/framework/module-sdk/src/index.ts')}},
  define:{__MODULE_ID__:JSON.stringify(manifest.id),__MODULE_VERSION__:JSON.stringify(manifest.version)},
  build:{outDir:join(packageRoot,'frontend'),emptyOutDir:true,target:'es2022',sourcemap:false}});
const pythonSource=join(work,'python');mkdirSync(pythonSource,{recursive:true});
copyFileSync(join(integration,'worker_entry.py'),join(pythonSource,'worker_entry.py'));
writeFileSync(join(pythonSource,'_build_identity.py'),`IDENTITY = ${JSON.stringify({id:manifest.id,version:manifest.version,hostApi:2})}\n`);
run(options['--python'],['-X','utf8','-m','PyInstaller','--noconfirm','--clean','--onedir','--contents-directory','_internal',
  '--name','module-worker','--distpath',join(work,'frozen'),'--workpath',join(work,'pyinstaller'),'--specpath',work,
  '--paths',join(project,'backend'),'--paths',join(host,'apps/framework/worker'),'--add-data',`${web};web`,join(pythonSource,'worker_entry.py')],
  {PYINSTALLER_CONFIG_DIR:join(work,'cache'),SOURCE_DATE_EPOCH:'1577836800',PYTHONHASHSEED:'0'});
const frozen=join(work,'frozen/module-worker');
run(options['--python'],['-X','utf8',join(project,'tests/frozen_smoke.py'),join(frozen,'module-worker.exe'),manifest.version]);
copyTree(frozen,join(packageRoot,'worker'));
const {readGitIdentity}=await import(pathToFileURL(join(host,'scripts/build-version.mjs')).href);
const {validateManifest,sanitizeRepository}=await import(pathToFileURL(join(host,'scripts/module-pack.mjs')).href);
const {writeImmutableFiles}=await import(pathToFileURL(join(host,'scripts/distribution.mjs')).href);
const origin=spawnSync('git',['remote','get-url','origin'],{cwd:host,windowsHide:true,encoding:'utf8',timeout:5000});
const identity=readGitIdentity(host);
manifest.source={commit:identity.commit,dirty:identity.dirty,repository:origin.status===0?sanitizeRepository(origin.stdout.trim()):null};
validateManifest(manifest);
writeFileSync(join(packageRoot,'manifest.json'),JSON.stringify(manifest,null,2)+'\n');
const artifact=join(work,'artifact.ndpkg');
run(options['--python'],['-X','utf8',join(host,'scripts/module-pack-archive.py'),packageRoot,artifact]);
const bytes=readFileSync(artifact);if(bytes.length>128*1024*1024)throw new Error('AI package too large.');
const sha256=createHash('sha256').update(bytes).digest('hex');
writeImmutableFiles(output,{[`${manifest.id}-${manifest.version}.ndpkg`]:bytes,'manifest.json':JSON.stringify(manifest,null,2)+'\n',SHA256SUMS:`${sha256}  ${manifest.id}-${manifest.version}.ndpkg\n`});
console.log(JSON.stringify({file:join(output,`${manifest.id}-${manifest.version}.ndpkg`),sha256,work},null,2));
