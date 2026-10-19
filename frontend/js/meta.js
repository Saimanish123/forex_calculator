import { createContext } from "preact";
import { useContext } from "preact/hooks";

// Platforms, banks and settings, loaded once at startup (`reload()` after changing them).
export const MetaContext = createContext(null);
export const useMeta = () => useContext(MetaContext);

export const platformName = (meta, id) => meta.platforms.find((p) => p.id === id)?.name ?? id;
export const bankName = (meta, id) => meta.banks.find((b) => b.id === id)?.name ?? id;
