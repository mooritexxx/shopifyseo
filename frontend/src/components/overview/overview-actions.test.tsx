import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "../../test/test-utils";
import { getJson } from "../../lib/api";
import { OverviewActions, rankMovement } from "./overview-actions";
import { type RankedKeyword, type RankCheck } from "../../hooks/use-rankings";
vi.mock("../../lib/api",()=>({getJson:vi.fn()}));
const check = (id:number,position:number|null,extra:Partial<RankCheck>={}):RankCheck => ({id,position,checked_at:"2026-09-30",status:"ok",cancelled:0,coverage_complete:1,checked_depth:50,pages_checked:5,source:"serpapi",profile:"test",searches_used:1,error:null,reported_position:null,ranking_url:null,...extra});
const keyword = (latest:RankCheck,previous:RankCheck):RankedKeyword => ({id:1,term:"Example keyword",latest,trend:[previous,latest],target_url:null,grp:null,change:null,movement:null,target_mismatch:false,top_competitor:null});
const empty = (path:string) => path.includes('/opportunities?')?{items:[]}:path==='/api/rankings'?{items:[]}:path==='/api/opportunities/tasks'?[]:path==='/api/keywords/clusters'?{clusters:[]}:path==='/api/article-ideas'?{items:[]}:{items:[],window_days:14,reporting_lag_days:3};
beforeEach(()=>{vi.clearAllMocks(); vi.mocked(getJson).mockImplementation(async path=>empty(path));});
describe('Overview action dashboard',()=>{
  it('shows all five sections with honest empty states',async()=>{
    renderWithProviders(<OverviewActions/>);
    expect(await screen.findByText(/No applied opportunity fixes yet/)).toBeVisible();
    for(const name of ['Top SEO opportunities','Ranking gains and losses','SEO work queue','Content coverage gaps','Results after SEO changes']) expect(screen.getByRole('heading',{name})).toBeVisible();
    expect(screen.getByRole('link',{name:'All clusters →'})).toHaveAttribute('href','/keywords?tab=clusters');
  });
  it('only uses comparable verified rank checks and distinguishes top ten crossings',()=>{
    expect(rankMovement(keyword(check(2,9),check(1,12)))?.label).toBe('Entered top 10');
    expect(rankMovement(keyword(check(2,null),check(1,9)))?.label).toBe('Left top 10');
    expect(rankMovement(keyword(check(2,null,{coverage_complete:0}),check(1,9)))).toBeNull();
    expect(rankMovement(keyword(check(2,9),check(1,12,{status:'unverified'})))).toBeNull();
    expect(rankMovement(keyword(check(2,9,{cancelled:1}),check(1,12)))).toBeNull();
  });
  it('deduplicates opportunity pages and links gaps, tasks and related ideas',async()=>{
    vi.mocked(getJson).mockImplementation(async path=>{
      if(path.includes('/opportunities?')) return {items:[{id:'1',query:'Best query',object_type:'product',object_handle:'example',impressions:100,ctr:.03,position:9,opportunity_score:80,suggested_action:'Improve title'},{id:'2',query:'Duplicate page',object_type:'product',object_handle:'example',impressions:90,ctr:.02,position:10,opportunity_score:70,suggested_action:'Improve title'}]};
      if(path==='/api/opportunities/tasks') return [{id:3,object_handle:'example',status:'draft_ready',evidence:{primary_query:'Review my draft'},detail_url:'/products/example'}];
      if(path==='/api/keywords/clusters') return {clusters:[{id:4,name:'Uncovered topic',suggested_match:null,cannibalization_risk:'high',priority_score:70,total_volume:1000,keyword_count:5}]};
      if(path==='/api/article-ideas') return {items:[{id:5,linked_cluster_id:4,suggested_title:'Related idea',status:'idea'}]};
      return empty(path);
    });
    renderWithProviders(<OverviewActions/>);
    expect(await screen.findByRole('link',{name:'Best query'})).toHaveAttribute('href','/products/example');
    expect(screen.queryByText('Duplicate page')).not.toBeInTheDocument();
    expect(screen.getByRole('link',{name:'Review my draft'})).toHaveAttribute('href','/products/example');
    expect(screen.getByRole('link',{name:'Related idea · idea'})).toHaveAttribute('href','/article-ideas/5');
  });
  it('does not show comparison metrics for insufficient history',async()=>{
    vi.mocked(getJson).mockImplementation(async path=>path==='/api/overview/change-results'?{items:[{id:1,object_handle:'example',detail_url:'/products/example',state:'insufficient',applied_at:'2026-09-01',before:{start:'2026-08-18',end:'2026-08-31',days:2,clicks:10,impressions:100,ctr:.1},after:{start:'2026-09-02',end:'2026-09-15',days:14,clicks:20,impressions:200,ctr:.1}}]}:empty(path));
    renderWithProviders(<OverviewActions/>);
    expect(await screen.findByText('Insufficient history')).toBeVisible();
    expect(screen.queryByText('10 → 20')).not.toBeInTheDocument();
    expect(screen.getByText(/Stored days: 2\/14 before/)).toBeVisible();
  });
  it('keeps results usable when another panel fails',async()=>{
    vi.mocked(getJson).mockImplementation(async path=>{
      if(path.includes('/opportunities?')) throw new Error('Offline');
      if(path==='/api/overview/change-results') return {items:[{id:1,object_handle:'example',detail_url:'/products/example',state:'ready',applied_at:'2026-09-01 12:00:00',applied_date:'2026-09-01',before:{start:'2026-08-18',end:'2026-08-31',days:14,clicks:10,impressions:100,ctr:.1},after:{start:'2026-09-02',end:'2026-09-15',days:14,clicks:20,impressions:200,ctr:.1}}]};
      return empty(path);
    });
    renderWithProviders(<OverviewActions/>);
    expect(await screen.findByText('Comparison available')).toBeVisible();
    expect(screen.getByText('10 → 20')).toBeVisible();
    expect(screen.getByRole('button',{name:'Retry top seo opportunities'})).toBeVisible();
  });

});
