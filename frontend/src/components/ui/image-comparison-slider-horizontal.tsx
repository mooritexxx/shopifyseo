import * as React from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { cn } from "../../lib/utils";

export interface ImageComparisonSliderProps extends React.HTMLAttributes<HTMLDivElement> {
  leftImage: string;
  rightImage: string;
  altLeft?: string;
  altRight?: string;
  initialPosition?: number;
}

export const ImageComparisonSlider = React.forwardRef<HTMLDivElement, ImageComparisonSliderProps>(
  (
    {
      className,
      leftImage,
      rightImage,
      altLeft = "Left image",
      altRight = "Right image",
      initialPosition = 50,
      ...props
    },
    ref
  ) => {
    const [sliderPosition, setSliderPosition] = React.useState(initialPosition);
    const [isDragging, setIsDragging] = React.useState(false);
    const containerRef = React.useRef<HTMLDivElement>(null);

    const setContainerRef = React.useCallback(
      (el: HTMLDivElement | null) => {
        containerRef.current = el;
        if (typeof ref === "function") {
          ref(el);
        } else if (ref) {
          (ref as React.MutableRefObject<HTMLDivElement | null>).current = el;
        }
      },
      [ref]
    );

    const handleMove = React.useCallback((clientX: number) => {
      const el = containerRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      const x = clientX - rect.left;
      let newPosition = (x / rect.width) * 100;
      newPosition = Math.max(0, Math.min(100, newPosition));
      setSliderPosition(newPosition);
    }, []);

    const handleMouseMove = React.useCallback(
      (e: MouseEvent) => {
        if (!isDragging) return;
        handleMove(e.clientX);
      },
      [isDragging, handleMove]
    );

    const handleTouchMove = React.useCallback(
      (e: TouchEvent) => {
        if (!isDragging) return;
        e.preventDefault();
        handleMove(e.touches[0].clientX);
      },
      [isDragging, handleMove]
    );

    const handleInteractionEnd = React.useCallback(() => {
      setIsDragging(false);
    }, []);

    const handleInteractionStart = React.useCallback(() => {
      setIsDragging(true);
    }, []);

    React.useEffect(() => {
      if (!isDragging) return;
      document.addEventListener("mousemove", handleMouseMove);
      document.addEventListener("touchmove", handleTouchMove, { passive: false });
      document.addEventListener("mouseup", handleInteractionEnd);
      document.addEventListener("touchend", handleInteractionEnd);
      document.body.style.cursor = "ew-resize";
      return () => {
        document.removeEventListener("mousemove", handleMouseMove);
        document.removeEventListener("touchmove", handleTouchMove);
        document.removeEventListener("mouseup", handleInteractionEnd);
        document.removeEventListener("touchend", handleInteractionEnd);
        document.body.style.cursor = "";
      };
    }, [isDragging, handleMouseMove, handleTouchMove, handleInteractionEnd]);

    return (
      <div
        ref={setContainerRef}
        className={cn("group relative h-full w-full select-none overflow-hidden", className)}
        onMouseDown={handleInteractionStart}
        onTouchStart={handleInteractionStart}
        {...props}
      >
        <img
          src={rightImage}
          alt={altRight}
          className="pointer-events-none absolute inset-0 h-full w-full object-cover"
          draggable={false}
        />

        <div
          className="pointer-events-none absolute inset-0 h-full w-full overflow-hidden"
          style={{ clipPath: `polygon(0 0, ${sliderPosition}% 0, ${sliderPosition}% 100%, 0 100%)` }}
        >
          <img
            src={leftImage}
            alt={altLeft}
            className="h-full w-full object-cover"
            draggable={false}
          />
        </div>

        <div
          className="absolute top-0 h-full w-1 cursor-ew-resize"
          style={{ left: `calc(${sliderPosition}% - 2px)` }}
        >
          <div className="absolute inset-y-0 w-1 bg-background/50 backdrop-blur-sm" />

          <div
            className={cn(
              "absolute top-1/2 flex h-12 w-12 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full bg-background/50 text-foreground shadow-xl backdrop-blur-md",
              "transition-all duration-300 ease-in-out",
              "group-hover:scale-105",
              isDragging && "scale-105 shadow-2xl shadow-primary/50"
            )}
            role="slider"
            aria-valuenow={Math.round(sliderPosition)}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-orientation="horizontal"
            aria-label="Image comparison slider"
          >
            <div className="flex items-center text-primary">
              <ChevronLeft className="h-5 w-5 drop-shadow-md" />
              <ChevronRight className="h-5 w-5 drop-shadow-md" />
            </div>
          </div>
        </div>
      </div>
    );
  }
);

ImageComparisonSlider.displayName = "ImageComparisonSlider";
