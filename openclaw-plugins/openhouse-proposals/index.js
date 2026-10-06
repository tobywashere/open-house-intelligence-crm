import { definePluginEntry } from 'openclaw/plugin-sdk/plugin-entry';
import { definition } from './proposals.js';
export default definePluginEntry(definition());
