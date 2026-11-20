import { createContext } from "react";
import type { Mode } from "./types";

// Provided by App once `/api/mode` has loaded; nothing that PATCHes renders before that.
export const ModeContext = createContext<Mode>("full");
