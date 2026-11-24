// The Ask tab for the Chiron UI, from chiron-mcp's askpatch. Save it as src/ask.tsx and wrap
// the settings in src/overrideConfig.tsx with withAsk(...). Built with VITE_ASK_URL=/ask the
// UI has an Ask tab; built without it, the UI is exactly as before.
import type { ReactNode } from "react";
import {
  Home as HomeIcon,
  Search as SearchIcon,
  TableChart as TableChartIcon,
  FileCopy as FileCopyIcon,
  Addchart as AddChartIcon,
  QuestionAnswer as AskIcon,
} from "@mui/icons-material";
import { useParams } from "react-router-dom";

const ASK_URL = (import.meta.env.VITE_ASK_URL || "").replace(/\/$/, "");

// The Ask page for the dataset being viewed, inside Chiron's page
function AskRoute() {
  const { dataset_id } = useParams();
  return (
    <iframe
      title="Ask"
      src={`${ASK_URL}/?dataset=${encodeURIComponent(dataset_id ?? "")}`}
      style={{ width: "100%", height: "calc(100vh - 112px)", minHeight: 480, border: 0, display: "block" }}
    />
  );
}

type Tab = { label: string; link: string; icon: ReactNode };
type Settings = { routes?: { datasetMore?: unknown[] }; header?: { nav?: Tab[] } };

// Chiron's own tabs: the UI replaces a list of tabs whole, so adding one lists the others
const CHIRON_TABS: Tab[] = [
  { label: "Dataset Home", link: "/dataset", icon: <HomeIcon /> },
  { label: "Query", link: "/query", icon: <SearchIcon /> },
  { label: "Results", link: "/results", icon: <TableChartIcon /> },
  { label: "Reports", link: "/reports", icon: <FileCopyIcon /> },
  { label: "Aggregate", link: "/aggregate", icon: <AddChartIcon /> },
];

export function withAsk<T extends object>(settings: T): T {
  if (!ASK_URL) return settings;
  const { routes = {}, header = {} } = settings as Settings;
  return {
    ...settings,
    routes: { ...routes, datasetMore: [...(routes.datasetMore ?? []), { path: "ask", element: <AskRoute /> }] },
    header: { ...header, nav: [...(header.nav ?? CHIRON_TABS), { label: "Ask", link: "/ask", icon: <AskIcon /> }] },
  } as T;
}
