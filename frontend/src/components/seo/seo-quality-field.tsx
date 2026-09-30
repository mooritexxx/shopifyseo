import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { getJson } from "../../lib/api";
import { CharacterBar } from "../ui/character-bar";
const rule = z.object({minimum:z.number(),target:z.number(),maximum:z.number()});
const policySchema = z.record(z.object({seo_title:rule,seo_description:rule}));
export function SeoQualityField({kind,field,value}:{kind:string;field:'seo_title'|'seo_description';value:string}) {
  const policy = useQuery({queryKey:['seo-quality-policy'],queryFn:()=>getJson('/api/seo-quality-policy',policySchema),staleTime:60_000});
  const limits = policy.data?.[kind]?.[field];
  if (!limits) return <p className="text-xs text-slate-500">{policy.error?'Quality guidance unavailable. Saving still checks the server rules.':'Loading quality guidance…'}</p>;
  const n = Array.from(value.trim()).length;
  const invalid = n>0 && (n<limits.minimum || n>limits.maximum);
  return <div><CharacterBar current={n} max={limits.maximum} goodMin={limits.target}/><p className={`mt-1 text-xs ${invalid?'text-red-700':n>0 && n<limits.target?'text-amber-700':'text-slate-500'}`}>
    {n}/{limits.maximum} characters. {invalid?`Outside required range (${limits.minimum}–${limits.maximum}). `:''}Recommended: {limits.target}–{limits.maximum} characters.{n>0 && !invalid && n<limits.target?' Below target; can still be saved.':''}
  </p></div>;
}
