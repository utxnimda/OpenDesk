import { describe, expect, it } from 'vitest';
import { parseToolOperation, toolIdentity } from '../../apps/framework/module-sdk/src/module-tools';
import { createPackageBroker } from '../../apps/framework/frontend/src/core/package-broker';
const tool = { name:'example__read',moduleId:'example',version:'1.0.0',digest:'a'.repeat(64),generation:'one' };
describe('explicit read-only module tool boundary', () => {
  it('accepts only bounded zero-argument operations and strips presentation fields', () => {
    expect(toolIdentity({...tool,title:'Title'})).toEqual(tool);
    expect(parseToolOperation('call',{tool,arguments:{}}).action).toBe('call');
    expect(()=>parseToolOperation('call',{tool,arguments:{command:'run'}})).toThrow();
    expect(()=>parseToolOperation('grant',{tools:[]})).toThrow();
    expect(()=>parseToolOperation('grant',{tools:Array(17).fill(tool)})).toThrow();
    expect(()=>toolIdentity({...tool,generation:''})).toThrow();
  });
  it('gates tool dispatch on handshake and discards stale generation', async () => {
    const calls: unknown[]=[]; const replies: unknown[]=[];
    const broker=createPackageBroker('one',async()=>null,async()=>{},undefined,async op=>{calls.push(op);return []});
    const request={type:'tools',generation:'one',id:'read',action:'list',params:{}};
    await broker.receive(request,v=>replies.push(v)); expect(calls).toHaveLength(0);
    await broker.receive({type:'ready',generation:'one'},()=>{});
    await broker.receive({...request,generation:'old'},()=>{}); expect(calls).toHaveLength(0);
    await broker.receive(request,()=>{}); expect(calls).toHaveLength(1);
    broker.dispose();await broker.receive({...request,id:'again'},()=>{});expect(calls).toHaveLength(1);
  });
});
