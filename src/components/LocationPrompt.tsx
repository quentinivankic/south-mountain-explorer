import { MapPin, X } from "lucide-react";
import { useFirstLaunchLocationPrompt } from "@/lib/location";

export function LocationPrompt() {
  const { shouldShow, grant, dismiss } = useFirstLaunchLocationPrompt();
  if (!shouldShow) return null;
  return (
    <div className="fixed inset-0 z-[2000] flex items-end sm:items-center justify-center bg-black/50 backdrop-blur-sm p-4 animate-in fade-in">
      <div className="w-full max-w-sm rounded-3xl bg-card border border-border shadow-2xl p-6 animate-in slide-in-from-bottom-4">
        <div className="flex items-start justify-between gap-3 mb-3">
          <div
            className="size-12 rounded-2xl flex items-center justify-center text-primary-foreground"
            style={{ background: "var(--gradient-sunrise)" }}
          >
            <MapPin className="size-6" />
          </div>
          <button
            onClick={dismiss}
            aria-label="Skip"
            className="p-1.5 -m-1.5 rounded-full text-muted-foreground hover:bg-muted"
          >
            <X className="size-4" />
          </button>
        </div>
        <h2 className="text-xl font-bold leading-tight">
          Show trails near you?
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">
          We use your location only on this device to surface nearby hiking
          areas. Your coordinates never leave your phone.
        </p>
        <div className="mt-5 flex flex-col gap-2">
          <button
            onClick={grant}
            className="w-full rounded-2xl py-3 font-bold text-primary-foreground active:scale-[0.99] transition-transform"
            style={{ background: "var(--gradient-sunrise)" }}
          >
            Use my location
          </button>
          <button
            onClick={dismiss}
            className="w-full rounded-2xl py-3 text-sm font-semibold text-muted-foreground hover:text-foreground"
          >
            Not now
          </button>
        </div>
      </div>
    </div>
  );
}
