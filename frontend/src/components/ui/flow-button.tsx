'use client';
import { ArrowRight, LoaderCircle } from 'lucide-react';

import { cn } from '../../lib/utils';

interface FlowButtonProps {
  text?: string;
  onClick?: () => void;
  disabled?: boolean;
  loading?: boolean;
}

export function FlowButton({ text = "Modern Button", onClick, disabled, loading }: FlowButtonProps) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={cn(
        'group relative flex items-center overflow-hidden rounded-[100px] border-[1.5px] border-white/40 bg-transparent py-3 text-sm font-semibold text-white cursor-pointer transition-all duration-[600ms] ease-[cubic-bezier(0.23,1,0.32,1)] hover:border-transparent hover:text-[#111111] hover:rounded-[12px] active:scale-[0.95] disabled:opacity-50 disabled:pointer-events-none',
        loading ? 'justify-center gap-2.5 px-8' : 'gap-1 px-8',
      )}
    >
      {loading ? (
        <>
          <LoaderCircle className="relative z-[9] h-4 w-4 shrink-0 stroke-white fill-none animate-spin" />
          <span className="relative z-[1]">{text}</span>
        </>
      ) : (
        <>
          <ArrowRight
            className="absolute w-4 h-4 left-[-25%] stroke-white fill-none z-[9] group-hover:left-4 group-hover:stroke-[#111111] transition-all duration-[800ms] ease-[cubic-bezier(0.34,1.56,0.64,1)]"
          />
          <span className="relative z-[1] -translate-x-3 group-hover:translate-x-3 transition-all duration-[800ms] ease-out">
            {text}
          </span>
          <span className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-4 h-4 bg-orange-500 rounded-[50%] opacity-0 group-hover:w-[220px] group-hover:h-[220px] group-hover:opacity-100 transition-all duration-[800ms] ease-[cubic-bezier(0.19,1,0.22,1)]"></span>
          <ArrowRight
            className="absolute w-4 h-4 right-4 stroke-white fill-none z-[9] group-hover:right-[-25%] group-hover:stroke-[#111111] transition-all duration-[800ms] ease-[cubic-bezier(0.34,1.56,0.64,1)]"
          />
        </>
      )}
    </button>
  );
}
