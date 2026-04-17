import type { ToastVariant } from "../components/ui/toast";

export function detectToastVariant(message: string): ToastVariant {
  const lowerMessage = message.toLowerCase();
  
  if (lowerMessage.includes("error") || lowerMessage.includes("failed") || lowerMessage.includes("fail")) {
    return "error";
  }
  if (lowerMessage.includes("success") || lowerMessage.includes("saved") || lowerMessage.includes("complete") || lowerMessage.includes("applied")) {
    return "success";
  }
  if (lowerMessage.includes("warning") || lowerMessage.includes("warn")) {
    return "warning";
  }
  if (lowerMessage.includes("info") || lowerMessage.includes("note")) {
    return "info";
  }
  
  // Default to success for positive-sounding messages
  if (lowerMessage.includes("regenerated") || lowerMessage.includes("generated") || lowerMessage.includes("refreshed")) {
    return "success";
  }
  
  return "default";
}
