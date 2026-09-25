import "server-only";

import { handleMaxIdentity } from "../infrastructure/identity-handler";
import { maxAccountLinking } from "./account-linking";
import { issueMaxLearnerSession } from "./learner-session";
import { canViewMaxManagerReport } from "./manager-report";

export function verifyMaxLaunch(request: Request) {
  return handleMaxIdentity(request, process.env.MAX_BOT_TOKEN, {
    ...maxAccountLinking,
    issueSession: issueMaxLearnerSession,
    canViewReports: canViewMaxManagerReport,
  });
}
