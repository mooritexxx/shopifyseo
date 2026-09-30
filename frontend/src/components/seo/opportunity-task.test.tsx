import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { OpportunityTaskPanel, OpportunityTaskQueue } from './opportunity-task';
import { SeoQualityField } from './seo-quality-field';
const task = {id:1,object_type:'product',object_handle:'test',status:'draft_ready',evidence:{primary_query:'example query',suggested_action:'Improve title/meta for CTR',queries:[{query:'example query',impressions:100,clicks:1,position:2,ctr:.01}]},draft:{seo_title:'prepared title',seo_description:'prepared description'},reviewed:{},error:'',detail_url:'/products/test'};
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
function mount(ui:React.ReactNode) {const client=new QueryClient({defaultOptions:{queries:{retry:false},mutations:{retry:false}}});return render(<QueryClientProvider client={client}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>);}
it('loads a prepared draft only on request and reviews the edited values',async()=>{
  const fetcher=vi.fn().mockImplementation((_url:string, options?:RequestInit)=>Promise.resolve({ok:true,text:async()=>JSON.stringify({ok:true,data:options?.method==='POST'?{...task,status:'reviewed'}:[task]})}));
  vi.stubGlobal('fetch',fetcher);
  const load=vi.fn();
  mount(<OpportunityTaskPanel kind="product" handle="test" draft={{seo_title:'edited title',seo_description:'edited description'}} onLoad={load}/>);
  await screen.findByText('Opportunity fix · Draft ready');
  expect(load).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'Load prepared draft'}));
  expect(load).toHaveBeenCalledWith(task.draft);
  fireEvent.click(screen.getByRole('button',{name:'Mark editor draft reviewed'}));
  await waitFor(()=>expect(fetcher.mock.calls.some(([url, options])=>url.endsWith('/review') && JSON.parse(options.body).fields.seo_title==='edited title')).toBe(true));
});
it('shows retry errors and keeps task navigation available',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,text:async()=>JSON.stringify({ok:true,data:[{...task,status:'failed',error:'Provider unavailable'}]})}));
  mount(<><OpportunityTaskQueue/><OpportunityTaskPanel kind="product" handle="test" draft={{}} onLoad={vi.fn()}/></>);
  await screen.findByText('Provider unavailable');
  expect(screen.getByRole('button',{name:'Retry preparation'})).toBeTruthy();
  expect(screen.getAllByRole('link').some(a=>a.getAttribute('href')==='/products/test')).toBe(true);
});
it('uses server policy and distinguishes advisory targets from errors',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,text:async()=>JSON.stringify({ok:true,data:{product:{seo_title:{minimum:42,target:50,maximum:65},seo_description:{minimum:115,target:150,maximum:160}}}})}));
  const view=mount(<SeoQualityField kind="product" field="seo_description" value={'x'.repeat(140)}/>);
  await screen.findByText(/Below target; can still be saved/);
  expect(screen.getByText(/Recommended: 150–160/)).toBeTruthy();
  view.unmount();
  mount(<SeoQualityField kind="product" field="seo_description" value={'x'.repeat(100)}/>);
  await screen.findByText(/Outside required range/);
});
it('restores the edited reviewed snapshot when returning to a task',async()=>{
  const reviewed={seo_title:'reviewed edit',seo_description:'reviewed description'};
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,text:async()=>JSON.stringify({ok:true,data:[{...task,status:'reviewed',reviewed}]})}));
  const load=vi.fn();
  mount(<OpportunityTaskPanel kind="product" handle="test" draft={{}} onLoad={load}/>);
  fireEvent.click(await screen.findByRole('button',{name:'Load reviewed draft'}));
  expect(load).toHaveBeenCalledWith(reviewed);
});
