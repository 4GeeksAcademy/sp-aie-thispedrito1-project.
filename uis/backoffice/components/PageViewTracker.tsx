"use client";

import { usePathname } from "next/navigation";
import { useEffect, useRef } from "react";

import { track } from "../services/telemetry";

export function PageViewTracker() {
  const pathname = usePathname();
  const previousPathname = useRef<string | null>(null);

  useEffect(() => {
    track("page_viewed", {
      route_template: pathname,
      referrer_route_template: previousPathname.current,
    });
    previousPathname.current = pathname;
  }, [pathname]);

  return null;
}
