"use client";

import { cn } from "@/lib/utils";

interface StepperProps {
  current: 1 | 2 | 3 | 4;
  labels: [string, string, string, string];
}

export function Stepper({ current, labels }: StepperProps) {
  return (
    <ol className="flex items-center w-full mb-8">
      {labels.map((label, i) => {
        const step = (i + 1) as 1 | 2 | 3 | 4;
        const isActive = step === current;
        const isDone = step < current;
        const isLast = i === labels.length - 1;
        return (
          <li
            key={label}
            className={cn(
              "flex items-center",
              !isLast && "flex-1",
            )}
          >
            <div className="flex flex-col items-center shrink-0">
              <div
                className={cn(
                  "w-8 h-8 rounded-full flex items-center justify-center font-semibold text-sm",
                  isActive && "bg-blue-500 text-white",
                  isDone && "bg-green-500 text-white",
                  !isActive && !isDone && "bg-gray-200 text-gray-600",
                )}
              >
                {isDone ? "✓" : step}
              </div>
              <span className="text-xs mt-1 text-center whitespace-nowrap">{label}</span>
            </div>
            {!isLast && (
              <div
                className={cn(
                  "flex-1 h-1 mx-2 rounded",
                  isDone ? "bg-green-500" : "bg-gray-200",
                )}
              />
            )}
          </li>
        );
      })}
    </ol>
  );
}
