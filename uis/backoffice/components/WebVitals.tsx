"use client";

import { useReportWebVitals } from "next/web-vitals";

import { track } from "../services/telemetry";

export function WebVitals() {
  useReportWebVitals((metric) => {
    track("web_vital_recorded", {
      route_template: window.location.pathname,
      metric_name: metric.name,
      value: metric.value,
      rating: metric.rating,
    });
  });

  return null;
}
