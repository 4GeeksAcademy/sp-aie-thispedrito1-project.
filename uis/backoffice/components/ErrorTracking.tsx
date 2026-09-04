"use client";

import { useEffect } from "react";

import { track } from "../services/telemetry";

const MAX_MESSAGE_LENGTH = 200;

function truncate(message: string): string {
  return message.length > MAX_MESSAGE_LENGTH ? `${message.slice(0, MAX_MESSAGE_LENGTH)}…` : message;
}

// Catches errors outside React's render tree (event handlers, timers,
// promise rejections) — render-tree errors are covered separately by
// app/error.tsx, which is the only place a React Error Boundary can live.
export function ErrorTracking() {
  useEffect(() => {
    const handleError = (event: ErrorEvent) => {
      track("frontend_error_captured", {
        component_name: "window",
        route_template: window.location.pathname,
        error_name: event.error?.name ?? "Error",
        error_message: truncate(event.message),
      });
    };

    const handleRejection = (event: PromiseRejectionEvent) => {
      const reason = event.reason;
      const message = reason instanceof Error ? reason.message : String(reason);
      track("frontend_error_captured", {
        component_name: "window",
        route_template: window.location.pathname,
        error_name: reason instanceof Error ? reason.name : "UnhandledRejection",
        error_message: truncate(message),
      });
    };

    window.addEventListener("error", handleError);
    window.addEventListener("unhandledrejection", handleRejection);
    return () => {
      window.removeEventListener("error", handleError);
      window.removeEventListener("unhandledrejection", handleRejection);
    };
  }, []);

  return null;
}
