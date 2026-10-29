import { afterEach, describe, expect, it, vi } from 'vitest';
afterEach(() => { vi.unstubAllGlobals(); vi.resetModules(); });
describe('Web App host tool reply boundary', () => {
  it('ignores another frame, another origin and a wrong nonce before accepting the bound reply', async () => {
    const postMessage=vi.fn(); const parent={postMessage}; let receive: (event: unknown)=>void = ()=>{};
    vi.stubGlobal('window',{parent,addEventListener:(_name:string,listener:(event:unknown)=>void)=>{receive=listener;}});
    vi.stubGlobal('location',{hash:'#hostOrigin=http%3A%2F%2F127.0.0.1%3A40123&bridgeNonce=bound&hostTools=1'});
    vi.stubGlobal('document',{documentElement:{dataset:{},style:{setProperty:vi.fn()}}});
    const client=await import('../frontend/hostTools');
    let resolved=false; const pending=client.listTools().then(value=>{resolved=true;return value;});
    const sent=postMessage.mock.calls[0][0];
    const data={channel:sent.channel,nonce:'bound',id:sent.id,ok:true,result:[]};
    receive({source:{},origin:'http://127.0.0.1:40123',data});
    receive({source:parent,origin:'http://127.0.0.1:9',data});
    receive({source:parent,origin:'http://127.0.0.1:40123',data:{...data,nonce:'stale'}});
    await Promise.resolve();expect(resolved).toBe(false);
    receive({source:parent,origin:'http://127.0.0.1:40123',data});
    expect(await pending).toEqual([]);
    expect(postMessage.mock.calls[0][1]).toBe('http://127.0.0.1:40123');
  });
  it('does not expose a usable tools client on an old or standalone host', async () => {
    const postMessage=vi.fn();
    vi.stubGlobal('window',{parent:{postMessage},addEventListener:()=>{}});
    vi.stubGlobal('location',{hash:'#hostOrigin=http%3A%2F%2F127.0.0.1%3A40123&bridgeNonce=bound&hostTools=0'});
    vi.stubGlobal('document',{documentElement:{dataset:{},style:{setProperty:vi.fn()}}});
    const client=await import('../frontend/hostTools');
    expect(client.available()).toBe(false);await expect(client.listTools()).rejects.toThrow();
    expect(postMessage).not.toHaveBeenCalled();
  });
});
