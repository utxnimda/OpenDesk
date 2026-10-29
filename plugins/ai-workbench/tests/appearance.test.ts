import { afterEach, describe, expect, it, vi } from 'vitest';
import { APPEARANCE_ATTRIBUTES, applyAppearance, observeHostAppearance, parseAppearanceMessage,
  readAppearanceFragment, readHostAppearance, writeAppearanceFragment, type WorkbenchAppearance } from '../shared/appearance';

const styles = ['simple','codex','cursor','web','anime','glass','mint'] as const;
const themes = ['light','dark','sakura','ocean','forest','sand','graphite'] as const;
const solid = {material:'solid',transparency:100,motion:'off',transparentBackground:false} as const;
function element(theme='light', uiStyle='simple') {
  const props = new Map([['--glass-opacity','100%']]);
  return {dataset:{theme,uiStyle,material:'solid',motion:'off',shellBackground:'false'},
    style:{getPropertyValue:(key:string)=>props.get(key)??'',setProperty:(key:string,value:string)=>props.set(key,value)}};
}
afterEach(() => { vi.unstubAllGlobals(); vi.resetModules(); });

describe('host appearance relay', () => {
  it('round-trips every independent style and theme through first-load URL fragments', () => {
    for (const uiStyle of styles) for (const theme of themes) {
      const state:WorkbenchAppearance={theme,uiStyle,surfaceEffects:solid};
      const fragment=new URLSearchParams('session=local-test-session&bridgeNonce=bound');
      writeAppearanceFragment(fragment,state);
      expect(readAppearanceFragment(fragment)).toEqual(state);
      expect(fragment.get('session')).toBe('local-test-session');
    }
  });
  it('observes style-only changes and material changes even when the theme is unchanged', () => {
    let callback:()=>void=()=>{}; const observe=vi.fn(); const disconnect=vi.fn();
    vi.stubGlobal('MutationObserver',class { constructor(fn:()=>void){callback=fn;} observe=observe;disconnect=disconnect; });
    const root=element(); const seen:WorkbenchAppearance[]=[];
    const observer=observeHostAppearance(root as unknown as HTMLElement,()=>seen.push(readHostAppearance(root as unknown as HTMLElement)));
    expect(observe).toHaveBeenCalledWith(root,{attributes:true,attributeFilter:APPEARANCE_ATTRIBUTES});
    root.dataset.uiStyle='cursor'; callback();
    expect(seen.at(-1)?.uiStyle).toBe('cursor');expect(seen.at(-1)?.theme).toBe('light');
    root.dataset.uiStyle='glass';root.dataset.material='glass';root.dataset.shellBackground='true';root.style.setProperty('--glass-opacity','78%');callback();
    expect(seen.at(-1)?.surfaceEffects).toEqual({material:'glass',motion:'off',transparency:78,transparentBackground:true});
    // Leaving an unsaved preview restores the host's previous values through the same path.
    root.dataset.uiStyle='simple';root.dataset.material='solid';root.dataset.shellBackground='false';callback();
    expect(seen.at(-1)?.uiStyle).toBe('simple');expect(seen.at(-1)?.surfaceEffects.transparentBackground).toBe(false);
    observer.disconnect();expect(disconnect).toHaveBeenCalled();
  });
  it('rejects malformed updates while using safe first-load defaults for old hosts', () => {
    const valid={type:'appearance',appearanceVersion:1,theme:'graphite',uiStyle:'cursor',surfaceEffects:solid};
    expect(parseAppearanceMessage(valid)?.theme).toBe('graphite');
    for (const patch of [{uiStyle:'invented'}, {theme:'system'}, {appearanceVersion:99},
      {surfaceEffects:{...solid,transparency:NaN}}, {surfaceEffects:{...solid,transparency:101}},
      {surfaceEffects:{...solid,material:[]}}, {surfaceEffects:{...solid,transparentBackground:'true'}}]) {
      expect(parseAppearanceMessage({...valid,...patch})).toBeNull();
    }
    expect(readAppearanceFragment(new URLSearchParams('uiStyle=invalid&appearance=invalid')).uiStyle).toBe('simple');
    const root=element(); applyAppearance(root as unknown as HTMLElement,parseAppearanceMessage(valid)!);
    expect(root.dataset).toMatchObject({uiStyle:'cursor',theme:'graphite',material:'solid'});
  });
  it('accepts live appearance only from its bound wrapper and leaves runtime URL unchanged', async () => {
    const parent={postMessage:vi.fn()};let receive:(event:unknown)=>void=()=>{};
    const fragment='#session=local-test&hostOrigin=http%3A%2F%2F127.0.0.1%3A40123&bridgeNonce=bound&hostTools=0&appearance=ocean&uiStyle=web';
    const location={hash:fragment}; const root=element();
    vi.stubGlobal('window',{parent,addEventListener:(_name:string,fn:(event:unknown)=>void)=>{receive=fn;}});
    vi.stubGlobal('location',location);vi.stubGlobal('document',{documentElement:root});
    await import('../frontend/hostTools');
    expect(root.dataset).toMatchObject({uiStyle:'web',theme:'ocean'});
    const data={channel:'opendesk.ai.host-tools.v1',nonce:'bound',type:'appearance',appearanceVersion:1,
      theme:'dark',uiStyle:'glass',surfaceEffects:{material:'glass',transparency:78,motion:'off',transparentBackground:true}};
    receive({source:{},origin:'http://127.0.0.1:40123',data});
    receive({source:parent,origin:'http://127.0.0.1:9',data});
    receive({source:parent,origin:'http://127.0.0.1:40123',data:{...data,nonce:'stale'}});
    expect(root.dataset.uiStyle).toBe('web');
    receive({source:parent,origin:'http://127.0.0.1:40123',data});
    expect(root.dataset).toMatchObject({uiStyle:'glass',theme:'dark',material:'glass',shellBackground:'true'});
    expect(root.style.getPropertyValue('--glass-opacity')).toBe('78%');
    expect(location.hash).toBe(fragment);
    expect(parent.postMessage).not.toHaveBeenCalled();
  });
});
