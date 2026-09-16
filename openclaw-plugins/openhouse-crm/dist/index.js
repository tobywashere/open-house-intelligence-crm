import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

import { createPluginDefinition } from "./definition.js";


export default definePluginEntry(createPluginDefinition());
