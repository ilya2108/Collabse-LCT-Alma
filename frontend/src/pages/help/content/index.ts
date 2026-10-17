import { adminGuide } from './adminGuide.ts';
import type { HelpGuide } from './types.ts';
import { userGuide } from './userGuide.ts';

export { adminGuide, userGuide };
export * from './types.ts';

export const HELP_GUIDES: Record<HelpGuide['id'], HelpGuide> = {
  user: userGuide,
  admin: adminGuide,
};
